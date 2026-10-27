"""Quality/delivery facts from explicitly contracted, validated source snapshots."""
from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from app.db.mongo import get_db
from app.domains.risk.operational_metrics import aggregate_operational_observations, normalize_operational_row, operational_observation_status
from app.domains.risk.risk_contract import OperationalDimension, OperationalSourceContract

POLICY_LIMITATION = '已取得指标，风险等级待确认：质量/交付评级政策尚未确认。'


def resolve_operational_source_contract(row: dict, dimension: OperationalDimension) -> OperationalSourceContract | None:
    """No production mapping has passed source-field acceptance yet.

    In particular, neither raw_fields nor an approval flag in a business row
    constitutes trusted unit/denominator configuration.
    """
    return None


def assess_operational_risk(company_name: str, dimension: OperationalDimension) -> dict[str, Any]:
    """Preserve the public payload; facts do not imply an approved risk rating."""
    return analyze_operational_metrics(company_name, dimension)['payload']


def analyze_operational_metrics(company_name: str, dimension: OperationalDimension) -> dict:
    if dimension not in {'quality', 'delivery'}:
        raise ValueError('dimension 必须是 quality 或 delivery')
    db = get_db()
    master = _find_master(db, company_name)
    if not master:
        return {'payload': {
            'assessment_status': 'missing_supplier', 'dimension': dimension, 'company_name': company_name,
            'risk_level': None, 'risk_score': None,
            'data_coverage': _coverage(dimension, False),
            'reason': '当前供应商主体或供应商代码缺失/不唯一，请先核验。',
        }, 'metric_result': None, 'limitations': ['供应商主体或代码尚未唯一核验。']}
    supplier_code = str(master['supplier_code'])
    rows = list(db['supplier_transaction_snapshots'].find({
        'supplier_code': supplier_code, 'sync_status': 'current', 'data_mode': 'real',
        'eligible_for_formal_assessment': True,
    }))
    if not rows:
        return _missing(company_name, dimension, '没有可用于正式分析的真实且校验通过的交易快照。')
    # Invalid periods cannot be ordered safely; never silently fall back to an older month.
    if any(not re.fullmatch(r'[1-9]\d{3}-(0[1-9]|1[0-2])', str(row.get('snapshot_month') or '')) for row in rows):
        return _missing(company_name, dimension, '来源统计月份非法，无法确认最新周期。')
    period = max(row['snapshot_month'] for row in rows)
    selected = [row for row in rows if row['snapshot_month'] == period]
    observations, limitations = [], [POLICY_LIMITATION]
    unconfigured = False
    for row in selected:
        contract = resolve_operational_source_contract(row, dimension)
        if contract is None or contract.dimension != dimension:
            unconfigured = True
            limitations.append('来源指标单位、分母和范围契约尚未确认，当前未覆盖。')
            continue
        observations.append(normalize_operational_row(row, contract))
    result = aggregate_operational_observations(observations)
    if any(operational_observation_status(item, datetime.now(timezone.utc)) != 'available' for item in observations):
        result = result.model_copy(update={'value': None, 'numerator': None, 'denominator': None,
                                          'status': 'partial' if result.status != 'conflicting' else 'conflicting',
                                          'reasons': (*result.reasons, 'source_time_limited')})
    if unconfigured:
        result = result.model_copy(update={'value': None, 'numerator': None, 'denominator': None,
                                          'status': 'partial', 'reasons': (*result.reasons, 'unconfigured_source')})
    reason_labels = {
        'source_time_limited': '部分来源时间未知、已过期或存在冲突，保留各条事实，不能形成当前整体比例。',
        'missing_aggregation_weights': '来源缺少可验证的统计分母，保留各项指标，不计算汇总比例。',
        'unproven_disjointness': '无法确认来源记录互不重叠，暂不合并。',
        'overlapping_partition': '来源统计范围重叠，暂不合并。',
        'incomparable_scope_or_basis': '来源范围、单位或统计口径不同，暂不合并。',
        'conflicting_source_record': '同一来源记录内容冲突，需要核验。',
        'conflicting_aliases': '来源中同一指标的多个字段相互冲突，需要核验。',
        'rate_count_conflict': '来源比例与数量计算不一致，需要核验。',
        'unconfigured_source': '部分来源的指标口径尚未确认，不能形成整体结论。',
    }
    limitations.extend(reason_labels.get(reason, '部分来源指标无法核实，详见数据说明。') for reason in result.reasons)
    if not any(item.status == 'available' and item.value is not None for item in observations):
        missing = _missing(company_name, dimension,
            '来源未配置或当前快照缺少合法且口径明确的质量/交付指标，不能推断低风险。', period)
        return {**missing, 'metric_result': result, 'limitations': list(dict.fromkeys(limitations))}
    facts = {result.metric: result.value} if result.value is not None else {}
    payload = {
        'assessment_status': 'partial', 'dimension': dimension, 'company_name': company_name,
        'supplier_code': supplier_code, 'period': period, 'risk_score': None, 'risk_level': None,
        'data_coverage': _coverage(dimension, bool(facts)), 'metrics': facts,
        'evidence': [{'source': item.source, 'period': item.period, 'data_mode': 'formal',
                      'facts': {item.metric: item.value} if item.value is not None else {}}
                     for item in result.observations],
    }
    return {'payload': payload, 'metric_result': result, 'limitations': list(dict.fromkeys(limitations))}


def _find_master(db: Any, company_name: str) -> dict | None:
    collection = db['supplier_master_snapshots']
    for field in ('name', 'supplier_id', 'supplier_code'):
        matches = list(collection.find({field: company_name, 'sync_status': 'current'}))
        if matches:
            if len(matches) != 1 or not str(matches[0].get('supplier_code') or '').strip():
                return None
            return matches[0]
    return None


def _coverage(dimension: OperationalDimension, available: bool) -> dict:
    fields = ['quality_rate' if dimension == 'quality' else 'on_time_rate']
    return {'available_fields': fields if available else [], 'required_fields': fields,
            'coverage_ratio': 1.0 if available else 0.0}


def _missing(company_name: str, dimension: OperationalDimension, reason: str, period: str | None = None) -> dict:
    payload = {'assessment_status': 'missing_data', 'dimension': dimension, 'company_name': company_name,
               'risk_score': None, 'risk_level': None, 'data_coverage': _coverage(dimension, False), 'reason': reason}
    if period is not None:
        payload['period'] = period
    return {'payload': payload, 'metric_result': None, 'limitations': [reason]}


__all__ = ['assess_operational_risk']
