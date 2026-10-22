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
