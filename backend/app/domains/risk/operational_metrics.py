"""Pure operational metric normalization; no storage, providers or graph imports."""
from datetime import datetime, timezone
import math
import re
from typing import Literal

from app.domains.risk.risk_contract import OperationalMetricResult, OperationalObservation, OperationalSourceContract


def _number(value: object) -> float:
    if isinstance(value, bool) or value is None:
        raise ValueError('invalid_number')
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError('invalid_number') from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError('invalid_number')
    return result


def normalize_rate(value: object, unit: Literal['ratio', 'percent']) -> float:
    number = _number(value)
    if unit not in {'ratio', 'percent'} or number > (1 if unit == 'ratio' else 100):
        raise ValueError('invalid_rate')
    return number if unit == 'ratio' else number / 100


def _timestamp(value: object) -> datetime | None:
    try:
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # A source without a timezone has no proven instant.
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except (TypeError, ValueError):
        return None


def normalize_operational_row(row: dict, contract: OperationalSourceContract) -> OperationalObservation:
    scope = {key: str(row.get(key) or '') for key in contract.scope_fields}
    base = dict(metric='quality_rate' if contract.dimension == 'quality' else 'on_time_rate',
                raw_unit=contract.rate_unit, period=str(row.get('snapshot_month') or ''), scope=scope,
                source=contract.source, source_version=contract.version,
                source_record_id=str(row.get(contract.record_id_field) or ''),
                partition=str(row.get(contract.partition_field) or '') or None,
                row_kind=contract.row_kind, denominator_basis=contract.denominator_basis,
                quantity_unit=str(row.get(contract.quantity_unit_field) or '') or None,
                observed_at=_timestamp(row.get(contract.observed_at_field)),
                valid_until=_timestamp(row.get(contract.valid_until_field)))
    status = 'unavailable'
    try:
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', base['period']) or base['period'].startswith('0000'):
            raise ValueError('invalid_period')
        if not scope or any(not value for value in scope.values()):
            raise ValueError('missing_scope')
        if contract.rate_kind == 'defect_occurrence':
            raise ValueError('non_complementary_defect_rate')
        quality = contract.dimension == 'quality'
        if contract.rate_kind not in ({'pass', 'reject'} if quality else {'on_time', 'late'}):
            raise ValueError('incompatible_rate_kind')
        if contract.denominator_basis and quality and contract.denominator_basis != 'inspected_units':
            raise ValueError('incompatible_denominator')
        complement = contract.rate_kind in {'reject', 'late'}
        if complement and (not contract.complement_allowed or not contract.denominator_basis):
            raise ValueError('unverified_complement')
        rates = [normalize_rate(row[field], contract.rate_unit) for field in contract.rate_fields if row.get(field) is not None]
        if rates and any(not math.isclose(rates[0], rate, rel_tol=0, abs_tol=1e-9) for rate in rates[1:]):
            status = 'conflicting'
            raise ValueError('conflicting_aliases')
        rate = rates[0] if rates else None
        if rates:
            base['raw_value'] = _number(next(row[field] for field in contract.rate_fields if row.get(field) is not None))
        numerator = _number(row[contract.numerator_field]) if row.get(contract.numerator_field) is not None else None
        denominator = _number(row[contract.denominator_field]) if row.get(contract.denominator_field) is not None else None
        if denominator is not None:
            if denominator == 0 or not contract.denominator_basis or not base['quantity_unit']:
                raise ValueError('invalid_denominator')
            if numerator is not None:
                if numerator > denominator:
                    raise ValueError('numerator_exceeds_denominator')
                from_counts = numerator / denominator
                if rate is not None and not math.isclose(rate, from_counts, rel_tol=0, abs_tol=1e-9):
                    status = 'conflicting'
                    raise ValueError('rate_count_conflict')
                rate = from_counts
            elif rate is not None:
                numerator = rate * denominator
        elif numerator is not None:
            raise ValueError('missing_denominator')
        if rate is None:
            raise ValueError('missing_rate')
        if complement:
            rate = 1 - rate
            if numerator is not None:
                numerator = denominator - numerator
        return OperationalObservation(**base, value=rate, numerator=numerator, denominator=denominator, status='available')
    except ValueError as exc:
        return OperationalObservation(**base, status=status, reasons=(str(exc),))


def aggregate_operational_observations(observations: list[OperationalObservation]) -> OperationalMetricResult:
    """Aggregate only disjoint, comparable observations; retain every limitation."""
    unique: dict[tuple[str, str, str], OperationalObservation] = {}
    retained = []
    reasons = []
    conflict = False
    for item in observations:
        key = (item.source, item.source_version, item.source_record_id)
        previous = unique.get(key) if item.source_record_id else None
        if previous == item:
            continue
        retained.append(item)
        if previous is not None:
            conflict = True
            reasons.append('conflicting_source_record')
        unique[key] = item
        if item.status != 'available':
            reasons.extend(item.reasons)
            conflict |= item.status == 'conflicting'
    first = retained[0] if retained else None
    common = dict(metric=first.metric if first else '', period=first.period if first else None,
                  observations=tuple(retained))
    if not first:
        return OperationalMetricResult(**common, status='unavailable', reasons=('missing_observations',))
    if not reasons and any(item.status != 'available' for item in retained):
        reasons.append('invalid_observation')
    if len(retained) > 1:
        signature = lambda item: (item.metric, item.period, item.source, item.source_version,
                                 item.scope, item.quantity_unit, item.denominator_basis, item.row_kind)
        if any(signature(item) != signature(first) for item in retained[1:]):
            reasons.append('incomparable_scope_or_basis')
        if any(not item.source_record_id or not item.partition or item.row_kind != 'detail' for item in retained):
            reasons.append('unproven_disjointness')
        if len({item.partition for item in retained}) != len(retained):
            reasons.append('overlapping_partition')
        if any(item.denominator is None or item.numerator is None for item in retained):
            reasons.append('missing_aggregation_weights')
    elif first.row_kind == 'detail' and not first.source_record_id:
        reasons.append('missing_record_identity')
    if reasons:
        return OperationalMetricResult(**common, status='conflicting' if conflict else 'partial',
                                       reasons=tuple(dict.fromkeys(reasons)))
    if len(retained) == 1:
        return OperationalMetricResult(**common, status='available', value=first.value,
                                       numerator=first.numerator, denominator=first.denominator)
    numerator = math.fsum(item.numerator for item in retained)
    denominator = math.fsum(item.denominator for item in retained)
    if not math.isfinite(numerator) or not math.isfinite(denominator) or denominator <= 0:
        return OperationalMetricResult(**common, status='unavailable', reasons=('invalid_aggregate',))
    return OperationalMetricResult(**common, status='available', value=numerator / denominator,
                                   numerator=numerator, denominator=denominator)


def operational_observation_status(observation: OperationalObservation, now: datetime) -> str:
    """Source validity at a supplied instant, independent of collection time."""
    if observation.status != 'available':
        return observation.status
    if (not observation.observed_at or not observation.valid_until
            or observation.observed_at > now or observation.valid_until <= observation.observed_at):
        return 'unavailable'
    return 'stale' if observation.valid_until <= now else 'available'
