#!/usr/bin/env python3
"""Owned Ubuntu acceptance, local private backup and isolated PostgreSQL restore.

Never uploads archives, dumps, transcripts or credentials. No disk formatting,
production DB restore, service cutover or remote Admin authority is performed.
"""
import argparse
import configparser
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def command(argv, *, timeout=180, **kwargs):
    p = subprocess.run([str(x) for x in argv], capture_output=True, text=True,
                       timeout=timeout, **kwargs)
    if p.returncode:
        raise RuntimeError('COMMAND_FAILED:' + Path(str(argv[0])).name)
    return p.stdout


def private_json(path, value):
    from coding_worker_v2.agent.ssh_cli import save_private
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink(): raise RuntimeError('SYMLINK_CONFIG_DENIED')
    if path.exists() and path.stat().st_uid != os.getuid():
        raise RuntimeError('CONFIG_OWNER_MISMATCH')
    save_private(path, value)


def main():
    ap = argparse.ArgumentParser();ap.add_argument('--bundle', required=True)
    ap.add_argument('--source-sha', required=True);ap.add_argument('--receipt', required=True)
    args = ap.parse_args()
    assert os.environ.get('RUNNER_NAME') == 'ubuntu-usb'
    assert os.getuid() != 0
    home = Path.home(); bundle = Path(args.bundle).resolve()
    report = {'schema':'KMCW_NATIVE_RUNTIME_RECOVERY_ACCEPTANCE_V1',
        'source_sha':args.source_sha,'node_id':'ubuntu-usb',
        'started_at':datetime.now(timezone.utc).isoformat(),
        'production_http_cutover':False,'production_database_restore':False,
        'disk_formatting':False,'off_device_backup':'NOT_PERFORMED',
        'ssd_boot_acceptance':'NOT_RUN','independent_qc':'NOT_ATTESTED_BY_EXECUTOR'}
    try:
        profile = home/'.config/knowingmind/kmcw-owner-runtime.json'
        if not profile.is_file() or profile.is_symlink() or profile.stat().st_uid != os.getuid():
            raise RuntimeError('EXISTING_OWNER_PROFILE_REQUIRED')
        owner_profile = json.loads(profile.read_text())
        owner_digest = hashlib.sha256(profile.read_bytes()).hexdigest()
        report['owner_profile_sha256'] = owner_digest
        manifest = json.loads((bundle/'release.json').read_text())
        assert manifest['source_sha'] == args.source_sha
        for rel, sha in manifest['files'].items():
            path = (bundle/rel).resolve()
            assert path.is_relative_to(bundle) and path.is_file()
            assert hashlib.sha256(path.read_bytes()).hexdigest() == sha
        venv = home/'KnowingMind/.venvs/kmcp-portable';py = venv/'bin/python'
        marker = json.loads((venv/'.knowingmind-managed.json').read_text())
        assert marker['source_sha'] == args.source_sha
        command([py,'-m','pip','install','--no-index','--find-links',bundle/'dependencies',
                 'psycopg[binary]>=3.2,<4'])
        # Re-enter the verified installed interpreter; no checkout import.
        if Path(sys.prefix).resolve() != venv.resolve():
            os.execv(py,[str(py),'-I',__file__,*sys.argv[1:]])
        import psycopg
        from coding_worker_v2.agent.native_cloudflare import native_client, private_file
        from coding_worker_v2.agent.runtime_backup import create_backup, restore_backup
        from coding_worker_v2.agent.ssh_cli import converse
        assert Path(__import__('coding_worker_v2').__file__).is_relative_to(venv)
        report['database_driver']={'version':psycopg.__version__,'installed':True}
        for name in ('kmcw','km'):
            link=home/'.local/bin'/name;target=venv/'bin'/name
            assert target.is_file()
            if link.exists() or link.is_symlink():
                assert link.is_symlink() and link.resolve()==target.resolve()
            else:link.symlink_to(target)
        checkout=home/'kmcp-host-setup'
        assert command(['git','-C',checkout,'rev-parse','HEAD']).strip()==args.source_sha
        inv=json.loads((home/'KnowingMind/state/installations/cloudflare-inventory.json').read_text())
        binding={'account_id':inv['account_id'],
                 'wrangler_project':str(checkout/'cloudflare/kmcp-edge'),
                 'allowed_models':['@cf/ibm-granite/granite-4.0-h-micro']}
        client=native_client(binding)
        cfg=home/'.config/knowingmind'
        chat={'schema':'KMCW_SSH_V1','provider':binding,'model':binding['allowed_models'][0],
              'sessions':str(home/'KnowingMind/state/ssh-sessions')}
        service=configparser.ConfigParser(interpolation=None)
        service.read(home/'.pg_service.conf')
        candidates=[s for s in service.sections() if service[s].get('dbname')=='km_microops'
                    and service[s].get('host','/var/run/postgresql') in ('localhost','127.0.0.1','/var/run/postgresql')]
        assert len(candidates)==1,'EXACT_LOCAL_DATABASE_SERVICE_REQUIRED'
        pg=candidates[0]
        legacy=home/'KnowingMind/workspaces/kmco-github-qc/av15/coding_worker_v2'
        if (legacy/'queue').is_dir():
            base=legacy;report['runtime_scope']='EXISTING_KM_CLI_RUNTIME_QUEUE'
        else:
            base=home/'KnowingMind/state/acceptance/kmcw-native-runtime'
            raise RuntimeError('EXISTING_RUNTIME_QUEUE_REQUIRED')
        catalog=home/'KnowingMind/workspaces/microops-source'
        runtime={'schema':'KMCP_RUNTIME_V1','node_id':'ubuntu-usb',
            'account_id':'posix:'+str(os.getuid()),'mission_id':'KMCP-V5-PRODUCTION',
            'task_id':'SSH-RECOVERY-ACCEPTANCE','workspace':str(checkout),
            'runtime_base':str(base),'kmco_catalog':str(catalog),
            'cloudflare':binding,'postgres_service':pg}
        if any(owner_profile.get(k) != v for k, v in runtime.items()):
            raise RuntimeError('EXISTING_OWNER_PROFILE_RECONCILIATION_REQUIRED')
        if hashlib.sha256(profile.read_bytes()).hexdigest() != owner_digest:
            raise RuntimeError('OWNER_PROFILE_CHANGED_DURING_ACCEPTANCE')
        chat_path = cfg/'kmcw-ssh.json'
        if chat_path.exists() or chat_path.is_symlink():
            if private_file(chat_path) != chat:
                raise RuntimeError('EXISTING_SSH_PROFILE_RECONCILIATION_REQUIRED')
        else:
            private_json(chat_path,chat)
        request='accept-'+os.environ['GITHUB_RUN_ID']
        result=converse(private_file(cfg/'kmcw-ssh.json'),'installation-acceptance',
                        'Reply only OK. Do not claim any commands were executed.',request,client=client)
        again=converse(chat,'installation-acceptance',
                      'Reply only OK. Do not claim any commands were executed.',request,client=client)
        assert result['reply'] and result['execution']=='NOT_REQUESTED' and again['reused']
        report['ssh_chat']={'status':'PASS','config':str(cfg/'kmcw-ssh.json'),
            'session_resume_and_dedup':'PASS','execution':'NOT_REQUESTED',
            'reply_sha256':hashlib.sha256(result['reply'].encode()).hexdigest()}
        file='av15/coding_worker_v2/README.md'
        quote=next(x.strip() for x in (checkout/file).read_text().splitlines() if x.strip())[:500]
        def obj(properties,required):
            return {'type':'object','properties':properties,'required':required,'additionalProperties':True}
        string={'type':'string'}
        graph={'nodes':[
            {'id':'p01','tool':'kmco.p01.evidence','arguments':{'file':file,'quote':quote},'after':[],
             'result_schema':obj({'circuit':{'type':'string','enum':['P01']},'output':obj({'state':{'type':'string','enum':['EXACT_QUOTE_VERIFIED']}},['state'])},['circuit','output'])},
            {'id':'ai','tool':'cf.ai.run','arguments':{'model':binding['allowed_models'][0],
             'prompt':'Reply only OK.','max_tokens':8},'after':['p01'],
             'result_schema':obj({'response':{'type':'string','minLength':1}},['response'])},
            {'id':'memory','tool':'kmco.core.recall','arguments':{'limit':10},'after':['ai'],
             'result_schema':obj({'state':{'type':'string','enum':['DERIVED_UNVERIFIED']}},['state'])}],
            'outputs':{'evidence':{'$ref':'p01#/output/state'},'reply':{'$ref':'ai#/response'},
                       'memory':{'$ref':'memory#/state'}},
            'acceptance':[{'path':'/evidence','schema':{'type':'string','enum':['EXACT_QUOTE_VERIFIED']}},
                          {'path':'/reply','schema':{'type':'string','minLength':1}},
                          {'path':'/memory','schema':{'type':'string','enum':['DERIVED_UNVERIFIED']}}]}
        graph_path=cfg/('recovery-canary-'+os.environ['GITHUB_RUN_ID']+'.graph.json')
        private_json(graph_path,graph)
        raw=command([venv/'bin/kmcw','circuit','--config',profile,'--graph',graph_path,
                     '--command','Verify real P01, provider inference and four-part memory',
                     '--request-id','recovery-'+os.environ['GITHUB_RUN_ID'],'--approve'])
        circuit=json.loads(raw)
        assert circuit['result']['status']=='COMPLETED'
        receipt=circuit['report']['receipt'];receipt_path=Path(receipt['uri'])
        assert hashlib.sha256(receipt_path.read_bytes()).hexdigest()==receipt['sha256']
        report['native_circuit']={'status':'PASS','circuit_id':circuit['report']['circuit_id'],
            'completed_nodes':circuit['report']['completed_nodes'],'receipt':receipt,
            'authentication':circuit['authentication'],'remote_admin_authority':False}
        with psycopg.connect('service='+pg,connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute('SET TRANSACTION READ ONLY')
                cur.execute("SELECT dukkha,samudaya,nirodha,magga,result_sha256 FROM circuit.core_memory WHERE mission_id=%s AND task_id=%s AND operation='kmco.p01.evidence' ORDER BY updated_at DESC LIMIT 1",(runtime['mission_id'],runtime['task_id']))
                observed=cur.fetchone()
                assert observed and observed[1]['cause'] is None and observed[2]['verified'] is False
                assert observed[3]['executed'] is True and observed[4]
                cur.execute('SHOW server_version_num');server_major=int(cur.fetchone()[0])//10000
        report['core_memory_readback']={'status':'PASS','observed_tool':'kmco.p01.evidence',
            'four_parts_present':True,'cause':'NOT_DIAGNOSED','independent_qc':'NOT_RUN'}
        export=base/'state/recovery/exports';export.mkdir(parents=True,exist_ok=True)
        graph_export=export/('graph-'+os.environ['GITHUB_RUN_ID']+'.json')
        command([venv/'bin/kmcw','graph','--config',profile,'--circuit-id',circuit['report']['circuit_id'],
                 '--output',graph_export])
        projection=json.loads(graph_export.read_text())
        assert len(projection['nodes'])==3 and len(projection['edges'])==2
        report['kves_projection']={'status':'PASS','nodes':3,'edges':2,
            'trace_status':projection['trace']['status'],'ui_deployed':False}
        recovery=base/'state/recovery';wheels=recovery/'wheels';wheels.mkdir(parents=True,exist_ok=True)
        for path in bundle.rglob('*.whl'):shutil.copy2(path,wheels/path.name)
        private_json(recovery/'kmcw-ssh.profile.json',chat)
        private_json(recovery/'kmcw-owner-runtime.profile.json',runtime)
        sessions=recovery/'ssh-sessions';sessions.mkdir(exist_ok=True,mode=0o700)
        for path in Path(chat['sessions']).glob('*.json'):
            if path.is_file() and not path.is_symlink():shutil.copy2(path,sessions/path.name)
        private_json(recovery/'cloudflare-resources.json',inv)
        backup_dir=home/'KnowingMind/backups';backup_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        assert backup_dir.stat().st_uid==os.getuid();backup_dir.chmod(0o700)
        backup=create_backup(checkout,base,backup_dir,catalog=catalog,pg_service=pg)
        report['backup']=backup
        tests=home/'KnowingMind/recovery-tests';tests.mkdir(parents=True,exist_ok=True,mode=0o700)
        with tempfile.TemporaryDirectory(prefix='acceptance-',dir=tests) as temp:
            temp=Path(temp);restored=temp/'restored'
            restored_receipt=restore_backup(backup['archive'],restored)
            repo=temp/'workspace'
            command(['git','clone','--quiet',restored/'recovery/source.bundle',repo])
            command(['git','-C',repo,'checkout','--quiet','--detach',args.source_sha])
            assert command(['git','-C',repo,'rev-parse','HEAD']).strip()==args.source_sha
            if (restored/'recovery/tracked-changes.patch').is_file():
                command(['git','-C',repo,'apply','--binary',restored/'recovery/tracked-changes.patch'])
            restored_venv=temp/'venv';command([sys.executable,'-m','venv',restored_venv])
            wheel_root=restored/'runtime/state/recovery/wheels'
            package=next(wheel_root.glob('knowingmind_kmcp-*.whl'))
            command([restored_venv/'bin/python','-m','pip','install','--no-index',
                     '--find-links',wheel_root,package,'psycopg[binary]>=3.2,<4'])
            command([restored_venv/'bin/kmcw','--help']);command([restored_venv/'bin/kmcp','--help'])
            report['portable_restore']={**restored_receipt,'source_bundle_checkout':'PASS',
                'offline_wheel_install':'PASS','kmcw_and_kmcp_entrypoints':'PASS'}
            # Restore the real dump into an isolated Unix-socket-only cluster.
            # This is a test fixture, never a project SSOT or production target.
            pg_bin=Path('/usr/lib/postgresql')/str(server_major)/'bin'
            socket=temp/'pg-socket';socket.mkdir(mode=0o700);data=temp/'pg-data'
            command([pg_bin/'initdb','-D',data,'--auth-local=trust','--auth-host=reject',
                     '--no-locale','--encoding=UTF8'])
            started=False
            try:
                opts=shlex.join(['-k',str(socket),'-p','55439','-h','',
                                 '-c','shared_buffers=16MB','-c','max_connections=10'])
                command([pg_bin/'pg_ctl','-D',data,'-l',temp/'postgres.log','-o',opts,'-w','start'])
                started=True
                command([pg_bin/'createdb','-h',socket,'-p','55439','km_microops'])
                command([pg_bin/'pg_restore','--exit-on-error','--no-owner','--no-acl',
                         '-h',socket,'-p','55439','-d','km_microops',restored/'recovery/km_microops.dump'],timeout=300)
                with psycopg.connect(host=str(socket),port=55439,dbname='km_microops') as conn:
                    with conn.cursor() as cur:
                        cur.execute('SELECT result_sha256 FROM circuit.core_memory WHERE mission_id=%s AND task_id=%s AND operation=%s ORDER BY updated_at DESC LIMIT 1',
                                    (runtime['mission_id'],runtime['task_id'],'kmco.p01.evidence'))
                        assert cur.fetchone()==(observed[4],)
                report['postgres_restore']={'status':'PASS','target':'ISOLATED_TEMPORARY_CLUSTER',
                    'network':'PRIVATE_UNIX_SOCKET_ONLY','source_record_hash_matches':True,
                    'production_database_modified':False}
            finally:
                if started:command([pg_bin/'pg_ctl','-D',data,'-m','fast','-w','stop'])
        disks=json.loads(command(['lsblk','-J','-o','NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,TRAN']))
        report['storage_observation']=disks
        report['status']='NATIVE_RUNTIME_CHAT_CORE_BACKUP_RESTORE_ACCEPTANCE_PASS'
    except Exception as exc:
        report['status']='PARTIAL_WITH_EXPLICIT_BLOCKER'
        report['blocker']={'type':type(exc).__name__,
            'reason':str(exc)[:240] if isinstance(exc,(RuntimeError,AssertionError)) else getattr(exc,'reason',type(exc).__name__)}
        raise
    finally:
        report['completed_at']=datetime.now(timezone.utc).isoformat()
        target=home/'KnowingMind/state/installations/kmcw-native-recovery.json'
        private_json(target,report)
        Path(args.receipt).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a', encoding='utf-8') as stream:
                stream.write('## Native circuit — observed execution\n\n')
                stream.write('| Stage | Result |\n|---|---|\n')
                for stage in ('ssh_chat','native_circuit','core_memory_readback','kves_projection','portable_restore','postgres_restore'):
                    value = report.get(stage)
                    status = value.get('status', 'RECORDED') if isinstance(value, dict) else 'NOT_RUN'
                    stream.write(f'| {stage} | {status} |\n')
                stream.write('\nProduction and independent QC are not attested by this executor.\n')


if __name__=='__main__':main()
