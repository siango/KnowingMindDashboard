'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const {KebunateCircuit,creativeGraph} = require('./KebunateCircuit.node.js');

test('graph uses original three registered tools and typed dependency chain',()=>{
  const graph=creativeGraph('/scoped/output/canary.png');
  assert.deepEqual(graph.nodes.map(n=>n.tool),[
    'creative.photocraft.create','creative.photocraft.inspect','creative.photocraft.export']);
  assert.deepEqual(graph.nodes[2].after,['inspect']);
  assert.equal(graph.nodes[0].arguments.width,64);
  assert.equal(graph.nodes[2].arguments.path,'/scoped/output/canary.png');
});
test('node exposes one fixed operation with no arbitrary executable or graph input',()=>{
  const node=new KebunateCircuit();
  assert.deepEqual(node.description.properties.map(p=>p.name),['operation']);
  assert.equal(node.description.properties[0].options.length,1);
});
test('item budget rejects before touching files or subprocesses',async()=>{
  await assert.rejects(KebunateCircuit.prototype.execute.call({getInputData:()=>[1,2,3,4,5]}),/ITEM_LIMIT/);
});
test('unregistered operation rejects before touching files or subprocesses',async()=>{
  await assert.rejects(KebunateCircuit.prototype.execute.call({
    getInputData:()=>[{}],getNodeParameter:()=> 'shell'}),/OPERATION_DENIED/);
});
