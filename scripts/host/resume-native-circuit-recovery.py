#!/usr/bin/env python3
"""Complete the corrected owner circuit, then private backup + isolated restore."""
import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime,timezone
from pathlib import Path
import psycopg
from coding_worker_v2.agent.native_cloudflare import private_file
from coding_worker_v2.agent.owner_cli import execute,load_owner_runtime
from coding_worker_v2.agent import circuits
from coding_worker_v2.agent.runtime_backup import create_backup,restore_backup,digest_file
from coding_worker_v2.agent.ssh_cli import save_private


def command(argv,timeout=180):
    p=subprocess.run([str(x) for x in argv],capture_output=True,text=True,timeout=timeout)
    if p.returncode:raise RuntimeError('RECOVERY_COMMAND_FAILED:'+Path(str(argv[0])).name)
    return p.stdout.strip()


def main():
    assert os.environ.get('RUNNER_NAME')=='ubuntu-usb' and os.getuid()!=0
    home=Path.home();profile=home/'.config/knowingmind/kmcw-owner-runtime.json'
    cfg=private_file(profile);assert cfg['account_id']=='posix:'+str(os.getuid())
    report={'schema':'KEBUNATE_NATIVE_CIRCUIT_RESTORE_V2','started_at':datetime.now(timezone.utc).isoformat(),
        'production_database_restored':False,'public_http_changed':False,'disk_formatted':False,
        'external_notifications_sent':False,'independent_qc':'NOT_ATTESTED_BY_EXECUTOR',
        'off_device_copy':'NOT_PERFORMED','ssd_boot_acceptance':'NOT_RUN'}
    try:
        workspace=Path(cfg['workspace']);base=Path(cfg['runtime_base']);venv=Path(sys.prefix)
        report['installed_source_sha']=json.loads((venv/'.knowingmind-managed.json').read_text())['source_sha']
        report['workspace_source_sha']=command(['git','-C',workspace,'rev-parse','HEAD'])
        module=Path(os.environ['RUNNER_TEMP'])/'accept-runtime-recovery.py'
        spec=importlib.util.spec_from_file_location('native_acceptance_graph',module)
        graph_module=importlib.util.module_from_spec(spec);spec.loader.exec_module(graph_module)
        file='av15/coding_worker_v2/README.md'
        quote=next(x.strip() for x in (workspace/file).read_text().splitlines() if x.strip())[:500]
        graph=graph_module.recovery_canary_graph(cfg['cloudflare']['allowed_models'][0],file,quote)
        ctx,registry=load_owner_runtime(profile)
        circuits.validate_graph(graph,registry)
        # Same original request, only if no earlier submitted checkpoint exists.
        request='recovery-45030098028'
        circuit_id='circuit-'+circuits.digest([cfg['account_id'],cfg['mission_id'],request])[:24]
        existing=base/'queue/checkpoints'/('.kmcp-'+circuit_id+'.json')
        if existing.exists():
            b=json.loads(existing.read_text())['data']['body']
            assert b['status']=='COMPLETED' and b['manifest']['graph']==graph,'EXISTING_CIRCUIT_RECONCILIATION_REQUIRED'
            result={'result':{'status':'COMPLETED'},'report':circuits.report({'circuit_id':circuit_id},ctx)}
            report['circuit_reused']=True
        else:
            result=execute(profile,graph,'Verify real P01, provider inference and four-part memory',request,approved=True)
            report['circuit_reused']=False
        status=result['result'].get('status');report['circuit']={'status':status,
            'reason':result['result'].get('reason'),'circuit_id':circuit_id}
        assert status=='COMPLETED','NATIVE_CIRCUIT_NOT_COMPLETED'
        receipt=result['report']['receipt'];assert digest_file(receipt['uri'])==receipt['sha256']
        report['circuit']['receipt']=receipt
        pg=cfg['postgres_service']
        with psycopg.connect('service='+pg,connect_timeout=5) as conn:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute('SHOW server_version_num');major=int(cur.fetchone()[0])//10000
                cur.execute("SELECT dukkha,samudaya,nirodha,magga,result_sha256 FROM circuit.core_memory WHERE mission_id=%s AND task_id=%s AND operation='kmco.p01.evidence' ORDER BY updated_at DESC LIMIT 1",(cfg['mission_id'],cfg['task_id']))
                row=cur.fetchone();assert row and all(isinstance(v,dict) and v for v in row[:4])
                expected=row[4];assert len(expected)==64
        report['four_part_memory']={'write_readback':'PASS','result_sha256':expected,'state':'DERIVED_UNVERIFIED'}
        # Snapshot existing runtime only; no source reset, credential export or disk targeting.
        recovery=base/'state/recovery';wheels=recovery/'wheels';wheels.mkdir(parents=True,exist_ok=True,mode=0o700)
        wheel=home/'.cache/knowingmind/local-services/knowingmind_kmcp-0.1.0-py3-none-any.whl'
        assert digest_file(wheel)=='effc3a1a5f98de6cd00607c81328ad3041e92480fb05beac3cd39ecdfabbd5b3'
        shutil.copy2(wheel,wheels/wheel.name)
        command([venv/'bin/python','-m','pip','download','--only-binary=:all:','--no-deps','--dest',wheels,
                 'psycopg=='+psycopg.__version__,'psycopg-binary=='+psycopg.__version__],timeout=180)
        save_private(recovery/'kmcw-owner-runtime.profile.json',cfg)
        free=shutil.disk_usage(home).free
        from coding_worker_v2.agent.runtime_backup import _excluded
        def footprint(root):
            total=0
            for directory,dirs,names in os.walk(root,followlinks=False):
                dirs[:]=[d for d in dirs if not _excluded(Path(d)) and not (Path(directory)/d).is_symlink()]
                for name in names:
                    path=Path(directory)/name
                    if path.is_file() and not path.is_symlink() and not _excluded(path.relative_to(root)):total+=path.stat().st_size
            return total
        gitdir=Path(command(['git','-C',workspace,'rev-parse','--git-common-dir']))
        if not gitdir.is_absolute():gitdir=workspace/gitdir
        estimated=footprint(gitdir)+footprint(base)+footprint(Path(cfg['kmco_catalog']))
        required=estimated*3+512*1024**2
        report['backup_space']={'estimated_payload_bytes':estimated,'free_bytes':free,'required_bytes':required}
        assert free>required,'BACKUP_RESTORE_FREE_SPACE_REQUIRED'
        backup_dir=home/'KnowingMind/backups';backup_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        assert backup_dir.stat().st_mode&0o077==0
        backup=create_backup(workspace,base,backup_dir,catalog=cfg['kmco_catalog'],pg_service=pg)
        report['backup']=backup
        tests=home/'KnowingMind/recovery-tests';tests.mkdir(parents=True,exist_ok=True,mode=0o700)
        with tempfile.TemporaryDirectory(prefix='native-restore-',dir=tests) as temp:
            temp=Path(temp);restored=temp/'restored'
            report['file_restore']=restore_backup(backup['archive'],restored)
            repo=temp/'workspace';command(['git','clone','--quiet',restored/'recovery/source.bundle',repo])
            command(['git','-C',repo,'checkout','--quiet','--detach',report['workspace_source_sha']])
            patch=restored/'recovery/tracked-changes.patch'
            if patch.is_file():command(['git','-C',repo,'apply','--binary',patch])
            testvenv=temp/'venv';command([sys.executable,'-m','venv',testvenv])
            wr=restored/'runtime/state/recovery/wheels'
            command([testvenv/'bin/python','-m','pip','install','--no-index','--find-links',wr,
                     wr/wheel.name,'psycopg[binary]=='+psycopg.__version__])
            command([testvenv/'bin/kmcw','--help']);command([testvenv/'bin/kmcp','--help'])
            report['offline_restore_entrypoints']='PASS'
            pg_bin=Path('/usr/lib/postgresql')/str(major)/'bin'
            socket=temp/'socket';socket.mkdir(mode=0o700);data=temp/'pg-data'
            command([pg_bin/'initdb','-D',data,'--auth-local=trust','--auth-host=reject','--no-locale','--encoding=UTF8'])
            started=False
            try:
                opts=shlex.join(['-k',str(socket),'-p','55439','-h','','-c','shared_buffers=16MB','-c','max_connections=10'])
                command([pg_bin/'pg_ctl','-D',data,'-l',temp/'postgres.log','-o',opts,'-w','start']);started=True
                command([pg_bin/'createdb','-h',socket,'-p','55439','km_microops'])
                command([pg_bin/'pg_restore','--exit-on-error','--no-owner','--no-acl','-h',socket,'-p','55439','-d','km_microops',restored/'recovery/km_microops.dump'],timeout=300)
                with psycopg.connect(host=str(socket),port=55439,dbname='km_microops') as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT result_sha256 FROM circuit.core_memory WHERE mission_id=%s AND task_id=%s AND operation='kmco.p01.evidence' ORDER BY updated_at DESC LIMIT 1",(cfg['mission_id'],cfg['task_id']))
                        assert cur.fetchone()==(expected,)
                report['postgres_restore']={'status':'PASS','target':'TEMPORARY_UNIX_SOCKET_ONLY','original_hash_matches':True}
            finally:
                if started:command([pg_bin/'pg_ctl','-D',data,'-m','fast','-w','stop'])
        report['status']='NATIVE_CIRCUIT_MEMORY_PRIVATE_BACKUP_ISOLATED_RESTORE_PASS'
    except Exception as exc:
        report['status']='PARTIAL_WITH_EXPLICIT_BLOCKER'
        report['blocker']={'type':type(exc).__name__,'reason':getattr(exc,'reason',str(exc) if isinstance(exc,(RuntimeError,AssertionError)) else type(exc).__name__)}
        raise
    finally:
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        target=home/'KnowingMind/state/installations/native-circuit-restore-v2.json';target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        save_private(target,report)
        (Path(os.environ['RUNNER_TEMP'])/'kmcp-native-circuit-restore.json').write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))


if __name__=='__main__':main()
