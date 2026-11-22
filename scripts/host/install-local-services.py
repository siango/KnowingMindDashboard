#!/usr/bin/env python3
"""Owner-local n8n + official ArtCraft/PhotoCraft; reuse existing runtime queue.

No HTTP bridge job rerun, production service restart, disk formatting, external
notification or public tunnel is performed. Only metadata receipts are uploaded.
"""
import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import struct
import subprocess
import tarfile
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.request import urlopen


def run(argv,*,env=None,timeout=180):
    p=subprocess.run([str(x) for x in argv],capture_output=True,text=True,env=env,timeout=timeout)
    if p.returncode:
        # Record fixed codes only; no process output that could contain native secrets.
        raise RuntimeError('COMMAND_FAILED:'+Path(str(argv[0])).name)
    return p.stdout


def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def private(path,value):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.is_symlink():raise RuntimeError('SYMLINK_TARGET_DENIED')
    fd,tmp=tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def unpack(archive,target):
    marker=target/'.kebunate-release.json'
    expected=sha(archive)
    if target.exists():
        assert marker.is_file() and json.loads(marker.read_text())['sha256']==expected,'EXISTING_RELEASE_NOT_OWNED'
        return
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    with tempfile.TemporaryDirectory(prefix='.install-',dir=target.parent) as tmp:
        temp=Path(tmp)
        with tarfile.open(archive,'r:gz') as tar:
            members=tar.getmembers()
            total=sum(m.size for m in members)
            assert len(members)<150000 and total<4*1024**3,'PACKAGE_LIMIT'
            assert shutil.disk_usage(target.parent).free>total+512*1024**2,'INSTALL_SPACE_REQUIRED'
            tar.extractall(temp,filter='data')
        private(temp/'.kebunate-release.json',{'sha256':expected})
        temp.rename(target)


def link_owned(link,target):
    link.parent.mkdir(parents=True,exist_ok=True)
    if link.exists() or link.is_symlink():
        assert link.is_symlink() and link.resolve()==target.resolve(),'EXISTING_COMMAND_NOT_OWNED'
    else:link.symlink_to(target)


