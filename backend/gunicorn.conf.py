"""Gunicorn 生产配置 — 多 worker + Uvicorn worker class。"""

import os

# Worker
worker_class = "uvicorn.workers.UvicornWorker"
# Prometheus collectors in this application are process-local. Keep the web
# topology single-process until multiprocess collector storage is introduced.
workers = 1
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
