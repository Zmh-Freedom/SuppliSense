"""Real server PG/Mongo checks, confined to assessment_stage2_test."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4

import pytest

from app.core.config import settings
from app.core.errors import DomainError
from app.db.init_pg import ensure_pg_schema
from app.db.postgres import get_cursor
from app.domains.agent_run.coordinator import RunCoordinator, execution_scope, ExecutionLost
from app.domains.agent_run.state_store import SessionStateStore
from app.domains.risk import assessment_repo as repo
from app.domains.risk.assessment_policy import current_policy

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def ledger_schema():
    if settings.PG_DB != 'assessment_stage2_test':
        pytest.skip('requires isolated remote assessment_stage2_test database')
    ensure_pg_schema()


@pytest.fixture
def assessment_factory(ledger_schema):
    actor, session = str(uuid4()), str(uuid4())
    companies = [str(uuid4()), str(uuid4())]
    with get_cursor() as (_, cur):
        cur.execute("INSERT INTO users(id,username,email,password_hash,role) VALUES(%s,%s,%s,'test','purchaser')",(actor,actor,actor+'@test.invalid'))
        for company in companies:
            cur.execute("INSERT INTO companies(id,legal_name,normalized_name,verification_status,identity_source,source_reference) VALUES(%s,'同名公司','同名公司','verified','import',%s)", (company,'fixture:'+company))
    run = str(SessionStateStore().start_harness_turn(session, actor, '评估')['run'].id)
    permit = RunCoordinator().claim(run, 'assessment-test')
    yield {'actor': actor, 'companies': companies, 'run': run, 'permit': permit}
    with get_cursor() as (_, cur):
        cur.execute("DELETE FROM outbox_events WHERE aggregate_id IN (SELECT id FROM assessment_history WHERE actor_id=%s)",(actor,))
        cur.execute("DELETE FROM assessment_history WHERE actor_id=%s",(actor,))
        cur.execute('DELETE FROM agent_sessions WHERE id=%s',(session,))
        for company in companies:
            cur.execute('DELETE FROM companies WHERE id=%s',(company,))
        cur.execute('DELETE FROM users WHERE id=%s',(actor,))


def identity(fixture, index=0, **extra):
    return {'company_id': fixture['companies'][index], 'company_name': '同名公司', 'dimensions': ['financial'], 'purchase_scope': 'category:a', **extra}


def begin(fixture, index=0, **extra):
    return repo.begin_assessment(identity(fixture,index), actor_id=fixture['actor'], request_key='request:'+str(index), policy=current_policy(['financial']), **extra)


def test_retry_preserves_id_and_original_policy(assessment_factory):
    f=assessment_factory
    first=begin(f)
    changed={**current_policy(['financial']), 'version':'future', 'weights': {'financial':99}}
    second=repo.begin_assessment(identity(f), actor_id=f['actor'], request_key='request:0', policy=changed)
    assert first['id']==second['id']
    assert first['policy_snapshot']==second['policy_snapshot']
    assert first['assessment_version']==second['assessment_version']


def test_same_idempotency_key_rejects_changed_company_or_scope(assessment_factory):
    f=assessment_factory
    begin(f)
    with pytest.raises(DomainError):
        repo.begin_assessment(identity(f,1),actor_id=f['actor'],request_key='request:0',policy=current_policy(['financial']))
    with pytest.raises(DomainError):
        repo.begin_assessment(identity(f,purchase_scope='category:b'),actor_id=f['actor'],request_key='request:0',policy=current_policy(['financial']))


def test_one_run_supports_two_companies_and_keeps_scope_isolation(assessment_factory):
    f=assessment_factory
    with execution_scope(f['permit']):
        a=repo.begin_assessment(identity(f), actor_id=f['actor'],run_id=f['run'],logical_key='task:a',policy=current_policy(['financial']))
        b=repo.begin_assessment(identity(f,1),actor_id=f['actor'],run_id=f['run'],logical_key='task:b',policy=current_policy(['financial']))
        again=repo.begin_assessment(identity(f),actor_id=f['actor'],run_id=f['run'],logical_key='task:a',policy=current_policy(['financial']))
    assert a['id']!=b['id']
    assert again['id']==a['id']
    assert repo.get_assessment(a['id'],str(uuid4())) is None


def test_concurrent_begin_allocates_distinct_versions_and_one_predecessor_chain(assessment_factory):
    f=assessment_factory
    barrier=Barrier(2)
    def create(index):
        barrier.wait()
        return repo.begin_assessment(identity(f),actor_id=f['actor'],request_key='parallel:'+str(index),policy=current_policy(['financial']))
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows=list(pool.map(create,[0,1]))
    rows.sort(key=lambda row: row['assessment_version'])
    assert rows[1]['assessment_version']==rows[0]['assessment_version']+1
    assert rows[1]['previous_assessment_id']==rows[0]['id']


def test_freeze_cannot_replace_committed_evidence(assessment_factory):
    row=begin(assessment_factory)
    first=repo.freeze_assessment(row['id'],row['collector_token'],inputs={'request':{'value':1}},manifest=[])
    again=repo.freeze_assessment(row['id'],row['collector_token'],inputs={'request':{'value':1}},manifest=[])
    assert first['input_digest']==again['input_digest']
    with pytest.raises(DomainError):
        repo.freeze_assessment(row['id'],row['collector_token'],inputs={'request':{'value':2}},manifest=[])


def test_cancelled_attempt_cannot_commit_assessment(assessment_factory):
    f=assessment_factory
    with execution_scope(f['permit']):
        row=repo.begin_assessment(identity(f),actor_id=f['actor'],run_id=f['run'],logical_key='task:risk',policy=current_policy(['financial']))
        repo.freeze_assessment(row['id'],row['collector_token'],inputs={},manifest=[])
        RunCoordinator().cancel(f['run'],f['actor'])
        with pytest.raises(ExecutionLost):
            repo.commit_assessment(row['id'],row['collector_token'],result={'risk_score':70,'risk_level':'低风险'},quality='partial')



def test_legacy_uuid_and_unknown_evidence_are_preserved(assessment_factory):
    legacy_id=str(uuid4())
    with get_cursor() as (_,cur):
        cur.execute("INSERT INTO assessment_history(id,actor_id,company_name,risk_score,risk_level) VALUES(%s,%s,'旧记录',88,'低风险')",(legacy_id,assessment_factory['actor']))
    row=repo.get_assessment(legacy_id,assessment_factory['actor'])
    assert row['id']==legacy_id and row['risk_score']==88
    assert row['assessment_status']=='legacy_unverified'
    assert row['policy_hash'] is None and row['input_digest'] is None
    assert row['evidence_manifest']==[]


def test_same_policy_version_cannot_be_redefined_for_new_requests(assessment_factory):
    f=assessment_factory
    begin(f)
    policy={**current_policy(['financial']), 'missing_policy':'zero_risk'}
    with pytest.raises(DomainError,match='政策版本'):
        repo.begin_assessment(identity(f),actor_id=f['actor'],request_key='new-policy-conflict',policy=policy)


def test_concurrent_same_request_has_one_identity_and_collector(assessment_factory):
    f=assessment_factory
    barrier=Barrier(2)
    def create(_):
        barrier.wait()
        return begin(f)
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows=list(pool.map(create,[0,1]))
    assert rows[0]['id']==rows[1]['id']
    assert sum(row['acquired'] for row in rows)==1


def test_result_and_outbox_commit_once_without_overwriting_original(assessment_factory):
    row=begin(assessment_factory)
    repo.freeze_assessment(row['id'],row['collector_token'],inputs={'data':'frozen'},manifest=[])
    result={'risk_score':72,'risk_level':'低风险','score_breakdown':{}}
    a=repo.commit_assessment(row['id'],row['collector_token'],result=result,quality='partial')
    b=repo.commit_assessment(row['id'],row['collector_token'],result=result,quality='partial')
    assert a['assessed_at']==b['assessed_at']
    with pytest.raises(DomainError):
        repo.commit_assessment(row['id'],row['collector_token'],result={**result,'risk_score':99},quality='partial')
    with get_cursor() as (_,cur):
        cur.execute("SELECT COUNT(*) FROM outbox_events WHERE aggregate_id=%s AND event_type='risk.assessment.committed'",(row['id'],))
        assert cur.fetchone()[0]==1


def test_outbox_insert_failure_rolls_back_assessment_result(assessment_factory,monkeypatch):
    row=begin(assessment_factory)
    repo.freeze_assessment(row['id'],row['collector_token'],inputs={},manifest=[])
    monkeypatch.setattr(repo,'enqueue_event',lambda *_args,**_kwargs: (_ for _ in ()).throw(RuntimeError('outbox failed')))
    with pytest.raises(RuntimeError,match='outbox failed'):
        repo.commit_assessment(row['id'],row['collector_token'],result={'risk_score':80},quality='partial')
    current=repo.get_assessment(row['id'],assessment_factory['actor'])
    assert current['assessment_status']=='frozen' and current['result'] is None
