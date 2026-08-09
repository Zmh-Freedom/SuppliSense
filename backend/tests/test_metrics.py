"""Regression coverage for production-safe Prometheus metric labels and aggregation."""

from types import SimpleNamespace

from app.core import metrics


def test_http_metric_endpoint_uses_route_template_or_bounded_fallback() -> None:
    """Concrete IDs and arbitrary unmatched paths must never become metric labels."""
    from app.main import _metric_endpoint_label

    assert _metric_endpoint_label({"route": SimpleNamespace(path="/api/v1/companies/{company_id}")}) == (
        "/api/v1/companies/{company_id}"
    )
    assert _metric_endpoint_label({}) == "__unmatched__"


def test_metrics_uses_a_fresh_multiprocess_registry_when_configured(monkeypatch) -> None:
    """The scrape endpoint must aggregate worker files instead of this worker's registry only."""
    registries: list[object] = []
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "/tmp/prometheus-test")
    monkeypatch.setattr(
        metrics.multiprocess,
        "MultiProcessCollector",
        lambda registry: registries.append(registry),
    )
    monkeypatch.setattr(metrics, "generate_latest", lambda registry: b"aggregated_metrics\n")

    body, content_type = metrics.get_metrics()

    assert body == "aggregated_metrics\n"
    assert content_type == metrics.CONTENT_TYPE_LATEST
    assert len(registries) == 1


def test_gunicorn_cleans_metric_files_before_fork_and_marks_exited_children(monkeypatch, tmp_path) -> None:
    """A restart cannot retain stale gauges and worker exits cannot leave stale gauge shards."""
    from pathlib import Path
    from runpy import run_path

    metric_file = tmp_path / "gauge_livesum_123.db"
    metric_file.touch()
    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    config = run_path(Path(__file__).parents[1] / "gunicorn.conf.py")
    dead_pids: list[int] = []
    monkeypatch.setattr(config["multiprocess"], "mark_process_dead", dead_pids.append)

    config["on_starting"](None)
    config["child_exit"](None, SimpleNamespace(pid=123))

    assert not metric_file.exists()
    assert dead_pids == [123]
