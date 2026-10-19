"""Shared helpers (plain module so the tests also run without pytest fixtures)."""

from datetime import UTC, datetime

from cmms_mcp import db, service

NOW = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)


def make(now: datetime = NOW) -> service.Cmms:
    box = {"now": now}
    svc = service.Cmms(db.connect(), lambda: box["now"])
    svc.box = box  # tests can move the clock: svc.box["now"] = ...
    return svc
