#!/usr/bin/env python3
"""Read existing native acceptance state. Never execute/retry a circuit."""
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path


def code(value):
    return value if isinstance(value,str) and re.fullmatch(r'[A-Z][A-Z0-9_:-]{0,119}',value) else None



def storage_summary(home, budget_seconds=90, max_files=500000):
    """Bounded metadata inventory; no names/content from private files leave host."""
    import time
    roots={'pip_cache':home/'.cache/pip',
           'delivery_cache':home/'.cache/knowingmind/local-services',
           'installed_local_apps':home/'.local/share/knowingmind',
           'runner_work':home/'KnowingMind/runners/github/_work',
           'ollama_models':home/'.ollama',
           'apt_archives':Path('/var/cache/apt/archives'),
           'apt_lists':Path('/var/lib/apt/lists')}
    deadline=time.monotonic()+budget_seconds
    result={};count=0
    for label,root in roots.items():
        row={'present':root.exists(),'complete':False,'regular_files':0,
             'logical_bytes':0,'allocated_bytes':0,'metadata_errors':0,
             'symlinks_skipped':0,'cross_device_observed':0}
        result[label]=row
        if root.is_symlink():
            row['status']='SYMLINK_ROOT_DENIED';continue
        if not row['present']:
            row.update(complete=True,status='ABSENT');continue
        device=root.stat().st_dev;stack=[root];seen=set()
        while stack and time.monotonic()<deadline and count<max_files:
            current=stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if time.monotonic()>=deadline or count>=max_files:
                            stack.append(current);break
                        try:
                            if entry.is_symlink():
                                row['symlinks_skipped']+=1;continue
                            stat=entry.stat(follow_symlinks=False)
                            if stat.st_dev!=device:
                                row['cross_device_observed']+=1
                            # Metadata-only inventory remains within explicitly
                            # scoped roots. Device mismatch does not mean zero usage.
                            if entry.is_dir(follow_symlinks=False):stack.append(Path(entry.path))
                            elif entry.is_file(follow_symlinks=False):
                                count+=1;identity=(stat.st_dev,stat.st_ino)
                                if identity in seen:continue
                                seen.add(identity)
                                row['regular_files']+=1
                                row['logical_bytes']+=stat.st_size
                                row['allocated_bytes']+=stat.st_blocks*512
                        except OSError:row['metadata_errors']+=1
            except OSError:row['metadata_errors']+=1
        row['complete']=not stack and row['metadata_errors']==0
        row['status']=('METADATA_READ' if row['complete'] else
                       'BUDGET_LIMIT_PARTIAL' if stack else 'METADATA_PARTIAL')
    return result


