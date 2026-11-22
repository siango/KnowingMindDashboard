"""Native owner credential channel. Credentials never enter runtime JSON."""
from __future__ import annotations
import json
import os
import stat
import subprocess
from pathlib import Path
from .cloudflare_tools import CloudflareClient
from .tools import ToolError


def private_file(path, limit=65536):
    path = Path(path).expanduser()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            meta = os.fstat(stream.fileno())
            if (not stat.S_ISREG(meta.st_mode) or meta.st_uid != os.getuid()
                    or meta.st_mode & 0o077 or meta.st_size > limit):
                raise ToolError('OWNER_CONFIG_PERMISSIONS_REQUIRED')
            raw = stream.read(limit + 1)
            if len(raw) > limit:
                raise ToolError('OWNER_CONFIG_TOO_LARGE')
            return json.loads(raw)
    except ToolError:
        raise
    except (OSError, ValueError):
        raise ToolError('OWNER_CONFIG_UNAVAILABLE') from None


def native_client(binding):
    if (not isinstance(binding, dict)
            or set(binding) != {'account_id', 'wrangler_project', 'allowed_models'}
            or not isinstance(binding['wrangler_project'], str)
            or not isinstance(binding['allowed_models'], list)):
        raise ToolError('CF_RUNTIME_BINDING_INVALID')
    project = Path(binding['wrangler_project']).expanduser().resolve()
    binary = project / 'node_modules/.bin/wrangler'
    try:
        for path in (project, binary.resolve(strict=True)):
            meta = path.stat()
            if meta.st_uid not in (0, os.getuid()) or meta.st_mode & 0o022:
                raise ToolError('CF_NATIVE_CLIENT_PERMISSIONS_DENIED')
        if not binary.is_file():
            raise ToolError('CF_NATIVE_CLIENT_UNAVAILABLE')
    except OSError:
        raise ToolError('CF_NATIVE_CLIENT_UNAVAILABLE') from None

    def credential():
        try:
            proc = subprocess.run([str(binary), 'auth', 'token', '--json'],
                cwd=project, capture_output=True, text=True, timeout=45,
                env={**os.environ, 'WRANGLER_SEND_METRICS': 'false', 'CI': 'true'})
            if proc.returncode or len(proc.stdout) > 16384:
                raise ToolError('CF_NATIVE_CREDENTIAL_UNAVAILABLE')
            doc = json.loads(proc.stdout)
            if doc.get('type') not in {'oauth', 'api_token'}:
                raise ToolError('CF_NATIVE_CREDENTIAL_INVALID')
            return doc['token']
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
            raise ToolError('CF_NATIVE_CREDENTIAL_UNAVAILABLE') from None
    return CloudflareClient(binding['account_id'], credential,
                            allowed_models=tuple(binding['allowed_models']))
