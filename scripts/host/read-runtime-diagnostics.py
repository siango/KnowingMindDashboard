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


def main():
    assert os.environ.get('RUNNER_NAME')=='ubuntu-usb' and os.getuid()!=0
    home=Path.home(); report={'schema':'KEBUNATE_NATIVE_DIAGNOSTIC_V1',
        'read_only':True,'circuit_replayed':False,'provider_inference_requested':False,
        'free_bytes':shutil.disk_usage(home).free,'circuits':[]}
    profile=home/'.config/knowingmind/kmcw-owner-runtime.json'
    if profile.is_file():
        config=json.loads(profile.read_text())
        assert config['account_id']=='posix:'+str(os.getuid())
        base=Path(config['runtime_base'])
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
