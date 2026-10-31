import test from 'node:test';
import assert from 'node:assert/strict';
import { createControlledOperation, safeEvidenceLink, normalizeDiagnostic } from '../src/interface-diagnostics.js';

test('diagnostic boundaries reject invented statuses and external or cross-run evidence', () => {
  const report={schema_version:'interface-diagnostic.v1',receipt_id:'diag-'+ 'a'.repeat(32),status:'UNKNOWN',request_count:null,usage:null};
  assert.equal(normalizeDiagnostic(report).request_count,null);
  assert.throws(()=>normalizeDiagnostic({...report,status:'validated-account'}));
  assert.throws(()=>normalizeDiagnostic({...report,receipt_id:'../../other'}));
  const run='research-a1';
  assert.equal(safeEvidenceLink('/research/'+run+'/report/complete#model-research',run),true);
  for(const href of ['javascript:alert(1)','//evil.com','/research/another/report/complete#source','/research/'+run+'/report/complete#unknown']) assert.equal(safeEvidenceLink(href,run),false);
});

test('duplicate clicks share one request and ambiguous retry retains the key', async () => {
  let finish; const sent=[]; let index=0;
  const operation=createControlledOperation({request:values=>{sent.push(values);return new Promise(resolve=>{finish=resolve;});},keyFactory:()=> 'key-'+(++index)});
  const a=operation.run({config_ref:'desk'}); const b=operation.run({config_ref:'desk'});
  assert.equal(sent.length,1); finish({status:'UNKNOWN'}); await Promise.all([a,b]);
  const c=operation.run({config_ref:'desk'}); assert.equal(sent[1].request_key,sent[0].request_key); finish({status:'UNKNOWN'}); await c;
  operation.newAttempt({config_ref:'desk'});
  const d=operation.run({config_ref:'desk'}); assert.notEqual(sent[2].request_key,sent[0].request_key); finish({}); await d;
});

test('editing inputs discards a late result while keeping its recovery key', async () => {
  let finish;const states=[];const sent=[];
  const operation=createControlledOperation({request:values=>{sent.push(values);return new Promise(resolve=>{finish=resolve;});},onChange:state=>states.push(state),keyFactory:()=> 'stable'});
  const old=operation.run({case_id:'complete'});operation.invalidate();finish({status:'EVALUATED'});
  assert.equal(await old,null);assert.equal(states.includes('READY'),false);
  const retry=operation.run({case_id:'complete'});finish({});await retry;
  assert.equal(sent[0].request_key,sent[1].request_key);
});

test('a local error does not mint a new paid request key',async()=>{
  const keys=[];let index=0;const operation=createControlledOperation({request:async v=>{keys.push(v.request_key);throw new Error('lost response');},keyFactory:()=>String(++index)});
  await assert.rejects(operation.run({case_id:'sparse'}));await assert.rejects(operation.run({case_id:'sparse'}));
  assert.deepEqual(keys,['1','1']);
});

test('session storage failure never dispatches an unpersisted retry',async()=>{
  let blocked=true;let sent=0;const keys=new Map();
  const operation=createControlledOperation({request:async()=>{sent++;return {};},keys,keyFactory:()=> 'stable-key',save:()=>{if(blocked)throw new Error('quota');}});
  await assert.rejects(operation.run({case_id:'complete'}));
  await assert.rejects(operation.run({case_id:'complete'}));
  assert.equal(sent,0);blocked=false;await operation.run({case_id:'complete'});assert.equal(sent,1);
});

test('changed comparison selection suppresses an older response',async()=>{
  const { createComparisonReader }=await import('../src/interface-diagnostics.js');
  let finish;const states=[];
  const reader=createComparisonReader({request:()=>new Promise(resolve=>{finish=resolve;}),onChange:state=>states.push(state)});
  const old=reader.read(['job-a','job-b']);reader.invalidate();finish({comparable:true});
  assert.equal(await old,null);assert.equal(states.at(-1),'STALE');assert.equal(states.includes('READY'),false);
});
