"""Hand-calculated regressions for unit and denominator mistakes."""
from copy import deepcopy

import pytest


def contract(**overrides):
    from app.domains.risk.risk_contract import OperationalSourceContract
    values = dict(source='test-contract', version='1', dimension='quality',
                  rate_fields=('quality_rate', 'pass_rate'), rate_unit='ratio', rate_kind='pass',
                  numerator_field='passed', denominator_field='inspected',
                  denominator_basis='inspected_units', complement_allowed=False,
                  quantity_unit_field='unit', scope_fields=('supplier_code', 'material_code', 'base'),
                  record_id_field='source_record_id', partition_field='batch', row_kind='detail',
                  observed_at_field='observed_at', valid_until_field='valid_until')
    return OperationalSourceContract(**(values | overrides))


def row(**overrides):
    return dict(source_record_id='r1', snapshot_month='2026-10', supplier_code='S1',
                material_code='M1', base='P1', batch='B1', unit='件', quality_rate=0.952,
                observed_at='2026-10-01T00:00:00Z', valid_until='2026-11-01T00:00:00Z') | overrides


def normalize(data, spec=None):
    from app.domains.risk.operational_metrics import normalize_operational_row
    return normalize_operational_row(data, spec or contract())


def test_rate_units_normalize_equally():
    from app.domains.risk.operational_metrics import normalize_rate
    assert normalize_rate(0.952, 'ratio') == pytest.approx(0.952)
    assert normalize_rate(95.2, 'percent') == pytest.approx(0.952)
    assert normalize_rate('0', 'ratio') == 0
    assert normalize_rate('95.2', 'percent') == pytest.approx(0.952)


@pytest.mark.parametrize('value,unit', [(True,'ratio'), (float('nan'),'ratio'),
    (float('inf'),'ratio'), (-float('inf'),'ratio'), (-0.1,'ratio'), (95.2,'ratio'),
    (100.1,'percent'), ('bad','ratio'), (None,'ratio')])
def test_invalid_rates_are_rejected(value, unit):
    from app.domains.risk.operational_metrics import normalize_rate
    with pytest.raises(ValueError):
        normalize_rate(value, unit)


def test_alias_conflict_is_not_first_value_wins():
    result = normalize(row(pass_rate=0.9))
    assert result.status == 'conflicting'
    assert result.value is None


def test_reject_ratio_is_not_divided_by_received_qty():
    result = normalize(row(quality_rate=0.048, received_qty=10000),
                       contract(rate_kind='reject', complement_allowed=True))
    assert result.value == pytest.approx(0.952)
    assert result.denominator is None
    assert normalize(row(quality_rate=0.048), contract(rate_kind='defect_occurrence')).value is None
    assert normalize(row(quality_rate=0.048), contract(rate_kind='reject')).value is None


@pytest.mark.parametrize('dimension,kind,basis,numerator,expected', [
    ('quality','reject','inspected_units',2,0.98),
    ('delivery','late','ordered_units',30,0.7)])
def test_counts_only_follow_declared_basis(dimension, kind, basis, numerator, expected):
    result = normalize(row(quality_rate=None, passed=numerator, inspected=100),
        contract(dimension=dimension, rate_kind=kind, denominator_basis=basis, complement_allowed=True))
    assert result.value == pytest.approx(expected)
    assert result.numerator == pytest.approx(expected * 100)


@pytest.mark.parametrize('changes', [dict(inspected=0), dict(inspected=-1),
    dict(passed=101, inspected=100), dict(passed=-1, inspected=100),
    dict(quality_rate='bad'), dict(snapshot_month='2026-13')])
def test_invalid_row_is_not_silently_repaired(changes):
    result = normalize(row(**changes))
    assert result.status == 'unavailable'
    assert result.value is None
    assert result.reasons


def test_counts_and_rate_must_agree():
    result = normalize(row(passed=50, inspected=100))
    assert result.status == 'conflicting'
    assert result.value is None


def test_received_quantity_is_not_assumed_inspected():
    result = normalize(row(received_qty=10000))
    assert result.value == 0.952
    assert result.denominator is None


def test_source_metadata_and_input_are_preserved():
    data = row(inspected=10000)
    original = deepcopy(data)
    result = normalize(data)
    assert data == original
    assert result.value == 0.952
    assert result.numerator == 9520
    assert result.source_record_id == 'r1'
    assert result.period == '2026-10'
    assert result.raw_unit == 'ratio'
    assert result.observed_at.isoformat() == '2026-10-01T00:00:00+00:00'


def aggregate(items):
    from app.domains.risk.operational_metrics import aggregate_operational_observations
    return aggregate_operational_observations(items)


def observations():
    return [normalize(row(quality_rate=0.5, inspected=100)),
            normalize(row(quality_rate=1.0, inspected=9900, source_record_id='r2', batch='B2'))]


def test_weighted_quality_rate_uses_inspected_denominator():
    result = aggregate(observations())
    assert result.status == 'available'
    assert result.denominator == 10000
    assert result.numerator == 9950
    assert result.value == pytest.approx(0.995)


def test_unweighted_rates_remain_individual_observations():
    result = aggregate([normalize(row()), normalize(row(source_record_id='r2', batch='B2'))])
    assert result.value is None
    assert len(result.observations) == 2
    single = aggregate([normalize(row(), contract(row_kind='summary'))])
    assert single.value == 0.952


@pytest.mark.parametrize('changes', [dict(partition=None), dict(partition='B1'),
    dict(quantity_unit='kg'), dict(period='2026-09'), dict(scope={'supplier_code':'S2'}),
    dict(source='other'), dict(source_version='2'), dict(row_kind='summary'),
    dict(denominator_basis='order_lines'), dict(source_record_id='')])
def test_distinct_record_ids_do_not_prove_disjointness(changes):
    items = observations()
    items[1] = items[1].model_copy(update=changes)
    result = aggregate(items)
    assert result.value is None
    assert result.reasons
    assert len(result.observations) == 2


def test_identical_record_is_counted_once_and_conflict_is_preserved():
    items = observations()
    assert aggregate(items + [items[0]]).denominator == 10000
    conflicting = items[0].model_copy(update={'value':0.6})
    result = aggregate(items + [conflicting])
    assert result.status == 'conflicting'
    assert result.value is None
    assert len(result.observations) == 3


def test_bad_row_is_not_dropped_for_a_good_aggregate():
    result = aggregate([normalize(row()), normalize(row(quality_rate=-1, source_record_id='r2'))])
    assert result.value is None
    assert result.status != 'available'
    assert len(result.observations) == 2
    assert aggregate([]).status == 'unavailable'