def pg_dump_diagnostic(service, timeout=45):
    """Exact read-only dump preflight, with no database bytes or secrets retained."""
    if not isinstance(service, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', service):
        return {'status':'INVALID_SERVICE_ALIAS'}
    result={'status':'NOT_RUN','read_only':True,'database_bytes_saved':False}
    for binary in ('pg_dump','psql'):
        try:
            p=subprocess.run([binary,'--version'],capture_output=True,text=True,timeout=5)
            match=re.search(r'(\d+)\.(\d+)(?:\.(\d+))?',p.stdout)
            result[binary+'_version']=match.group(0) if p.returncode==0 and match else 'UNKNOWN'
        except (OSError,subprocess.TimeoutExpired):
            result[binary+'_version']='UNAVAILABLE'
    try:
        p=subprocess.run(
            ['pg_dump','--dbname=service='+service,'--format=custom',
             '--no-owner','--no-acl','--file=/dev/null'],
            capture_output=True,text=True,timeout=timeout,
            env={**os.environ,'PGCONNECT_TIMEOUT':'5'})
        result['exit_code']=p.returncode
        result['status']='PASS' if p.returncode==0 else 'FAILED'
        # Never disclose raw stderr: it may include hostnames, user names or SQL.
        error=p.stderr.lower()
        markers=(
            ('SERVER_CLIENT_VERSION_MISMATCH',('server version mismatch','aborting because of server version mismatch')),
            ('PERMISSION_DENIED',('permission denied','must be owner','insufficient privilege')),
            ('CONNECTION_FAILED',('connection refused','could not connect','no such file or directory','timeout expired')),
            ('AUTH_FAILED',('password authentication failed','no password supplied','fe_sendauth')),
            ('SERVER_ERROR',('error:','fatal:')))
        result['failure_class']='NONE' if p.returncode==0 else next(
            (name for name,signatures in markers if any(x in error for x in signatures)),
            'UNCLASSIFIED')
    except subprocess.TimeoutExpired:
        result.update(status='TIMED_OUT',failure_class='TIMEOUT')
    except OSError:
        result.update(status='UNAVAILABLE',failure_class='EXECUTABLE_OR_OS')
    return result


def main():
    assert os.environ.get('RUNNER_NAME')=='ubuntu-usb' and os.getuid()!=0
    home=Path.home(); report={'schema':'KEBUNATE_NATIVE_DIAGNOSTIC_V1',
        'read_only':True,'circuit_replayed':False,'provider_inference_requested':False,
        'free_bytes':shutil.disk_usage(home).free,'circuits':[]}
    fs=os.statvfs(home)
    report['free_inodes']=fs.f_favail
    report['storage_usage']=storage_summary(home)
    report['storage_deleted']=False
    profile=home/'.config/knowingmind/kmcw-owner-runtime.json'
    if profile.is_file():
        config=json.loads(profile.read_text())
        assert config['account_id']=='posix:'+str(os.getuid())
        base=Path(config['runtime_base'])
        report['pg_dump_diagnostic']=pg_dump_diagnostic(config['postgres_service'])
        for p in sorted((base/'queue/checkpoints').glob('.kmcp-circuit-*.json'),key=lambda p:p.stat().st_mtime,reverse=True)[:20]:
            if p.stat().st_size>1048576:continue
            raw=p.read_bytes();d=json.loads(raw);b=d.get('data',{}).get('body',{})
            if not b:continue
            # Never include arguments, outputs, prompts, identities or provider data.
            nodes=b.get('nodes',{})
            if isinstance(nodes,dict):nodes=list(nodes.values())
            report['circuits'].append({'checkpoint_sha256':hashlib.sha256(raw).hexdigest(),
                'status':code(b.get('status')),'reason':code(b.get('reason')),
                'nodes':[{'status':code(n.get('status')),'error':code(n.get('error'))}
                         for n in nodes if isinstance(n,dict)]})
        try:
            import psycopg
            with psycopg.connect('service='+config['postgres_service'],connect_timeout=5) as conn:
                with conn.cursor() as cur:
                    cur.execute('SET TRANSACTION READ ONLY')
                    cur.execute("SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='circuit' AND table_name IN ('usage_events','core_memory') ORDER BY table_name,ordinal_position")
                    report['postgres_columns']=cur.fetchall()
                    cur.execute("SELECT count(*) FROM circuit.core_memory WHERE mission_id=%s AND task_id IN ('SSH-RECOVERY-ACCEPTANCE','N8N-ARTCRAFT-ACCEPTANCE')",(config['mission_id'],))
                    report['scoped_core_memory_count']=cur.fetchone()[0]
        except Exception as exc:
            report['postgres_error_type']=type(exc).__name__
            report['postgres_sqlstate']=getattr(exc,'sqlstate',None)
    receipt=home/'KnowingMind/state/installations/local-services.json'
    if receipt.is_file():
        d=json.loads(receipt.read_text());report['local_services']={
            'status':code(d.get('status')),'reason':code(d.get('blocker',{}).get('reason')),
            'error_type':d.get('blocker',{}).get('type') if d.get('blocker',{}).get('type') in {'RuntimeError','AssertionError','OSError','FileNotFoundError','TimeoutExpired'} else None,
            'receipt_sha256':hashlib.sha256(receipt.read_bytes()).hexdigest()}
    env={**os.environ,'XDG_RUNTIME_DIR':'/run/user/'+str(os.getuid()),
         'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/'+str(os.getuid())+'/bus'}
    p=subprocess.run(['systemctl','--user','is-active','km-n8n-local.service'],env=env,capture_output=True,text=True,timeout=10)
    report['n8n_service']=p.stdout.strip() if p.stdout.strip() in {'active','inactive','failed','unknown'} else 'UNKNOWN'
    report['io_pressure']=(Path('/proc/pressure/io').read_text() if Path('/proc/pressure/io').is_file() else None)
    target=Path(os.environ['RUNNER_TEMP'])/'kmcp-native-diagnostics.json'
    target.write_text(json.dumps(report,indent=2)+'\n');target.chmod(0o600)
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
