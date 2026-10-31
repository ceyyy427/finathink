"""Quality rates expose their denominators and never infer absent outcomes."""
from copy import deepcopy

from finahinking.research.context_bundle import build_context_bundle
from tests.research.test_grounded_driver import setup_driver


def example():
    req,driver,_=setup_driver()
    contexts={role:build_context_bundle(req,driver.snapshot,role) for role in ('technical','fundamentals','sentiment','news','learning','research')}
    expected={role:'READY' if role=='technical' else 'INSUFFICIENT_DATA' for role in ('technical','fundamentals','sentiment','news','learning')}
    ref=next(iter(contexts['technical'].evidence_index))
    roles=[{'role':role,'status':status,'claims':[],'evidence_refs':[ref] if role=='technical' else [],'limitations':[]} for role,status in expected.items()]
    call={'role':'technical','model':'named-model','status':'READY','dispatched':True,'usage':{'total_units':0},'duration_ms':0}
    return {'model_research':{'roles':roles,'calls':[call],'synthesis':{'disagreements':['Keep the recorded disagreement.']}}},contexts,expected


def score(job,contexts,expected):
    from finahinking.research.quality_evaluation import score_quality
    return score_quality(job,contexts,expected)


def test_known_zero_and_insufficient_roles_keep_independent_denominators():
    job,contexts,expected=example()
    result=score(job,contexts,expected)
    assert result['schema_acceptance']=={'attempted':1,'accepted':1,'unknown':0,'not_sent':0,'rate':1.0}
    assert result['citations']=={'valid':1,'observed':1,'rate':1.0}
    assert result['insufficient_data']=={'correct':4,'observed':4,'expected':4,'rate':1.0}
    assert result['usage']=={'known_total_units':0,'unknown_calls':0,'total_units':0}
    assert result['model_duration_ms']==0 and result['estimated_cost'] is None
    assert result['disagreements']==['Keep the recorded disagreement.']
    renamed=deepcopy(job)
    renamed['model_research']['calls'][0]['model']='best-labelled-model'
    assert score(renamed,contexts,expected)==result


def test_failed_and_unknown_calls_are_not_filtered_to_inflate_quality():
    job,contexts,expected=example()
    calls=job['model_research']['calls']
    calls.append({**calls[0],'role':'news','status':'SCHEMA_ERROR','usage':None,'duration_ms':10})
    result=score(job,contexts,expected)
    assert result['schema_acceptance']['attempted']==2 and result['schema_acceptance']['rate']==.5
    assert result['usage']['unknown_calls']==1 and result['usage']['total_units'] is None
    calls.append({**calls[0],'role':'research','status':'UNKNOWN','usage':None,'duration_ms':None})
    result=score(job,contexts,expected)
    assert result['schema_acceptance']['unknown']==1 and result['schema_acceptance']['rate'] is None
    assert result['model_duration_ms'] is None and result['usage']['total_units'] is None


def test_no_attempts_or_missing_reports_do_not_get_zero_or_perfect_rates():
    job,contexts,expected=example()
    job['model_research']['calls']=[{'role':'technical','status':'NOT_SENT','dispatched':False,'usage':None,'duration_ms':None}]
    job['model_research']['roles']=[]
    result=score(job,contexts,expected)
    assert result['schema_acceptance']['not_sent']==1 and result['schema_acceptance']['rate'] is None
    assert result['citations']['rate'] is None and result['insufficient_data']['rate'] is None
    assert result['usage']['total_units'] is None


def test_foreign_reference_is_not_a_valid_citation():
    job,contexts,expected=example()
    job['model_research']['roles'][0]['evidence_refs']=['evidence:another-run']
    result=score(job,contexts,expected)
    assert result['citations']=={'valid':0,'observed':1,'rate':0.0}
    assert result['schema_acceptance']['rate']==1.0
