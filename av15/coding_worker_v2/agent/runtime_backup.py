"""Private portable runtime backup; never formats disks or restores over live state."""
from __future__ import annotations
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from .tools import ToolError

MAX_FILES = 100000
MAX_BYTES = 10 * 1024 ** 3
EXCLUDE = {'.git', '.venv', 'node_modules', '__pycache__', '.ssh', '.wrangler',
           'credentials', 'secrets', 'id_rsa', 'id_ed25519'}


def digest_file(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''): value.update(chunk)
    return value.hexdigest()


def _run(argv, *, cwd=None, timeout=120):
    try:
        result = subprocess.run(argv, cwd=cwd, capture_output=True, timeout=timeout)
        if result.returncode:
            raise ToolError('BACKUP_COMMAND_FAILED', Path(argv[0]).name)
        return result.stdout.decode('utf-8').strip()
    except (OSError, subprocess.TimeoutExpired):
        raise ToolError('BACKUP_COMMAND_UNAVAILABLE', Path(argv[0]).name) from None


def _excluded(path):
    return (any(x in EXCLUDE or x.startswith('.env') for x in path.parts)
            or path.suffix.lower() in {'.key', '.pem', '.p12', '.pfx', '.lock', '.pid'}
            or path.name in {'.pgpass', '.pg_service.conf', 'config.json', '.credentials', '.runner'})


def create_backup(source, runtime, output, *, catalog=None, pg_service=None):
    source, runtime, output = (Path(x).expanduser().resolve(strict=True)
                               for x in (source, runtime, output))
    if not all(p.is_dir() for p in (source, runtime, output)):
        raise ToolError('BACKUP_DIRECTORY_REQUIRED')
    if output.is_relative_to(runtime) or output.is_relative_to(source):
        raise ToolError('BACKUP_RECURSIVE_TARGET_DENIED')
    if output.stat().st_uid != os.getuid() or output.stat().st_mode & 0o077:
        raise ToolError('BACKUP_PRIVATE_OUTPUT_REQUIRED')
    if pg_service and not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', pg_service):
        raise ToolError('BACKUP_PG_SERVICE_INVALID')
    source_sha = _run(['git', 'rev-parse', 'HEAD'], cwd=source)
    if not re.fullmatch(r'[0-9a-f]{40}', source_sha):
        raise ToolError('BACKUP_SOURCE_SHA_INVALID')
    # Preserve tracked modifications as a private binary patch; no reset/stash.
    status = _run(['git', 'status', '--porcelain', '--untracked-files=no'], cwd=source)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    archive = output / ('kmcw-' + stamp + '.tar.gz')
    excluded, files, total = [], {}, 0
    with tempfile.TemporaryDirectory(prefix='.kmcw-backup-', dir=output) as tmp:
        tmp = Path(tmp)
        _run(['git', 'bundle', 'create', str(tmp / 'source.bundle'), '--all'], cwd=source)
        _run(['git', 'bundle', 'verify', str(tmp / 'source.bundle')], cwd=source)
        if status:
            with (tmp / 'tracked-changes.patch').open('wb') as stream:
                result = subprocess.run(['git', 'diff', '--binary', 'HEAD'], cwd=source,
                                        stdout=stream, stderr=subprocess.PIPE, timeout=60)
                if result.returncode: raise ToolError('BACKUP_PATCH_FAILED')
        if pg_service:
            db = _run(['psql', '-X', '-w', '-At', '-d', 'service=' + pg_service,
                       '-c', 'SELECT current_database()'])
            if db != 'km_microops': raise ToolError('BACKUP_DB_TARGET_MISMATCH')
            _run(['pg_dump', '--dbname=service=' + pg_service, '--format=custom',
                  '--no-owner', '--no-acl', '--file=' + str(tmp / 'km_microops.dump')], timeout=300)
            _run(['pg_restore', '--list', str(tmp / 'km_microops.dump')])
        roots = [(tmp, 'recovery'), (runtime, 'runtime')]
        if catalog: roots.append((Path(catalog).expanduser().resolve(strict=True), 'catalog'))
        for root, prefix in roots:
            if not root.is_dir(): raise ToolError('BACKUP_DIRECTORY_REQUIRED')
            for directory, dirs, names in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not _excluded(Path(d))
                                 and not (Path(directory) / d).is_symlink())
                for name in sorted(names):
                    path = Path(directory) / name
                    rel = path.relative_to(root)
                    key = prefix + '/' + rel.as_posix()
                    if path.is_symlink() or _excluded(rel):
                        excluded.append(key); continue
                    meta = path.stat()
                    if not stat.S_ISREG(meta.st_mode):
                        excluded.append(key); continue
                    total += meta.st_size
                    if len(files) >= MAX_FILES or total > MAX_BYTES:
                        raise ToolError('BACKUP_SIZE_LIMIT')
                    files[key] = {'path': path, 'size': meta.st_size,
                                  'sha256': digest_file(path), 'mode': meta.st_mode & 0o700}
        manifest = {'schema': 'KMCW_PORTABLE_BACKUP_V1', 'source_sha': source_sha,
            'tracked_changes': bool(status), 'created_at': stamp,
            'files': {k: {x:v for x,v in row.items() if x != 'path'} for k,row in files.items()},
            'exclusions': excluded[:1000], 'excluded_credentials': True,
            'database': 'km_microops' if pg_service else None,
            'consistency': 'PG_TRANSACTIONAL_SNAPSHOT_RUNTIME_FILES_VERIFIED_NOT_GLOBAL_ATOMIC',
            'database_restore': 'EXPLICIT_TARGET_REQUIRED_NOT_AUTOMATIC',
            're_enrollment_required': ['GitHub runner identity', 'Cloudflare login/tunnel',
                                       'database service credentials', 'Admin session'],
            'ssd_boot_acceptance': 'NOT_RUN'}
        try:
            fd = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream, tarfile.open(fileobj=stream, mode='w:gz') as tar:
                raw = json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode()
                info = tarfile.TarInfo('manifest.json'); info.size = len(raw); info.mode = 0o600
                tar.addfile(info, io.BytesIO(raw))
                for key, row in files.items():
                    if row['path'].is_symlink() or digest_file(row['path']) != row['sha256']:
                        raise ToolError('BACKUP_SOURCE_CHANGED_RETRY_WHEN_IDLE')
                    with row['path'].open('rb') as payload:
                        info = tarfile.TarInfo(key); info.size = row['size']; info.mode = row['mode'] or 0o600
                        tar.addfile(info, payload)
            # Validation reads every payload hash; no archive is accepted on tar success alone.
            verify_backup(archive)
        except BaseException:
            archive.unlink(missing_ok=True)
            raise
    receipt = {'schema': 'KMCW_BACKUP_RECEIPT_V1', 'archive': str(archive),
        'sha256': digest_file(archive), 'source_sha': source_sha, 'files': len(files),
        'bytes': total, 'integrity': 'PASS', 'database_dump': bool(pg_service),
        'credentials': 'EXCLUDED_REENROLL_TARGET', 'ssd_boot_acceptance': 'NOT_RUN'}
    (archive.with_suffix(archive.suffix + '.receipt.json')).write_text(json.dumps(receipt, indent=2))
    os.chmod(archive.with_suffix(archive.suffix + '.receipt.json'), 0o600)
    return receipt


