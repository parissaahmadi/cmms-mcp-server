"""Command line entry point: ``cmms-mcp-server`` / ``python -m cmms_mcp``."""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from . import db, service
from .tools import build_server


def _fixed_clock(text: str) -> service.Clock:
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise SystemExit(f"error: --fixed-now must be an ISO-8601 time, got {text!r}") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return lambda: dt


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cmms-mcp-server", description=__doc__)
    ap.add_argument("--db", default=os.environ.get("CMMS_DB", ":memory:"),
                    help="SQLite file (created and seeded when missing). Default: in-memory, reseeded on every start")  # fmt: skip
    ap.add_argument("--read-only", action="store_true",
                    default=os.environ.get("CMMS_READ_ONLY") == "1",
                    help="do not register the write tools at all")  # fmt: skip
    ap.add_argument("--fixed-now", default=os.environ.get("CMMS_FIXED_NOW"),
                    help="freeze the clock (ISO-8601), useful for demos and tests; the seed data is set on 2026-10-09")  # fmt: skip
    ap.add_argument("--transport", choices=["stdio", "streamable-http"], default="stdio")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    clock = _fixed_clock(args.fixed_now) if args.fixed_now else service.utc_now
    svc = service.Cmms(db.connect(args.db), clock)
    server = build_server(svc, read_only=args.read_only)
    # stdout belongs to the protocol on stdio: anything human-readable goes to stderr.
    print(f"cmms-mcp-server: {args.transport}, db={args.db}, read_only={args.read_only}", file=sys.stderr)
    server.run(args.transport)
    return 0
