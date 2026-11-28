"""D12 native source/runtime-file backup and isolated restore via existing code.

No credential backup, database/server restore, format, overwrite or service change.
"""
import os
from pathlib import Path

from ..tasks import ID_RE
from .native_database import NativeOwnerReadAuthority
from .runtime_backup import create_backup, verify_backup, restore_backup, digest_file
from .tools import ToolError


def require_native_owner(ctx):
    marker = ctx.get('_native_owner_read_authority')
    uid = os.getuid()
    if (uid == 0 or not isinstance(marker, NativeOwnerReadAuthority) or marker.uid != uid
            or ctx.get('account_id') != 'posix:' + str(uid)):
        raise ToolError('D12_NATIVE_OWNER_AUTHORITY_REQUIRED')


def run_d12(args, ctx):
    # M01: original source/runtime ownership comes from native private config.
    require_native_owner(ctx)
    source = Path(ctx['workspace']).resolve(strict=True)
    runtime = Path(ctx['store'].base).parent.resolve(strict=True)
    ident = args['restore_id']
    if not isinstance(ident, str) or not ID_RE.fullmatch(ident):
        raise ToolError('D12_RESTORE_ID_INVALID')
    output = Path(args['output_dir']).expanduser().resolve(strict=True)
    destination = output/('restore-'+ident)
    if destination.exists() or destination.is_symlink():
        raise ToolError('D12_NEW_ISOLATED_RESTORE_REQUIRED')
    # M02/M03: original bounded backup checks source consistency and copies only
    # approved runtime files plus the verified git bundle; never whole-host files.
    backup = create_backup(source, runtime, output)
    # M04: reread every archive payload digest, no success based on exit code only.
    manifest = verify_backup(backup['archive'])
    if digest_file(backup['archive']) != backup['sha256']:
        raise ToolError('D12_BACKUP_DIGEST_MISMATCH')
    # M05: original restore requires a new destination; cannot replace live state.
    restored = restore_backup(backup['archive'], destination)
    # M06: compare each restored file to its original manifest hash.
    for relative, record in manifest['files'].items():
        if digest_file(destination/relative) != record['sha256']:
            raise ToolError('D12_RESTORE_READBACK_MISMATCH')
    return {'circuit':'D12','steps_completed':[f'D12-M{i:02d}' for i in range(1,7)],
            'status':'FILE_BACKUP_RESTORE_READBACK_VERIFIED',
            'scope':'SOURCE_BUNDLE_AND_RUNTIME_FILES_ONLY',
            'backup':backup,'restore':restored,'production_database_restored':False,
            'credentials':'EXCLUDED','independent_qc':'NOT_RUN','authority_granted':False}


def register_backup(registry,catalog):
    from .tools import ToolSpec
    def call(args,ctx):
        if any(f'D12-M{i:02d}' not in catalog._operations for i in range(1,7)):
            raise ToolError('KMCO_D12_CATALOG_INCOMPLETE')
        return run_d12(args,ctx)
    registry.register(ToolSpec('kmco.d12.backup_restore',
        'Original private runtime file backup and isolated restore',
        {'output_dir':'str','restore_id':'str'},{},'write','creates new private backup and restore only',
        'native owner approval; no database restore, no live overwrite; actual payload checksums'),call)