def executable(root,name):
    candidates=[x for x in root.rglob(name) if x.is_file() and os.access(x,os.X_OK)]
    assert len(candidates)==1,'EXACT_BINARY_REQUIRED:'+name
    return candidates[0]


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--bundle',required=True)
    ap.add_argument('--source-sha',required=True);ap.add_argument('--receipt',required=True)
    args=ap.parse_args();assert os.environ.get('RUNNER_NAME')=='ubuntu-usb';assert os.getuid()!=0
    bundle=Path(args.bundle).resolve();home=Path.home()
    report={'schema':'KEBUNATE_LOCAL_SERVICES_ACCEPTANCE_V1','source_sha':args.source_sha,
            'node_id':'ubuntu-usb','started_at':datetime.now(timezone.utc).isoformat(),
            'http_bridge_modified':False,'public_endpoints_created':False,
            'external_notifications_sent':False,'ssd_boot_acceptance':'NOT_RUN','components':{}}
    try:
        manifest=json.loads((bundle/'release.json').read_text())
        assert manifest['source_sha']==args.source_sha
        for rel,digest in manifest['files'].items():
            path=(bundle/rel).resolve();assert path.is_relative_to(bundle) and path.is_file()
            assert sha(path)==digest,'BUNDLE_INTEGRITY_FAILED'
        base=home/'.local/share/knowingmind';venv=home/'KnowingMind/.venvs/kmcp-portable'
        assert (venv/'.knowingmind-managed.json').is_file()
        # Serialized by the one existing runner; the old HTTP job is not rerun.
        wheel=next(bundle.glob('knowingmind_kmcp-*.whl'))
        run([venv/'bin/python','-m','pip','install','--no-index','--no-deps','--force-reinstall',wheel])
        for name in ('kmcw','kmcp','km'):link_owned(home/'.local/bin'/name,venv/'bin'/name)
        private(venv/'.knowingmind-managed.json',{'schema':'KMCP_MANAGED_VENV_V1',
            'state':'INSTALLED','source_sha':args.source_sha,'wheel_sha256':sha(wheel)})
        photo_root=base/'artcraft/releases/photocraft-0.23.1'
        studio_root=base/'artcraft/releases/artcraft-0.41.1'
        unpack(bundle/'photocraft-0.23.1-linux-x86_64.tar.gz',photo_root)
        unpack(bundle/'artcraft-0.41.1-linux-x86_64.tar.gz',studio_root)
        photo=executable(photo_root,'photocraft-cli');studio=executable(studio_root,'artcraft')
        link_owned(home/'.local/bin/photocraft-cli',photo)
        link_owned(home/'.local/bin/artcraft',studio)
        for name in ('input','output'):(base/'artcraft'/name).mkdir(exist_ok=True,mode=0o700)
        assert 'not found' not in run(['ldd',photo])
        studio_ldd=run(['ldd',studio]);report['components']['artcraft_studio']={
            'status':'INSTALLED','version':'0.41.1','binary':str(studio),
            'shared_libraries':'PASS' if 'not found' not in studio_ldd else 'MISSING_DEPENDENCY',
            'desktop_window_acceptance':'NOT_OBSERVED'}
        desktop=home/'.local/share/applications/artcraft.desktop'
        if not desktop.exists():
            desktop.parent.mkdir(parents=True,exist_ok=True)
            desktop.write_text('[Desktop Entry]\nType=Application\nName=ArtCraft Studio\n'
                'Exec="'+str(studio)+'"\nIcon=applications-graphics\nTerminal=false\nCategories=Graphics;\nX-KEBUNATE-Managed=true\n')
        report['components']['photocraft']={'status':'INSTALLED','version':'0.23.1','binary':str(photo)}
        # Retain the actual existing local queue and mission from the native profile.
        cfg=home/'.config/knowingmind';original=cfg/'kmcw-owner-runtime.json'
        assert original.is_file(),'EXISTING_NATIVE_RUNTIME_PROFILE_REQUIRED'
        runtime=json.loads(original.read_text())
        assert runtime['account_id']=='posix:'+str(os.getuid())
        assert (Path(runtime['runtime_base'])/'queue').is_dir()
        runtime.pop('cloudflare',None)
        runtime.update(photocraft=True,audit_all_tools=True,task_id='N8N-ARTCRAFT-ACCEPTANCE')
        private(cfg/'n8n-runtime.json',runtime)
        n8n_root=base/'n8n/releases/2.48.7'
        unpack(bundle/'n8n-2.48.7-linux-x64-node24.tar.gz',n8n_root)
        n8n_js=n8n_root/'node_modules/n8n/bin/n8n';assert n8n_js.is_file()
        ext=base/'n8n/extensions';ext.mkdir(parents=True,exist_ok=True,mode=0o700)
        shutil.copy2(bundle/'KebunateCircuit.node.js',ext/'KebunateCircuit.node.js')
        data=base/'n8n/data';data.mkdir(parents=True,exist_ok=True,mode=0o700)
        env={'HOME':str(home),'PATH':str(home/'.local/bin')+':/usr/local/bin:/usr/bin:/bin','LANG':'C.UTF-8',
            'N8N_USER_FOLDER':str(data),'N8N_CUSTOM_EXTENSIONS':str(ext),'N8N_LISTEN_ADDRESS':'127.0.0.1',
            'N8N_HOST':'localhost','N8N_PORT':'5678','N8N_PROTOCOL':'http','N8N_SECURE_COOKIE':'false',
            'N8N_DIAGNOSTICS_ENABLED':'false','N8N_PERSONALIZATION_ENABLED':'false',
            'N8N_VERSION_NOTIFICATIONS_ENABLED':'false','N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS':'true',
            'GENERIC_TIMEZONE':'Asia/Bangkok','TZ':'Asia/Bangkok'}
        node=Path(shutil.which('node',path=env['PATH']));assert node.is_file()
        launcher=base/'n8n/n8n-local';launcher.parent.mkdir(parents=True,exist_ok=True)
        launcher.write_text('#!/usr/bin/env python3\nimport os,sys\nos.environ.update('+repr(env)+')\n'
                            'os.execv('+repr(str(node))+',['+repr(str(node))+','+repr(str(n8n_js))+',*sys.argv[1:]])\n')
        launcher.chmod(0o750);link_owned(home/'.local/bin/n8n',launcher)
        bus={**env,'XDG_RUNTIME_DIR':'/run/user/'+str(os.getuid()),
             'DBUS_SESSION_BUS_ADDRESS':'unix:path=/run/user/'+str(os.getuid())+'/bus'}
        unit_path=home/'.config/systemd/user/km-n8n-local.service'
        active=subprocess.run(['systemctl','--user','is-active','--quiet','km-n8n-local.service'],env=bus).returncode==0
        if active:
            assert unit_path.is_file() and 'X-KEBUNATE' in unit_path.read_text(),'EXISTING_UNIT_NOT_OWNED'
            run(['systemctl','--user','stop','km-n8n-local.service'],env=bus)
        # Never replace an unknown process already serving the conventional port.
        with socket.socket() as sock:
            assert sock.connect_ex(('127.0.0.1',5678))!=0,'EXISTING_N8N_LISTENER_REUSE_REQUIRED'
        version=run([launcher,'--version'],env=env,timeout=120).strip();assert '2.48.7' in version
        workflows=base/'n8n/workflows';workflows.mkdir(exist_ok=True,mode=0o700)
        shutil.copy2(bundle/'km_artcraft_local.json',workflows/'km_artcraft_local.json')
        run([launcher,'import:workflow','--input',workflows/'km_artcraft_local.json'],env=env,timeout=180)
        execution=run([launcher,'execute','--id','kmartcraft000001','--rawOutput'],env=env,timeout=240)
        # Logs may precede the JSON envelope; decode only an actual execution object.
        decoder=json.JSONDecoder();observed=None
        for offset,char in enumerate(execution):
            if char!='{':continue
            try:
                value,_=decoder.raw_decode(execution[offset:])
                if isinstance(value,dict) and isinstance(value.get('data'),dict) and 'resultData' in value['data']:
                    observed=value;break
            except ValueError:pass
        assert observed and not observed['data']['resultData'].get('error'),'N8N_EXECUTION_NOT_VERIFIED'
        run_data=observed['data']['resultData']['runData']['KEBUNATE Circuit']
        artifact=run_data[-1]['data']['main'][0][0]['json']
        path=Path(artifact['artifact']);assert path.is_relative_to(base/'artcraft/output')
        raw=path.read_bytes();assert raw[:8]==b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II',raw[16:24])==(64,64) and sha(path)==artifact['sha256']
        report['components']['n8n']={'status':'EXECUTION_PASS','version':version,
            'workflow_id':'kmartcraft000001','workflow_active':False,'artifact':artifact,
            'external_notifications_sent':False}
        unit_path.parent.mkdir(parents=True,exist_ok=True)
        if unit_path.exists():assert 'X-KEBUNATE' in unit_path.read_text(),'EXISTING_UNIT_NOT_OWNED'
        unit_path.write_text('# X-KEBUNATE managed owner-local n8n\n[Unit]\nDescription=KEBUNATE n8n local\n'
            '[Service]\nType=simple\nExecStart='+str(launcher)+' start\nRestart=on-failure\nRestartSec=5\n'
            'UMask=0077\n[Install]\nWantedBy=default.target\n')
        run(['systemctl','--user','daemon-reload'],env=bus)
        run(['systemctl','--user','enable','--now','km-n8n-local.service'],env=bus)
        healthy=False
        import time
        for _ in range(20):
            try:
                with urlopen('http://127.0.0.1:5678/healthz',timeout=2) as response:
                    healthy=response.status==200
                if healthy:break
            except OSError:pass
            time.sleep(1)
        assert healthy,'N8N_HEALTH_NOT_READY'
        report['components']['n8n']['service']='ACTIVE_LOOPBACK_HEALTH_PASS'
        report['components']['n8n']['owner_web_setup']='REQUIRES_OWNER_UI_SETUP_IF_NOT_ALREADY_DONE'
        report['components']['n8n']['linger']=run(['loginctl','show-user',str(os.getuid()),'-p','Linger','--value']).strip()
        # Coherent SQLite backup plus its native private encryption configuration.
        backup=home/'KnowingMind/backups';backup.mkdir(parents=True,exist_ok=True,mode=0o700)
        assert backup.stat().st_mode & 0o077==0,'PRIVATE_BACKUP_DIRECTORY_REQUIRED'
        archive=backup/('local-services-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'.tar.gz')
        with tempfile.TemporaryDirectory(prefix='.local-services-',dir=backup) as temp:
            temp=Path(temp);snapshot=temp/'n8n';snapshot.mkdir(mode=0o700)
            databases=list(data.rglob('database.sqlite'));assert len(databases)==1,'N8N_DATABASE_NOT_UNIQUE'
            with sqlite3.connect('file:'+str(databases[0])+'?mode=ro',uri=True) as source,sqlite3.connect(snapshot/'database.sqlite') as target:
                source.backup(target)
            native=databases[0].parent/'config'
            if native.is_file():shutil.copy2(native,snapshot/'config')
            shutil.copy2(cfg/'n8n-runtime.json',snapshot/'runtime-profile.json')
            with tarfile.open(archive,'w:gz',compresslevel=3) as tar:
                tar.add(snapshot,arcname='n8n');tar.add(workflows,arcname='workflows')
                tar.add(ext,arcname='extensions');tar.add(base/'artcraft/input',arcname='artcraft/input')
                tar.add(base/'artcraft/output',arcname='artcraft/output')
            archive.chmod(0o600)
        report['backup']={'archive':str(archive),'sha256':sha(archive),
            'n8n_sqlite_snapshot':'PASS','native_encryption_config':'LOCAL_PRIVATE_ONLY',
            'uploaded_to_github':False,'off_device_copy':'NOT_PERFORMED'}
        report['status']='LOCAL_N8N_TO_KMCP_TO_PHOTOCRAFT_PNG_ACCEPTANCE_PASS'
    except Exception as exc:
        report['status']='PARTIAL_WITH_EXPLICIT_BLOCKER'
        report['blocker']={'type':type(exc).__name__,
            'reason':str(exc)[:240] if isinstance(exc,(RuntimeError,AssertionError)) else type(exc).__name__}
        raise
    finally:
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        private(home/'KnowingMind/state/installations/local-services.json',report)
        Path(args.receipt).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
