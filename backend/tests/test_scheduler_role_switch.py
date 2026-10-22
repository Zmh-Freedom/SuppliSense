"""The API and the dedicated scheduler must never both own business cron."""

import app.services.scheduler as scheduler_service


class FakeScheduler:
    def __init__(self):
        self.running = False
        self.jobs = []
        self.started = False

    def add_job(self, func, trigger, **kwargs):
        self.jobs.append(kwargs.get("id"))

    def start(self):
        self.started = True
        self.running = True

    def shutdown(self, wait=True):
        self.running = False


class FakeLeadership:
    def __init__(self, acquired):
        self.acquired = acquired
        self.releases = 0

    def acquire(self):
        return self.acquired

    def release(self):
        self.releases += 1


def _setup(monkeypatch, *, role, acquired=True):
    fake = FakeScheduler()
    leadership = FakeLeadership(acquired)
    business = []

    def add_business():
        business.append(1)
        fake.add_job(None, None, id="financial_check")

    monkeypatch.setattr(scheduler_service, "_scheduler", fake)
    monkeypatch.setattr(scheduler_service, "_scheduler_leadership", leadership)
    monkeypatch.setattr(scheduler_service, "_add_business_jobs", add_business)
    monkeypatch.setattr(scheduler_service.settings, "SCHEDULER_ROLE_ENABLED", role)
    return fake, leadership, business


def test_api_with_business_role_disabled_only_runs_outbox(monkeypatch):
    fake, leadership, business = _setup(monkeypatch, role=False)
    scheduler_service.start_scheduler()
    assert business == []
    assert fake.jobs == ["outbox_worker"]
    assert fake.started is True
    assert leadership.releases == 0


def test_without_leadership_no_process_starts(monkeypatch):
    fake, leadership, business = _setup(monkeypatch, role=False, acquired=False)
    scheduler_service.start_scheduler()
    assert business == []
    assert fake.jobs == []
    assert fake.started is False
    assert leadership.releases == 0


def test_leader_starts_business_jobs_and_outbox(monkeypatch):
    fake, _, business = _setup(monkeypatch, role=True)
    scheduler_service.start_scheduler()
    assert business == [1]
    assert fake.jobs == ["financial_check", "outbox_worker"]
    assert fake.started is True
