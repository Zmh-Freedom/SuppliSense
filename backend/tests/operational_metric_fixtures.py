"""In-memory source boundary for production operational service/tool tests."""
from tests.test_operational_metrics import contract, row


class Collection:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query):
        return [item for item in self.rows if all(item.get(k) == v for k, v in query.items())]

    def find_one(self, query):
        return next(iter(self.find(query)), None)


def transaction(**changes):
    return row(valid_until='2099-11-01T00:00:00Z', quality_rate=0.952,
               supplier_code='S1', sync_status='current', data_mode='real',
               eligible_for_formal_assessment=True) | changes


def install(monkeypatch, rows=None, masters=None, configured=True):
    from app.domains.risk import operational_risk_service as service
    database = {
        'supplier_master_snapshots': Collection(masters if masters is not None else [
            {'name':'甲公司', 'supplier_code':'S1', 'sync_status':'current'}]),
        'supplier_transaction_snapshots': Collection(rows if rows is not None else [transaction()]),
    }
    monkeypatch.setattr(service, 'get_db', lambda: database)
    if configured:
        monkeypatch.setattr(service, 'resolve_operational_source_contract',
            lambda data, dimension: contract(dimension=dimension,
                rate_kind='pass' if dimension == 'quality' else 'on_time',
                rate_fields=('quality_rate','pass_rate') if dimension == 'quality' else ('on_time_rate',),
                denominator_basis='inspected_units' if dimension == 'quality' else 'ordered_units'),
            raising=False)
    return database
