import asyncio
from datetime import datetime, timezone

import pytest

from app.domains.risk.tools_risk import assess_operational_risk
from app.graphs.agent_core.evidence_ledger import Claim, EvidenceLedger, EvidenceRecord
from app.tools.executor import ToolContext, ToolExecutor
from app.tools.registry import ToolRegistry, ToolSpec
from tests.operational_metric_fixtures import install, transaction


def execute(dimension='quality', entity_id='company:1'):
    registry = ToolRegistry()
    registry.register(assess_operational_risk, ToolSpec(name=assess_operational_risk.name,
        capability='operational_risk', side_effect='read', approval_policy='none'))
    return asyncio.run(ToolExecutor(registry).execute('assess_operational_risk',
        {'company_name':'甲公司','dimension':dimension}, ToolContext(entity_id=entity_id)))


def test_metric_claim_is_supported_by_its_actual_fact(monkeypatch):
    install(monkeypatch)
    output = execute()
    assert output.status in {'success','partial'}
    records = [EvidenceRecord.model_validate(item) for item in output.data['evidence_records']]
    claims = [Claim.model_validate(item) for item in output.data['claims']]
    rate = next(claim for claim in claims if claim.fact_path == 'quality_rate')
    assert rate.value == 0.952 and rate.unit == 'ratio'
    assert '95.2%' in rate.statement
    assert all(claim.fact_path not in {'risk_score','risk_level'} for claim in claims)
    ledger = EvidenceLedger(records)
    assert ledger.validate_claim(rate).claim.validation_status == 'supported'
    assert ledger.validate_claim(rate.model_copy(update={'entity_id':'company:other'})).claim.validation_status == 'unsupported'
    assert ledger.validate_claim(rate.model_copy(update={'dimension':'delivery'})).claim.validation_status == 'unsupported'
    assert records[0].collected_at >= datetime(2026,10,1,tzinfo=timezone.utc)
    assert records[0].facts['observed_at'] == '2026-10-01T00:00:00+00:00'


def test_multiple_unweighted_facts_are_bound_separately(monkeypatch):
    install(monkeypatch, [transaction(), transaction(source_record_id='r2', batch='B2', quality_rate=0.8)])
    output = execute()
    claims = [item for item in output.data['claims'] if item['fact_path'] == 'quality_rate']
    assert sorted(item['value'] for item in claims) == [0.8,0.952]
    assert len({item['evidence_refs'][0] for item in claims}) == 2
    assert all(EvidenceLedger([EvidenceRecord.model_validate(r) for r in output.data['evidence_records']])
        .validate_claim(Claim.model_validate(c)).claim.validation_status == 'supported' for c in claims)


@pytest.mark.parametrize('change,expected', [(dict(observed_at='2019-12-01T00:00:00Z', valid_until='2020-01-01T00:00:00Z'),'stale'),
    (dict(observed_at=None),'unavailable'), (dict(valid_until=None),'unavailable')])
def test_missing_or_expired_source_time_never_becomes_current(monkeypatch, change, expected):
    install(monkeypatch, [transaction(**change)])
    output = execute()
    records = output.data.get('evidence_records', [])
    assert records and records[0]['status'] == expected
    ledger = EvidenceLedger([EvidenceRecord.model_validate(r) for r in records])
    assert all(ledger.validate_claim(Claim.model_validate(c)).claim.validation_status != 'supported'
               for c in output.data.get('claims', []))


def test_source_without_formal_contract_has_no_positive_claim(monkeypatch):
    install(monkeypatch, configured=False)
    output = execute()
    assert output.status == 'unavailable'
    assert not output.data.get('claims')
