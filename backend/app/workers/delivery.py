"""Independent Transactional Outbox delivery process; no API or scheduler."""

import argparse
import json
import os
import signal
import socket
import threading
import uuid
from typing import Any

from app.core.config import settings
from app.core.logging import get_logger
from app.db.worker_migrations import assert_worker_schema_ready
from app.domains.outbox.worker import run_outbox_once
from app.workers.role_config import assert_test_delivery_sink

logger = get_logger(__name__)
DRAIN_TIMEOUT_SECONDS = 30.0
_STOP = threading.Event()
_EXIT_TIMER: threading.Timer | None = None


def run_delivery_once(worker_id: str | None = None) -> dict[str, Any]:
    return run_outbox_once(worker_id, dedicated=True)


def _request_stop(*_: Any) -> None:
    global _EXIT_TIMER
    if _STOP.is_set():
        return
    _STOP.set()
    # Synchronous Mongo/provider calls may not be cancellable. Leave the
    # outstanding leases and consumer receipts for the successor to verify.
    _EXIT_TIMER = threading.Timer(DRAIN_TIMEOUT_SECONDS, os._exit, args=(0,))
    _EXIT_TIMER.daemon = True
    _EXIT_TIMER.start()


def main() -> None:
    parser = argparse.ArgumentParser(description="Independent Outbox delivery worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--worker-id", default="")
    args = parser.parse_args()
    if settings.DELIVERY_ROLE_ENABLED:
        raise ValueError("独立投递前必须设置 DELIVERY_ROLE_ENABLED=false，停止 API 领取")
    assert_test_delivery_sink()
    assert_worker_schema_ready()
    worker_id = args.worker_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)
    try:
        while not _STOP.is_set():
            result = run_delivery_once(worker_id)
            if args.once:
                print(json.dumps(result, ensure_ascii=False), flush=True)
                return
            logger.info("delivery_batch_finished", **result)
            if not _STOP.is_set():
                _STOP.wait(settings.OUTBOX_POLL_SECONDS)
    finally:
        if _EXIT_TIMER is not None:
            _EXIT_TIMER.cancel()


if __name__ == "__main__":
    main()
