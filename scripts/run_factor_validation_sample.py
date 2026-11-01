"""Reproduce the 80-factor frozen synthetic validation via real durable workers."""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from finahinking.research.application_service import ResearchApplicationService
from finahinking.research.contracts import stable_digest
from finahinking.research.factor_library import FactorLibrary
from finahinking.research.factor_validation_sample import SAMPLE_SPEC, ValidationSample


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-root',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--receipt',type=Path)
    args=parser.parse_args()
    component_paths=['src/finahinking/research/factor_validation_sample.py','src/finahinking/research/factor_study_suite.py',
        'src/finahinking/research/application_service.py','src/finahinking/research/factor_library.py','src/finahinking/quant/evaluation/robustness.py']
    source_root=Path(__file__).resolve().parents[1]
    args.state_root.mkdir(parents=True,exist_ok=True)
    producer_file=args.state_root/'producer-component-hashes.json'
    if not producer_file.exists():
        producer_file.write_text(json.dumps({f:hashlib.sha256((source_root/f).read_bytes()).hexdigest() for f in component_paths},indent=2)+'\n')
    producer_hashes=json.loads(producer_file.read_text())
    service=ResearchApplicationService(args.state_root,allow_model_calls=False)
    try:
        sample=ValidationSample();ids=[r['factor_id'] for r in FactorLibrary().entries()]
        results=[];family_ref=None
        service.start()
        for start in range(0,len(ids),20):
            values={'idempotency_key':f'phase25-fixed-sample-{start//20}',
                'family_key':'phase25-fixed-80-validation','factor_ids':ids[start:start+20],
                'question':'固定64标的×756观察日的80因子工程验证，保留全部试验和失败', 'sample_spec':SAMPLE_SPEC}
            study=service.create_factor_suite(values);family_ref=study['spec']['family_ref']
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                study=service.factor_study(study['study_id']);job=study['validation_job']
                if job['status'] in {'completed','failed','cancelled'}:break
                time.sleep(.25)
            if job['status']!='completed':raise RuntimeError(f"batch {start//20} ended {job['status']}: {job.get('failure_kind')}")
            result=study['validation']['suite_validation']
            results.append({'study_id':study['study_id'],'spec_ref':study['spec_ref'],
                'job_id':job['job_id'],'run_id':job['run_id'],'manifest_ref':stable_digest(service.runs.get_run(job['run_id'])['manifest']),
                'report_status':job['report_status'],'suite':result})
            print(json.dumps({'batch':start//20,'status_counts':result['status_counts']},ensure_ascii=False),flush=True)
        family=service.factor_suite_family(family_ref)
        trials=[r for batch in results for r in batch['suite']['trials']]
        if len(trials)!=80 or len({r['factor_id'] for r in trials})!=80 or family['published_count']!=80:
            raise RuntimeError('incomplete or duplicate factor verification')
        from finahinking.research.research_evidence import build_research_evidence
        evidence=build_research_evidence(family['trial_refs'],catalogue=service.factor_suite_catalogue())
        if evidence['status']!='COMPLETE':raise RuntimeError('published suite evidence validation blocked')
        reader_hashes={f:hashlib.sha256((source_root/f).read_bytes()).hexdigest() for f in
            ['src/finahinking/research/application_service.py','src/finahinking/research/research_evidence.py']}
        payload={'producer_components_sha256':producer_hashes,'reader_validation':{'version':'published-report-integrity-gate.v1',
            'component_sha256':reader_hashes,'status':evidence['status'],'evidence_fingerprint':evidence['fingerprint'],
            'record_count':len(evidence['records']),'blocked_records':sum(bool(r['blockers']) for r in evidence['records'])},'schema_version':'phase25-fixed-sample-verification.v1','sample_spec':sample.spec,
            'sample_fingerprint':sample.fingerprint,'sample_content_digest':sample.content_digest,
            'assets':len(sample.assets),'observations':len(sample.times),'registered_attempts':len(trials),
            'status_counts':{status:sum(t['status']==status for t in trials) for status in ('PASS','FALSIFIED','NEEDS_DATA','EXECUTION_FAILED')},
            'data_mode':'SAMPLE','financial_falsification':False,'family':family,'batches':results}
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        if args.receipt:
            receipt={k:v for k,v in payload.items() if k not in {'batches','family'}}
            receipt['family']={k:v for k,v in family.items() if k!='trial_refs'}
            receipt['records']=[{k:r[k] for k in ('record_id','factor_id','status','fingerprint','data_mode','financial_falsification')} for r in trials]
            receipt['manifests']=[{k:b[k] for k in ('study_id','spec_ref','job_id','run_id','manifest_ref','report_status')} for b in results]
            args.receipt.parent.mkdir(parents=True,exist_ok=True)
            args.receipt.write_text(json.dumps(receipt,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
        print(json.dumps({k:v for k,v in payload.items() if k not in {'batches','family'}},ensure_ascii=False),flush=True)
    finally:service.close()


if __name__=='__main__':main()
