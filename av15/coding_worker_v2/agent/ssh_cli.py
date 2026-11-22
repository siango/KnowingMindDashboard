"""KMCW SSH conversation and recovery entry; LLM text grants no tool authority."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from ..providers import redact
from .native_cloudflare import native_client, private_file
from .tools import ToolError

SYSTEM = ('You are the KMCW engineering assistant for KEBUNATE. Reply in the user language. '
          'Separate evidence, assumptions and proposed actions. You cannot execute tools in chat. '
          'Never claim a command ran, a database changed, deployment succeeded or independent QC passed. '
          'Commands must be run explicitly through the existing km/KMCP approval and audit pipeline. '
          'Do not ask for passwords, cookies, private keys or tokens in chat.')
IDENT = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$')


def save_private(path, value):
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix='.kmcw-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp): os.unlink(tmp)


def converse(config, session_id, text, request_id, *, client=None):
    if (not isinstance(config, dict) or set(config) != {'schema', 'provider', 'model', 'sessions'}
            or config.get('schema') != 'KMCW_SSH_V1'
            or not isinstance(config['model'], str)
            or not isinstance(config['sessions'], str)):
        raise ToolError('KMCW_SSH_CONFIG_INVALID')
    if not all(isinstance(x, str) and IDENT.fullmatch(x) for x in (session_id, request_id)):
        raise ToolError('KMCW_SESSION_ID_INVALID')
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ToolError('KMCW_CHAT_INPUT_LIMIT')
    # Redact known credential forms before provider transmission and persistence.
    text = redact(text)
    root = Path(config['sessions']).expanduser()
    if root.is_symlink(): raise ToolError('KMCW_SESSION_PATH_INVALID')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    meta = root.stat()
    if meta.st_uid != os.getuid() or meta.st_mode & 0o077:
        raise ToolError('KMCW_SESSION_PERMISSIONS_REQUIRED')
    path = root / (session_id + '.json')
    fd = os.open(root / (session_id + '.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'a+') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        identity = {'provider': config['provider']['account_id'], 'model': config['model']}
        data = private_file(path, 1_000_000) if path.exists() or path.is_symlink() else {
            'schema': 'KMCW_SSH_SESSION_V1', 'identity': identity, 'messages': [], 'requests': {}}
        if data.get('identity') != identity:
            raise ToolError('KMCW_SESSION_IDENTITY_CONFLICT')
        key = hashlib.sha256(text.encode()).hexdigest()
        requests = data['requests']
        if request_id in requests:
            old = requests[request_id]
            if old['input_sha256'] != key:
                raise ToolError('KMCW_REQUEST_CONFLICT')
            if old['state'] != 'COMPLETED':
                raise ToolError('KMCW_AMBIGUOUS_REQUEST_NO_REPLAY')
            return {**old['result'], 'reused': True}
        if len(requests) >= 128:
            raise ToolError('KMCW_SESSION_LIMIT_NEW_SESSION_REQUIRED')
        requests[request_id] = {'state': 'RESERVED', 'input_sha256': key}
        save_private(path, data)
        history = data['messages'][-6:]
        client = client or native_client(config['provider'])
        answer = client.converse(config['model'], [{'role': 'system', 'content': SYSTEM},
            *history, {'role': 'user', 'content': text}], max_tokens=256)
        reply = redact(answer['response'])
        result = {'schema': 'KMCW_SSH_CHAT_V1', 'session_id': session_id,
            'request_id': request_id, 'reply': reply, 'model': config['model'],
            'execution': 'NOT_REQUESTED', 'independent_qc': 'NOT_RUN',
            'billed_cost': answer['billed_cost'], 'reused': False}
        data['messages'] = [*history, {'role': 'user', 'content': text},
                            {'role': 'assistant', 'content': reply}]
        requests[request_id].update(state='COMPLETED', result=result)
        save_private(path, data)
        return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    for name in ('chat', 'ask'):
        p = sub.add_parser(name)
        p.add_argument('--config', required=True)
        p.add_argument('--session', default='ssh-main')
        if name == 'ask':
            p.add_argument('--text', required=True)
            p.add_argument('--request-id', required=True)
    p = sub.add_parser('backup')
    p.add_argument('--source', required=True); p.add_argument('--runtime', required=True)
    p.add_argument('--catalog'); p.add_argument('--pg-service')
    p.add_argument('--output', required=True)
    p = sub.add_parser('restore')
    p.add_argument('--archive', required=True); p.add_argument('--destination', required=True)
    p = sub.add_parser('circuit')
    p.add_argument('--config', required=True); p.add_argument('--graph', required=True)
    p.add_argument('--command', required=True); p.add_argument('--request-id', required=True)
    p.add_argument('--approve', action='store_true'); p.add_argument('--max-steps', type=int, default=8)
    p = sub.add_parser('graph')
    p.add_argument('--config', required=True); p.add_argument('--circuit-id')
    p.add_argument('--output')
    p = sub.add_parser('worker')
    p.add_argument('--config')
    p.add_argument('arguments', nargs=argparse.REMAINDER)
    sub.add_parser('mcp').add_argument('arguments', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.action in {'chat', 'ask'}:
            config = private_file(args.config)
            if args.action == 'ask':
                print(json.dumps(converse(config, args.session, args.text, args.request_id), ensure_ascii=False))
            else:
                print('KMCW SSH — /exit เพื่อออก; ข้อความเป็นการปรึกษา ยังไม่สั่งรันงาน')
                while True:
                    try: text = input('คุณ> ')
                    except EOFError: break
                    if text.strip() == '/exit': break
                    if not text.strip(): continue
                    result = converse(config, args.session, text, uuid.uuid4().hex)
                    print('KMCW> ' + result['reply'])
        elif args.action == 'graph':
            from .owner_cli import load_owner_runtime
            from .kves import projection
            ctx, registry = load_owner_runtime(args.config)
            result = projection(ctx, registry, circuit_id=args.circuit_id)
            if args.output:
                target = Path(args.output)
                if target.exists() or target.is_symlink(): raise ToolError('KVES_NEW_OUTPUT_REQUIRED')
                save_private(target, result)
                print(json.dumps({'output': str(target), 'nodes': len(result['nodes']),
                                  'execution_authority': False}))
            else: print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == 'circuit':
            from .owner_cli import execute
            graph = Path(args.graph)
            if graph.stat().st_size > 262144: raise ToolError('CIRCUIT_PAYLOAD_LIMIT')
            result = execute(args.config, json.loads(graph.read_text('utf-8')), args.command,
                             args.request_id, approved=args.approve, max_steps=args.max_steps)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            if args.approve and result['result'].get('status') != 'COMPLETED': return 1
        elif args.action in {'backup', 'restore'}:
            from .runtime_backup import create_backup, restore_backup
            result = (create_backup(args.source, args.runtime, args.output,
                       catalog=args.catalog, pg_service=args.pg_service)
                      if args.action == 'backup' else restore_backup(args.archive, args.destination))
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif args.action == 'worker':
            return worker_main(args.arguments, config=args.config)
        else:
            from .circuit_cli import main as mcp
            return mcp(args.arguments)
        return 0
    except (ToolError, OSError, ValueError, KeyError) as exc:
        print(json.dumps({'ok': False, 'reason': exc.reason if isinstance(exc, ToolError)
                          else type(exc).__name__}))
        return 1


def worker_main(argv=None, *, config=None):
    import sys
    from .km_main import main as worker
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ['--']: argv = argv[1:]
    if any(x in argv for x in ('--help', '-h')):
        return worker(argv)
    if '--base' in argv and '--workspace' in argv:
        return worker(argv)
    config = config or os.environ.get('KMCW_RUNTIME_CONFIG')
    if not config:
        print(json.dumps({'ok': False, 'reason': 'KMCW_EXPLICIT_RUNTIME_REQUIRED',
            'next': 'km --base EXISTING_RUNTIME --workspace EXISTING_REPO status'}))
        return 1
    from .circuit_cli import load_runtime
    try:
        ctx, _ = load_runtime(config)
        return worker(['--base', str(ctx['state_dir'].parent),
                       '--workspace', ctx['workspace'], *argv], runtime_context=ctx)
    except (ToolError, OSError, ValueError) as exc:
        print(json.dumps({'ok': False, 'reason': exc.reason if isinstance(exc, ToolError)
                          else type(exc).__name__}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
