#!/usr/bin/env python3
"""Fixed provider/route readbacks; no orders, prompts, messages or deployment."""
import hashlib
import json
import os
import shutil
from datetime import datetime,timezone
from pathlib import Path
from urllib import error,request
from coding_worker_v2.agent.native_cloudflare import native_client,private_file

assert os.environ.get('RUNNER_NAME')=='ubuntu-usb' and os.getuid()!=0
home=Path.home();config=private_file(home/'.config/knowingmind/kmcw-owner-runtime.json')
assert config['account_id']=='posix:'+str(os.getuid())
client=native_client(config['cloudflare'])
report={'schema':'KEBUNATE_TRADER_CHANNEL_READBACK_V1','observed_at':datetime.now(timezone.utc).isoformat(),
        'read_only':True,'orders_sent':False,'messages_sent':False,'deployment_changed':False,'workers':{}}
fs=os.statvfs(home)
report['host_storage_capabilities']={'free_bytes':fs.f_bavail*fs.f_frsize,
    'inodes_total':fs.f_files,'inodes_free':fs.f_favail,
    'fuse_device_present':Path('/dev/fuse').exists(),
    'fuse_device_accessible':os.access('/dev/fuse',os.R_OK|os.W_OK),
    'programs':{name:shutil.which(name) for name in ('squashfuse','squashfuse_ll','fusermount3','fusermount','mksquashfs')}}
for worker in ('km-trader-observer','knowingmind-line-webhook'):
    row={};report['workers'][worker]=row
    for field,suffix in [('settings','settings'),('deployments','deployments')]:
        try:
            result=client._call('workers/scripts/'+worker+'/'+suffix)['result']
            if field=='settings':
                row['bindings']=[{k:b[k] for k in ('name','type','service','entrypoint') if k in b}
                                 for b in result.get('bindings',[])]
                # Never retain plain_text or secret binding contents.
            else:
                rows=result if isinstance(result,list) else result.get('deployments',[])
                row['deployments']=[{k:d[k] for k in ('id','created_on','strategy') if k in d} for d in rows[:3]]
        except Exception as exc:row[field+'_error']=getattr(exc,'reason',type(exc).__name__)

class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None
opener=request.build_opener(NoRedirect())
report['routes']=[]
for url in ('https://km-trader-observer.siangoster.workers.dev/health',
            'https://km-trader-observer.siangoster.workers.dev/v1/market/btcusdt',
            'https://ai.knowingmind.app/v1/control/trader-observer'):
    row={'url':url,'authenticated':False};report['routes'].append(row)
    try:
        response=opener.open(request.Request(url,headers={'Accept':'application/json','User-Agent':'KnowingMind-owner-runtime-readback/1'}),timeout=20)
        status=response.status;raw=response.read(131073);response.close()
    except error.HTTPError as exc:status=exc.code;raw=exc.read(131073)
    except (error.URLError,OSError,TimeoutError) as exc:
        row['error_type']=type(exc).__name__;continue
    row.update(http_status=status,response_sha256=hashlib.sha256(raw).hexdigest())
    if len(raw)>131072:row['error']='RESPONSE_LIMIT';continue
    try:doc=json.loads(raw)
    except ValueError:row['format']='NON_JSON';continue
    if not isinstance(doc,dict):row['format']='UNEXPECTED_JSON';continue
    row['metadata']={k:doc[k] for k in ('service','status','mode','live_orders_enabled','reason','source','symbol','freshness','observed_at','exchange_timestamp')
                     if k in doc and isinstance(doc[k],(str,int,float,bool,type(None)))}
    if status==200 and url.endswith('/v1/market/btcusdt'):
        row['market_contract']={'positive_mark_price':isinstance(doc.get('mark_price'),(int,float)) and not isinstance(doc.get('mark_price'),bool) and doc['mark_price']>0,
            'read_only':doc.get('mode')=='READ_ONLY' and doc.get('live_orders_enabled') is False,
            'claims_fresh':doc.get('freshness')=='FRESH'}
        # No fabricated price, signed-account probe or inferred profitability.
target=Path(os.environ['RUNNER_TEMP'])/'kmcp-trader-runtime.json'
target.write_text(json.dumps(report,indent=2)+'\n');target.chmod(0o600)
print(json.dumps(report,indent=2))
