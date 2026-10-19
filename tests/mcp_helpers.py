import os
import sys
from datetime import UTC, datetime

import anyio
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

from cmms_mcp import db, service
from cmms_mcp.tools import build_server

NOW = datetime(2026, 10, 9, 6, 0, tzinfo=UTC)


def server(*, read_only=False, svc=None):
    svc = svc or service.Cmms(db.connect(), lambda: NOW)
    return build_server(svc, read_only=read_only)


def run(coro_fn):
    """Run ``async def fn(client)`` against a fresh in-process server."""
    return anyio.run(coro_fn)


def with_client(srv, body):
    async def main():
        async with Client(srv) as c:
            return await body(c)

    return anyio.run(main)


def stdio_params(*extra):
    env = {**os.environ}
    return StdioServerParameters(
        command=sys.executable,
        args=["-m", "cmms_mcp", "--fixed-now", "2026-10-09T06:00:00Z", *extra],
        env=env,
    )


def with_stdio(body, *extra):
    async def main():
        async with Client(stdio_params(*extra)) as c:
            return await body(c)

    return anyio.run(main)
