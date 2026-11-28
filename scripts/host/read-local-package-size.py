#!/usr/bin/env python3
"""Metadata-only sizing of the exact verified local n8n delivery."""
import hashlib
import json
import os
import shutil
import tarfile
from pathlib import Path

assert os.environ.get('RUNNER_NAME')=='ubuntu-usb' and os.getuid()!=0
root=Path.home()/'.cache/knowingmind/local-services'
archive=root/'n8n-2.48.7-linux-x64-node24.tar.gz'
h=hashlib.sha256()
with archive.open('rb') as stream:
    for chunk in iter(lambda:stream.read(1048576),b''):h.update(chunk)
assert h.hexdigest()=='8958ea4bf9a90ffcaa135a297290cf007218249265dfbcd7724b5e1d002c6613'
count=total=files=0
with tarfile.open(archive,'r|gz') as stream:
    for entry in stream:
        count+=1;total+=entry.size;files+=entry.isfile()
        assert count<1000000 and total<16*1024**3,'AUDIT_LIMIT'
report={'schema':'KEBUNATE_VERIFIED_PACKAGE_SIZE_V1','read_only':True,
    'archive_sha256':h.hexdigest(),'members':count,'regular_files':files,
    'unpacked_bytes':total,'free_bytes':shutil.disk_usage(root).free,
    'compressed_bytes':archive.stat().st_size,'extracted':False}
target=Path(os.environ['RUNNER_TEMP'])/'kmcp-local-package-size.json'
target.write_text(json.dumps(report,indent=2)+'\n');target.chmod(0o600)
print(json.dumps(report,indent=2))
