"""Real Harness + real operational tool; all source/state boundaries in memory."""
import asyncio

import pytest

from app.domains.risk.tools_risk import assess_operational_risk
from app.graphs.harness import run_harness
from app.tools.contracts import OperationalRiskOutput
from app.tools.executor import ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec
from tests.operational_metric_fixtures import install, transaction

pytestmark = pytest.mark.agent_e2e


def run_operational(dimensions):
    registry = ToolRegistry()
    registry.register(assess_operational_risk, ToolSpec(name=assess_operational_risk.name,
        capability='operational_risk', side_effect='read', approval_policy='none'), output_model=OperationalRiskOutput)
    return asyncio.run(run_harness({
        'session_id':'operational-session','turn_id':'operational-turn','run_id':'operational-run',
        'user_message':'查看甲公司质量和交付', 'execution_context':{'references':[]},
        'current_task':{'task_id':'operational-task','task_type':'analysis',
                        'target_supplier_names':['甲公司'],'analysis_dimensions':dimensions,
                        'user_message':'查看甲公司' + ('质量' if dimensions == ['quality'] else '交付' if dimensions == ['delivery'] else '质量和交付')},
        'budget':{'max_tool_calls':4},
    }, executor=ToolExecutor(registry)))


@pytest.mark.parametrize('dimensions', [['quality'],['delivery'],['quality','delivery']])
def test_harness_summarizes_real_metrics_without_formal_grades(monkeypatch, dimensions):
    install(monkeypatch, [transaction(on_time_rate=0.7, inspected=1000)])
    result = run_operational(dimensions)
    answer = result['answer']
    assert answer['claims']
    assert all(item['fact_path'] not in {'risk_score','risk_level'} for item in answer['claims'])
    if 'quality' in dimensions:
        assert '95.2%' in answer['summary']
    if 'delivery' in dimensions:
        assert '70.0%' in answer['summary']
    assert any('评级政策尚未确认' in text for text in answer['limitations'])
    assert '没有可验证' not in answer['summary']
    assert '安全评分' not in answer['summary']


def test_harness_reports_source_gaps_without_claiming_safety(monkeypatch):
    install(monkeypatch, configured=False)
    answer = run_operational(['quality'])['answer']
    assert answer['status'] != 'completed'
    assert not answer['claims']
    assert '没有可验证' in answer['summary'] or '未覆盖' in answer['summary']


def test_harness_evidence_has_recomputable_counts(monkeypatch):
    install(monkeypatch, [transaction(inspected=100,quality_rate=0.5),
                          transaction(source_record_id='r2',batch='B2',inspected=9900,quality_rate=1)])
    result = run_operational(['quality'])
    facts = result['evidence_records'][0]['facts']
    assert facts['quality_rate'] == 0.995
    assert sum(item['numerator'] for item in facts['source_observations']) == 9950
    assert sum(item['denominator'] for item in facts['source_observations']) == 10000
    assert '99.5%' in result['answer']['summary']


def test_unauthorized_user_cannot_read_operational_data(monkeypatch):
    from types import SimpleNamespace
    from app.domains.auth import service as auth
    from app.domains.supplier import access
    from app.domains.risk import operational_risk_service as operational
    monkeypatch.setattr(access,'formal_supplier_id_by_name',lambda name: 'supplier-1')
    monkeypatch.setattr(access,'can_access_supplier',lambda *args: False)
    monkeypatch.setattr(auth,'get_user_by_id',lambda user: SimpleNamespace(role=SimpleNamespace(value='purchaser')))
    def forbidden():
        raise AssertionError('Denied request reached transaction data')
    monkeypatch.setattr(operational,'get_db',forbidden)
    registry = ToolRegistry()
    registry.register(assess_operational_risk, ToolSpec(name=assess_operational_risk.name,
        capability='operational_risk',side_effect='read',approval_policy='none'),output_model=OperationalRiskOutput)
    from app.tools.executor import ToolContext
    outcome = asyncio.run(ToolExecutor(registry).execute('assess_operational_risk',
        {'company_name':'甲公司','dimension':'quality'},ToolContext(user_id='buyer-other')))
    assert outcome.status == 'denied'
    assert not outcome.data.get('claims')


def public_fixture(monkeypatch):
    """Generate the actual contract; normalize only volatile collection timestamps."""
    install(monkeypatch,[transaction(on_time_rate=0.7,inspected=1000)])
    result = run_operational(['quality','delivery'])
    records = result['evidence_records']
    for record in records:
        record['collected_at'] = '2026-10-22T00:00:00Z'
    return {'answer':result['answer'],'evidence':records}


def test_actual_harness_contract_matches_frontend_fixture(monkeypatch):
    import json
    from pathlib import Path
    actual = public_fixture(monkeypatch)
    fixture = Path(__file__).resolve().parents[2] / 'frontend/src/__tests__/fixtures/operationalMetricAnswer.json'
    assert fixture.exists(), 'Generate fixture from public_fixture, not by hand'
    assert actual == json.loads(fixture.read_text())