def verify_backup(archive):
    try:
        with tarfile.open(archive, 'r:gz') as tar:
            members = []
            for member in tar:
                members.append(member)
                if len(members) > MAX_FILES + 1:
                    raise ToolError('BACKUP_ARCHIVE_LIMIT')
            if not members: raise ToolError('BACKUP_ARCHIVE_INVALID')
            names = [m.name for m in members]
            if len(set(names)) != len(names) or names[0] != 'manifest.json':
                raise ToolError('BACKUP_ARCHIVE_INVALID')
            for item in members:
                path = PurePosixPath(item.name)
                if (not item.isfile() or path.is_absolute() or '..' in path.parts
                        or '\\' in item.name or str(path) != item.name or item.size < 0):
                    raise ToolError('BACKUP_UNSAFE_MEMBER')
            if members[0].size > 16 * 1024 ** 2 or sum(m.size for m in members) > MAX_BYTES + 16 * 1024 ** 2:
                raise ToolError('BACKUP_ARCHIVE_LIMIT')
            manifest = json.load(tar.extractfile(members[0]))
            if manifest.get('schema') != 'KMCW_PORTABLE_BACKUP_V1' or not isinstance(manifest.get('files'), dict):
                raise ToolError('BACKUP_MANIFEST_INVALID')
            if set(names[1:]) != set(manifest['files']):
                raise ToolError('BACKUP_MANIFEST_MEMBERS_MISMATCH')
            for item in members[1:]:
                row = manifest['files'][item.name]
                if (not isinstance(row, dict) or set(row) != {'size', 'sha256', 'mode'}
                        or type(row['size']) is not int or type(row['mode']) is not int
                        or not isinstance(row['sha256'], str)
                        or not re.fullmatch(r'[0-9a-f]{64}', row['sha256'])):
                    raise ToolError('BACKUP_MANIFEST_INVALID')
                digest = hashlib.sha256()
                with tar.extractfile(item) as payload:
                    for chunk in iter(lambda: payload.read(1024 * 1024), b''): digest.update(chunk)
                if row.get('size') != item.size or row.get('sha256') != digest.hexdigest():
                    raise ToolError('BACKUP_PAYLOAD_INTEGRITY_FAILED')
            return manifest
    except ToolError:
        raise
    except (OSError, ValueError, KeyError, tarfile.TarError):
        raise ToolError('BACKUP_ARCHIVE_UNREADABLE') from None


def restore_backup(archive, destination):
    manifest = verify_backup(archive)
    target = Path(destination).expanduser()
    if target.is_symlink() or target.exists():
        raise ToolError('RESTORE_NEW_DESTINATION_REQUIRED')
    parent = target.parent.resolve(strict=True)
    target = parent / target.name
    # Stage and atomically rename only a verified, new directory; never overwrite.
    with tempfile.TemporaryDirectory(prefix='.kmcw-restore-', dir=parent) as tmp:
        tmp = Path(tmp)
        with tarfile.open(archive, 'r:gz') as tar:
            for name, row in manifest['files'].items():
                path = tmp / name
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with tar.extractfile(name) as source, path.open('xb') as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)
                os.chmod(path, row.get('mode', 0o600) & 0o700 or 0o600)
                if digest_file(path) != row['sha256']:
                    raise ToolError('RESTORE_INTEGRITY_FAILED')
        (tmp / 'manifest.json').write_text(json.dumps(manifest, indent=2))
        # mkdir reserves the name without replacing a concurrently-created path.
        target.mkdir(mode=0o700)
        try:
            for path in tmp.iterdir(): shutil.move(str(path), target / path.name)
        except BaseException:
            raise ToolError('RESTORE_INCOMPLETE_KEEP_ORIGINAL_ARCHIVE') from None
    return {'schema': 'KMCW_RESTORE_RECEIPT_V1', 'destination': str(target),
            'source_sha': manifest['source_sha'], 'files': len(manifest['files']),
            'integrity': 'PASS', 'database_restored': False,
            'credentials': 'REENROLL_REQUIRED', 'ssd_boot_acceptance': 'NOT_RUN'}
