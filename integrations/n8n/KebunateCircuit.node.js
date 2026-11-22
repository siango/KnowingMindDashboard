'use strict';
// Local, operator-installed n8n node. No shell command, URL or tool name input.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const run = promisify(execFile);

function creativeGraph(output) {
  return {
    nodes: [
      {id:'create',tool:'creative.photocraft.create',
       arguments:{name:'KEBUNATE local circuit',width:64,height:64,background:'#204060'},
       after:[],result_schema:{type:'object'}},
      {id:'inspect',tool:'creative.photocraft.inspect',arguments:{},after:['create'],
       result_schema:{type:'object'}},
      {id:'export',tool:'creative.photocraft.export',arguments:{path:output},
       after:['inspect'],result_schema:{type:'object'}},
    ],
    outputs:{exported:{$ref:'export#'}},
    acceptance:[{path:'/exported',schema:{type:'object'}}],
  };
}

class KebunateCircuit {
  constructor() {
    this.description = {
      displayName:'KEBUNATE Circuit',name:'kebunateCircuit',group:['transform'],version:1,
      description:'Run the fixed native-owner PhotoCraft circuit through existing KMCP/KMCW',
      defaults:{name:'KEBUNATE Circuit'},inputs:['main'],outputs:['main'],
      properties:[{displayName:'Operation',name:'operation',type:'options',default:'creativeCanary',
        options:[{name:'Create, Inspect and Export 64px PNG',value:'creativeCanary'}]}],
    };
  }
  async execute() {
    const results = [];
    const items = this.getInputData();
    if (items.length > 4) throw new Error('KEBUNATE_ITEM_LIMIT');
    for (let index=0;index<items.length;index++) {
      if (this.getNodeParameter('operation',index) !== 'creativeCanary')
        throw new Error('KEBUNATE_OPERATION_DENIED');
      const id = 'n8n-' + String(this.getExecutionId()) + '-' + index;
      if (!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(id)) throw new Error('KEBUNATE_EXECUTION_ID_INVALID');
      const home = os.homedir();
      const root = path.join(home,'.local/share/knowingmind/n8n/graphs');
      fs.mkdirSync(root,{recursive:true,mode:0o700});
      const output = path.join(home,'.local/share/knowingmind/artcraft/output',id+'.png');
      // Existing output and ambiguous native requests must never be replayed.
      if (fs.existsSync(output)) throw new Error('KEBUNATE_ARTIFACT_ALREADY_EXISTS');
      const graphPath = path.join(root,id+'.json');
      fs.writeFileSync(graphPath,JSON.stringify(creativeGraph(output)),{mode:0o600,flag:'wx'});
      const cli = path.join(home,'KnowingMind/.venvs/kmcp-portable/bin/kmcw');
      const profile = path.join(home,'.config/knowingmind/n8n-runtime.json');
      let stdout;
      try {
        ({stdout} = await run(cli,['circuit','--config',profile,'--graph',graphPath,
          '--command','n8n fixed local PhotoCraft create-inspect-export circuit',
          '--request-id',id,'--approve'],{
          timeout:120000,maxBuffer:1048576,
          env:{HOME:home,PATH:process.env.PATH,LANG:'C.UTF-8',PYTHONUTF8:'1'},
        }));
      } catch (_) {throw new Error('KEBUNATE_CIRCUIT_BLOCKED_READ_NATIVE_RECEIPT');}
      let receipt;
      try {receipt=JSON.parse(stdout);} catch (_) {throw new Error('KEBUNATE_RECEIPT_INVALID');}
      if (receipt.result?.status !== 'COMPLETED') throw new Error('KEBUNATE_CIRCUIT_NOT_COMPLETE');
      const info = fs.lstatSync(output);
      if (!info.isFile() || info.isSymbolicLink() || info.size > 1048576)
        throw new Error('KEBUNATE_ARTIFACT_INVALID');
      const raw = fs.readFileSync(output);
      if (raw.length < 24 || raw.subarray(0,8).toString('hex') !== '89504e470d0a1a0a'
          || raw.readUInt32BE(16)!==64 || raw.readUInt32BE(20)!==64)
        throw new Error('KEBUNATE_PNG_CONTRACT_FAILED');
      results.push({json:{schema:'KEBUNATE_N8N_ARTCRAFT_RECEIPT_V1',
        status:'COMPLETED',artifact:output,sha256:crypto.createHash('sha256').update(raw).digest('hex'),
        width:64,height:64,circuit_id:receipt.report.circuit_id,
        receipt:receipt.report.receipt,independent_qc:'NOT_RUN'},pairedItem:{item:index}});
    }
    return [results];
  }
}
module.exports={KebunateCircuit,creativeGraph};
