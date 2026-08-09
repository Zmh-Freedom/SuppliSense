"""Gunicorn 生产配置 — 多 worker + Uvicorn worker class。"""

import os
from pathlib import Path

from prometheus_client import multiprocess

# Worker
worker_class = "uvicorn.workers.UvicornWorker"
workers = int(os.getenv("GUNICORN_WORKERS", "4"))
threads = int(os.getenv("GUNICORN_THREADS", "2"))
worker_connections = 1000

# Binding
bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

# Timeouts
timeout = 120  # SSE 长连接需要较长超时
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info")

# Process naming
proc_name = "supplisense"


def on_starting(server) -> None:
    """Clear stale multiprocess metric files in the Gunicorn master before workers fork."""
    directory = os.getenv("PROMETHEUS_MULTIPROC_DIR")
    if not directory:
        return
    metrics_dir = Path(directory)
    metrics_dir.mkdir(parents=True, exist_ok=True)
    for path in metrics_dir.glob("*.db"):
        path.unlink()


def child_exit(server, worker) -> None:
    """Mark dead workers so multiprocess gauges do not retain stale values."""
    if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.mark_process_dead(worker.pid)
