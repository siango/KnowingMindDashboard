#!/usr/bin/env python3
"""Build-side verified official desktop assets; no credentials or host state."""
import hashlib
import json
import shutil
from pathlib import Path
from urllib.request import urlopen

ASSETS = [
    {'name':'photocraft-0.23.1-linux-x86_64.tar.gz','version':'0.23.1',
     'url':'https://github.com/storytold/photocraft/releases/download/v0.23.1/photocraft-0.23.1-linux-x86_64.tar.gz',
     'sha256':'4383aef67a07cc370ad250a6ed60015205d3e6c463faf3ff4b0f67b8178ccfc8'},
    {'name':'artcraft-0.41.1-linux-x86_64.tar.gz','version':'0.41.1',
     'url':'https://github.com/storytold/artcraft/releases/download/artcraft-v0.41.1/artcraft-0.41.1-linux-x86_64.tar.gz',
     'sha256':'149f9748ea33553177c021e1ade47a195fad25b881129d4178fe5bd922a94eb4'},
]

if __name__=='__main__':
    root=Path('local-services-bundle');root.mkdir(exist_ok=True)
    for row in ASSETS:
        target=root/row['name']
        with urlopen(row['url'],timeout=120) as response,target.open('wb') as output:
            shutil.copyfileobj(response,output,1024*1024)
        assert hashlib.sha256(target.read_bytes()).hexdigest()==row['sha256'], 'UPSTREAM_DIGEST_MISMATCH'
    (root/'official-assets.json').write_text(json.dumps(ASSETS,indent=2))
