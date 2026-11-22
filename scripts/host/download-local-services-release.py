#!/usr/bin/env python3
"""Native GitHub-release delivery with persistent verified per-file cache."""
import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


def digest(path):
    result=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):result.update(data)
    return result.hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--manifest-sha',required=True)
    ap.add_argument('--software-sha',required=True);ap.add_argument('--installer',required=True)
    ap.add_argument('--installer-sha',required=True);ap.add_argument('--receipt',required=True)
    args=ap.parse_args();assert os.environ.get('RUNNER_NAME')=='ubuntu-usb'
    assert os.getuid()!=0
    repo='siango/KnowingMindDashboard';tag='km-local-services-20261123-candidate'
    cache=Path.home()/'.cache/knowingmind/local-services'
    cache.mkdir(parents=True,exist_ok=True,mode=0o700)
    os.umask(0o077)
    receipt=Path.home()/'KnowingMind/state/installations/local-services.json'
    if receipt.is_file():
        current=json.loads(receipt.read_text())
        if (current.get('source_sha')==args.software_sha and
                current.get('status')=='LOCAL_N8N_TO_KMCP_TO_PHOTOCRAFT_PNG_ACCEPTANCE_PASS'):
            Path(args.receipt).write_text(json.dumps(current,indent=2))
            print('SKIPPED_ALREADY_ACCEPTED_SAME_SOURCE');return
    def fetch(name,target):
        with tempfile.TemporaryDirectory(prefix='.incoming-',dir=cache) as temp:
            proc=subprocess.Popen(['gh','release','download',tag,'--repo',repo,
                                   '--pattern',name,'--dir',temp],
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            started=time.monotonic();last=0
            try:
                while proc.poll() is None:
                    elapsed=int(time.monotonic()-started)
                    if elapsed-last>=30:
                        size=sum(p.stat().st_size for p in Path(temp).rglob('*') if p.is_file())
                        print(json.dumps({'asset':name,'received_bytes':size,'elapsed_s':elapsed}),flush=True)
                        last=elapsed
                    if elapsed>2400:raise RuntimeError('RELEASE_ASSET_DOWNLOAD_TIMEOUT:'+name)
                    time.sleep(2)
                assert proc.returncode==0,'RELEASE_ASSET_DOWNLOAD_FAILED:'+name
                path=Path(temp)/name;assert path.is_file()
                shutil.move(path,target)
            finally:
                if proc.poll() is None:proc.kill();proc.wait()
    manifest=cache/'release.json'
    fetch('release.json',manifest)
    assert digest(manifest)==args.manifest_sha,'MANIFEST_INTEGRITY_FAILED'
    doc=json.loads(manifest.read_text());assert doc['source_sha']==args.software_sha
    def checked(item):
        name,expected=item
        assert re.fullmatch(r'[A-Za-z0-9_.-]+',name) and re.fullmatch(r'[a-f0-9]{64}',expected)
        path=cache/name
        if path.is_file() and not path.is_symlink() and digest(path)==expected:
            print('CACHE_VERIFIED:'+name,flush=True);return
        if path.exists() or path.is_symlink():path.unlink()
        fetch(name,path)
        assert digest(path)==expected,'ASSET_INTEGRITY_FAILED:'+name
        print('DOWNLOAD_VERIFIED:'+name,flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(checked,doc['files'].items()))
    installer=Path(args.installer).resolve(strict=True)
    assert digest(installer)==args.installer_sha,'INSTALLER_INTEGRITY_FAILED'
    proc=subprocess.run(['python3',str(installer),'--bundle',str(cache),
                         '--source-sha',args.software_sha,'--receipt',args.receipt])
    raise SystemExit(proc.returncode)


if __name__=='__main__':main()
