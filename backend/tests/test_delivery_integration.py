"""Real PG/Mongo leases and multi-process Redis→authenticated WebSocket delivery."""

import json
import os
from pathlib import Path
import select
import signal
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen
from uuid import uuid4

import pytest

from app.core.cache import cache_client
from app.core.config import settings
from app.core.security import create_access_token
from app.db.init_pg import ensure_pg_schema
from app.db.mongo import get_db
from app.db.postgres import get_cursor
from app.domains.outbox.repo import enqueue_event

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", autouse=True)
def isolated_schema():
    if settings.PG_DB != "delivery_stage3_test" or settings.MONGO_DB != "delivery_stage3_test":
        pytest.skip("requires isolated delivery_stage3_test PG and Mongo databases")
    ensure_pg_schema()


def child_env(channel):
    return {**os.environ, "PG_DB": "delivery_stage3_test", "MONGO_DB": "delivery_stage3_test",
            "REALTIME_CHANNEL": channel, "DELIVERY_ROLE_ENABLED": "false", "OUTBOX_WORKER_ENABLED": "true",
            "OUTBOX_BATCH_SIZE": "1", "OUTBOX_LEASE_SECONDS": "3", "PYTHONUNBUFFERED": "1"}


def wait_ready(process, word=b"READY", timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if select.select([process.stdout], [], [], .1)[0] and process.stdout.readline().strip() == word:
            return
        if process.poll() is not None:
            pytest.fail("process exited before ready")
    pytest.fail("process readiness deadline exceeded")


def stop(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
    process.communicate(timeout=5)


def test_delivery_sigkill_after_projection_recovers_one_business_result():
    aggregate = str(uuid4())
    with get_cursor() as (_, cur):
        event_id = enqueue_event(cur, "test.delivery.projection", "test", aggregate, {"key": aggregate})
    channel = "supplisense:test:delivery:" + aggregate
    source = '''
import time
from app.db.mongo import get_db
from app.domains.outbox import service
from app.workers.delivery import run_delivery_once
def project(event):
    get_db()['delivery_test_projections'].update_one({'_id': event['payload']['key']}, {'$setOnInsert': {'value': 'committed'}}, upsert=True)
    print('READY', flush=True)
    time.sleep(SLEEP)
service.register_consumer('test.delivery.projection', 'projection', project)
print(run_delivery_once('WORKER'), flush=True)
'''
    first = subprocess.Popen([sys.executable, "-u", "-c", source.replace("SLEEP", "60").replace("WORKER", "crashed")], cwd=BACKEND, env=child_env(channel), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    try:
        wait_ready(first)
        first.send_signal(signal.SIGKILL)
        first.wait(timeout=3)
        with get_cursor() as (_, cur):
            cur.execute("SELECT published_at,locked_by FROM outbox_events WHERE event_id=%s", (event_id,))
            assert cur.fetchone() == (None, "crashed")
            # Advance only this test's lease. Recovery must never rely on a
            # dead process running finally, or on clearing the whole queue.
            cur.execute("UPDATE outbox_events SET locked_until=clock_timestamp()-INTERVAL '1 second' WHERE event_id=%s", (event_id,))
        successor = subprocess.run([sys.executable, "-u", "-c", source.replace("SLEEP", "0").replace("WORKER", "successor")], cwd=BACKEND, env=child_env(channel), capture_output=True, timeout=20)
        assert successor.returncode == 0, successor.stderr.decode()
        assert get_db()["delivery_test_projections"].count_documents({"_id": aggregate}) == 1
        with get_cursor() as (_, cur):
            cur.execute("SELECT published_at IS NOT NULL FROM outbox_events WHERE event_id=%s", (event_id,))
            assert cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM outbox_consumptions WHERE event_id=%s", (event_id,))
            assert cur.fetchone()[0] == 1
    finally:
        stop(first)
        with get_cursor() as (_, cur):
            cur.execute("DELETE FROM outbox_events WHERE event_id=%s", (event_id,))
        get_db()["delivery_test_projections"].delete_one({"_id": aggregate})


def test_two_api_processes_receive_one_hint_and_reconcile_redis_reconnect():
    from websockets.sync.client import connect

    actor, channel = str(uuid4()), "supplisense:test:realtime:" + uuid4().hex
    with get_cursor() as (_, cur):
        cur.execute("INSERT INTO users(id,username,email,password_hash) VALUES(%s,%s,%s,'test')", (actor, actor, actor + "@example.test"))
    token = create_access_token({"sub": actor})
    processes, ports = [], []
    try:
        for _ in range(2):
            with socket.socket() as listener:
                listener.bind(("127.0.0.1", 0))
                ports.append(listener.getsockname()[1])
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "tests.delivery_process_app:app", "--host", "127.0.0.1", "--port", str(ports[-1]), "--no-access-log", "--log-level", "warning"], cwd=BACKEND, env=child_env(channel), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if int(cache_client.pubsub_numsub(channel)[0][1]) == 2:
                break
            time.sleep(.1)
        else:
            pytest.fail("both API subscribers must be ready")

        def receive(ws, expected):
            for _ in range(8):
                message = json.loads(ws.recv(timeout=5))
                if message["event"] == expected:
                    return message
            pytest.fail("expected WS event missing")

        with connect(f"ws://127.0.0.1:{ports[0]}/ws", additional_headers={"Cookie": "access_token=" + token}) as a, connect(f"ws://127.0.0.1:{ports[1]}/ws", additional_headers={"Cookie": "access_token=" + token}) as b:
            publication = subprocess.run([sys.executable, "-c", "from app.services.realtime import publish; assert publish('company_updated', {})"], cwd=BACKEND, env=child_env(channel), capture_output=True, timeout=10)
            assert publication.returncode == 0
            assert receive(a, "company_updated")["data"] == {}
            assert receive(b, "company_updated")["data"] == {}
            for ws in (a, b):
                ws.send("ping")
                assert json.loads(ws.recv(timeout=5))["event"] == "pong"  # no duplicate hint
            with urlopen(Request(f"http://127.0.0.1:{ports[0]}/__test/disconnect-realtime", method="POST"), timeout=5) as response:
                assert json.load(response)["disconnected"]
            assert receive(a, "realtime_resync")["data"] == {}
    finally:
        for process in processes:
            stop(process)
        with get_cursor() as (_, cur):
            cur.execute("DELETE FROM users WHERE id=%s", (actor,))


def test_two_delivery_processes_compete_for_one_event_and_one_consumption():
    aggregate = str(uuid4())
    with get_cursor() as (_, cur):
        event_id = enqueue_event(cur, "test.delivery.race", "test", aggregate, {"key": aggregate})
    source = '''
from app.db.mongo import get_db
from app.domains.outbox.service import register_consumer
from app.workers.delivery import run_delivery_once
def project(event):
    get_db()['delivery_test_projections'].update_one({'_id': event['payload']['key']}, {'$setOnInsert': {'value': 'one'}}, upsert=True)
register_consumer('test.delivery.race', 'race', project)
print(run_delivery_once(), flush=True)
'''
    children = []
    try:
        for _ in range(2):
            children.append(subprocess.Popen([sys.executable, "-u", "-c", source], cwd=BACKEND, env=child_env("supplisense:test:race:" + aggregate), stdout=subprocess.PIPE, stderr=subprocess.STDOUT))
        for child in children:
            output, _ = child.communicate(timeout=20)
            assert child.returncode == 0, output.decode()
        with get_cursor() as (_, cur):
            cur.execute("SELECT COUNT(*) FROM outbox_consumptions WHERE event_id=%s", (event_id,))
            assert cur.fetchone()[0] == 1
        assert get_db()["delivery_test_projections"].count_documents({"_id": aggregate}) == 1
    finally:
        for child in children:
            stop(child)
        with get_cursor() as (_, cur):
            cur.execute("DELETE FROM outbox_events WHERE event_id=%s", (event_id,))
        get_db()["delivery_test_projections"].delete_one({"_id": aggregate})


def test_killed_notification_sender_is_retained_for_review_without_another_send(monkeypatch):
    from app.domains.outbox.notification_delivery import queue_notification
    from app.workers.delivery import run_delivery_once

    key = str(uuid4())
    row = queue_notification({"monitor_target_id": key, "type": "risk_alert"}, {"recipient_open_id": "test-recipient"}, "test only", key)
    source = '''
import time
from app.domains.outbox import notification_delivery
from app.services import feishu
from app.workers.delivery import run_delivery_once
notification_delivery._recipient_still_authorized = lambda row: True
def provider(*args, **kwargs):
    print('READY', flush=True)
    time.sleep(60)
    return True
feishu.send_user_message = provider
run_delivery_once('killed-sender')
'''
    child = subprocess.Popen([sys.executable, "-u", "-c", source], cwd=BACKEND, env=child_env("supplisense:test:notification:" + key), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
    try:
        wait_ready(child)
        child.kill()
        child.wait(timeout=3)
        with get_cursor() as (_, cur):
            cur.execute("SELECT status FROM notification_dispatches WHERE id=%s", (row["id"],))
            assert cur.fetchone()[0] == "SENDING"
            cur.execute("UPDATE outbox_events SET locked_until=clock_timestamp()-INTERVAL '1 second' WHERE aggregate_id=%s", (row["id"],))
        monkeypatch.setattr(settings, "DELIVERY_ROLE_ENABLED", False)
        monkeypatch.setattr(settings, "OUTBOX_WORKER_ENABLED", True)
        monkeypatch.setattr(settings, "OUTBOX_BATCH_SIZE", 1)
        monkeypatch.setattr("app.services.feishu.send_user_message", lambda *a, **kw: pytest.fail("unknown result must not resend"))
        monkeypatch.setattr("app.services.realtime.publish", lambda *a: True)
        assert run_delivery_once("successor")["published"] == 1
        with get_cursor() as (_, cur):
            cur.execute("SELECT status FROM notification_dispatches WHERE id=%s", (row["id"],))
            assert cur.fetchone()[0] == "NEEDS_REVIEW"
    finally:
        stop(child)
        with get_cursor() as (_, cur):
            cur.execute("DELETE FROM outbox_events WHERE aggregate_id=%s", (row["id"],))
            cur.execute("DELETE FROM notification_dispatches WHERE id=%s", (row["id"],))
        get_db()["notifications"].delete_one({"_id": row["id"]})
        get_db()["notification_deliveries"].delete_many({"notification_id": row["id"]})
