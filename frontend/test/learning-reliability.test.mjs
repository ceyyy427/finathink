import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeAssessment, normalizePair, selectionGuard } from '../src/learning-reliability.js';
import { normalizeJobSummary } from '../src/run-workspace.js';

const assessment = { assessment_id: `assessment-${'a'.repeat(64)}`, status:'OBSERVE', paper_only:true, sample_count:1, minimum_samples:20, selected_review_ids:['one'], statistics:{mean:-.01}, exclusions:[], counterexamples:[], limits:[] };
test('cohort summaries preserve absence and cannot claim readiness with too few windows', () => {
  assert.equal(normalizeAssessment(assessment), assessment);
  assert.throws(() => normalizeAssessment({...assessment,status:'RECOMMENDED'}));
  assert.throws(() => normalizeAssessment({...assessment,paper_only:false}));
  assert.throws(() => normalizeAssessment({...assessment,statistics:{mean:NaN}}));
});
test('late operations lose ownership on input events and silent option changes', () => {
  let selected='first'; const guard=selectionGuard(()=>selected);
  const one=guard.begin(); selected='second'; assert.equal(one(),false);
  const two=guard.begin(); guard.invalidate(); assert.equal(two(),false);
  const three=guard.begin(); assert.equal(three(),true);
});
test('pair success requires two complete verified children', () => {
  const value={pair_id:`pair-${'b'.repeat(64)}`,paper_only:true,status:'AVAILABLE',children:{left:{status:'completed',report_status:'AVAILABLE'},right:{status:'completed',report_status:'AVAILABLE'}},metrics:[]};
  assert.equal(normalizePair(value),value);
  assert.throws(()=>normalizePair({...value,children:{left:value.children.left}}));
  assert.throws(()=>normalizePair({...value,metrics:[{left:1,right:2,delta:NaN}]}));
});
test('history metadata does not masquerade as a verified job', () => {
  const job={job_id:'job-1',run_id:'research-1',status:'completed',paper_only:true,report_status:'NOT_CHECKED',report_url:null,run_url:'/research/research-1'};
  assert.equal(normalizeJobSummary(job),job);
  assert.throws(()=>normalizeJobSummary({...job,report_status:'AVAILABLE'}));
});

test('learning catalogue coalesces concurrent reads and releases settled responses', async () => {
  const { readLearningCatalogue } = await import('../src/learning-reliability.js');
  let calls=0, release;
  const fetcher=async()=>{calls++;await new Promise(resolve=>{release=resolve;});return {ok:true,json:async()=>({reviews:[]})};};
  const first=readLearningCatalogue(fetcher,'/api/research/learning');
  const second=readLearningCatalogue(fetcher,'/api/research/learning');
  assert.equal(calls,1);release();await Promise.all([first,second]);
  const next=readLearningCatalogue(fetcher,'/api/research/learning');assert.equal(calls,2);release();await next;
});
