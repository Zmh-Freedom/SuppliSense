"""Operator entry for registering and inspecting durable schedules."""

from __future__ import annotations

import argparse
import json
from datetime import datetime

from app.domains.scheduling import repo
from app.domains.scheduling.periods import ScheduleDefinition


def _register(args: argparse.Namespace) -> dict:
    scope = json.loads(args.scope) if args.scope else {"target_id": args.target_id}
    definition = ScheduleDefinition(
        schedule_id=args.schedule_id,
        revision=args.revision,
        target_id=args.target_id,
        cron=args.cron,
        timezone=args.timezone,
        actor_kind=args.actor_kind,
        actor_id=args.actor_id,
        kind=args.kind,
        scope=scope,
        effective_at=datetime.fromisoformat(args.effective_at),
    )
    return repo.register_schedule(definition)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Durable schedule administration")
    sub = parser.add_subparsers(dest="command", required=True)

    register = sub.add_parser("register", help="register an immutable schedule revision")
    register.add_argument("--schedule-id", required=True)
    register.add_argument("--revision", type=int, required=True)
    register.add_argument("--target-id", required=True)
    register.add_argument("--cron", required=True)
    register.add_argument("--timezone", default="Asia/Shanghai")
    register.add_argument("--actor-kind", choices=("user", "service"), default="user")
    register.add_argument("--actor-id", required=True)
    register.add_argument("--kind", required=True)
    register.add_argument("--scope", default="", help="JSON object; defaults to target binding")
    register.add_argument("--effective-at", required=True, help="ISO-8601 timestamp with offset")

    listing = sub.add_parser("list", help="list persisted schedule revisions")
    listing.add_argument("--schedule-id", default="")

    args = parser.parse_args(argv)
    if args.command == "register":
        payload = _register(args)
    else:
        payload = repo.list_schedules(schedule_id=args.schedule_id or None)
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
