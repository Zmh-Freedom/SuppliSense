import pytest

from app.domains.risk.operational_risk_service import assess_operational_risk
from tests.operational_metric_fixtures import install, transaction


PARTIAL_KEYS = {'assessment_status','dimension','company_name','supplier_code','period',
                'risk_score','risk_level','data_coverage','metrics','evidence'}


def test_metrics_survive_without_an_approved_rating(monkeypatch):
    install(monkeypatch)
    result = assess_operational_risk('甲公司', 'quality')
    assert result['assessment_status'] == 'partial'
    assert result['risk_score'] is None and result['risk_level'] is None
    assert result['metrics']['quality_rate'] == 0.952
    assert set(result) == PARTIAL_KEYS
    assert set(result['evidence'][0]) == {'source','period','data_mode','facts'}


def test_unconfigured_source_does_not_guess_units(monkeypatch):
    install(monkeypatch, configured=False)
    result = assess_operational_risk('甲公司', 'quality')
    assert result['assessment_status'] == 'missing_data'
    assert result['risk_score'] is None
    assert '来源' in result['reason']


def test_latest_bad_month_never_falls_back(monkeypatch):
    install(monkeypatch, [transaction(snapshot_month='2026-09'), transaction(quality_rate=-1)])
    result = assess_operational_risk('甲公司', 'quality')
    assert result['assessment_status'] == 'missing_data'
    assert result['period'] == '2026-10'


@pytest.mark.parametrize('change', [dict(snapshot_month='2026-13'), dict(data_mode='synthetic'),
    dict(sync_status='old'), dict(eligible_for_formal_assessment=False)])
def test_invalid_or_nonformal_snapshot_is_unavailable(monkeypatch, change):
    install(monkeypatch, [transaction(**change)])
    result = assess_operational_risk('甲公司', 'quality')
    assert result['assessment_status'] == 'missing_data'
    assert result['risk_score'] is None


@pytest.mark.parametrize('masters', [[], [{'name':'甲公司','sync_status':'current'}],
    [{'name':'甲公司','supplier_code':'S1','sync_status':'current'},
     {'name':'甲公司','supplier_code':'S2','sync_status':'current'}]])
def test_ambiguous_or_missing_master_is_not_a_source(monkeypatch, masters):
    install(monkeypatch, masters=masters)
    result = assess_operational_risk('甲公司', 'quality')
    assert result['assessment_status'] == 'missing_supplier'
    assert set(result) == {'assessment_status','dimension','company_name','risk_level','risk_score','data_coverage','reason'}


def test_missing_fields_keep_original_missing_branch(monkeypatch):
    install(monkeypatch, [transaction(quality_rate=None)])
    result = assess_operational_risk('甲公司', 'quality')
    assert result['assessment_status'] == 'missing_data'
    assert set(result) == {'assessment_status','dimension','company_name','period','risk_level','risk_score','data_coverage','reason'}


def test_mixed_source_times_do_not_publish_a_current_aggregate(monkeypatch):
    install(monkeypatch,[transaction(inspected=100,quality_rate=0.5),
        transaction(source_record_id='r2',batch='B2',inspected=9900,quality_rate=1,observed_at=None)])
    result = assess_operational_risk('甲公司','quality')
    assert result['metrics'] == {}
    assert len(result['evidence']) == 2
