"""SQLite schema, connection and seeding."""

from __future__ import annotations

import json
import sqlite3
from importlib import resources

SCHEMA = """
CREATE TABLE users (
    username     TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    role         TEXT NOT NULL CHECK (role IN ('viewer', 'technician', 'supervisor'))
);
CREATE TABLE assets (
    id           TEXT PRIMARY KEY,
    name         TEXT NOT NULL,
    type         TEXT NOT NULL,
    location     TEXT NOT NULL,
    manufacturer TEXT NOT NULL,
    model        TEXT NOT NULL,
    serial       TEXT NOT NULL,
    criticality  TEXT NOT NULL CHECK (criticality IN ('A', 'B', 'C')),
    status       TEXT NOT NULL CHECK (status IN ('running', 'stopped', 'maintenance')),
    installed_on TEXT NOT NULL,
    meter_hours  INTEGER NOT NULL CHECK (meter_hours >= 0)
);
CREATE TABLE alarms (
    id              TEXT PRIMARY KEY,
    asset_id        TEXT NOT NULL REFERENCES assets (id),
    severity        TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'critical')),
    code            TEXT NOT NULL,
    message         TEXT NOT NULL,
    raised_at       TEXT NOT NULL,
    state           TEXT NOT NULL CHECK (state IN ('active', 'acknowledged', 'cleared')),
    acknowledged_by TEXT REFERENCES users (username),
    acknowledged_at TEXT,
    cleared_at      TEXT
);
CREATE TABLE work_orders (
    id              TEXT PRIMARY KEY,
    asset_id        TEXT NOT NULL REFERENCES assets (id),
    title           TEXT NOT NULL,
    description     TEXT NOT NULL,
    wo_type         TEXT NOT NULL CHECK (wo_type IN ('corrective', 'preventive', 'inspection')),
    priority        TEXT NOT NULL CHECK (priority IN ('low', 'medium', 'high', 'urgent')),
    status          TEXT NOT NULL CHECK (status IN
                      ('open', 'assigned', 'in_progress', 'on_hold', 'completed', 'closed', 'cancelled')),
    assignee        TEXT REFERENCES users (username),
    created_by      TEXT NOT NULL REFERENCES users (username),
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL,
    due_date        TEXT,
    completed_at    TEXT,
    source_alarm_id TEXT REFERENCES alarms (id)
);
CREATE TABLE wo_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    wo_id       TEXT NOT NULL REFERENCES work_orders (id),
    at          TEXT NOT NULL,
    actor       TEXT NOT NULL,
    action      TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT,
    note        TEXT
);
CREATE INDEX idx_alarms_asset ON alarms (asset_id, state);
CREATE INDEX idx_wo_asset ON work_orders (asset_id, status);
CREATE INDEX idx_wo_alarm ON work_orders (source_alarm_id);
"""

_TABLE_COLUMNS = {
    "users": ("username", "display_name", "role"),
    "assets": ("id", "name", "type", "location", "manufacturer", "model", "serial",
               "criticality", "status", "installed_on", "meter_hours"),  # fmt: skip
    "alarms": ("id", "asset_id", "severity", "code", "message", "raised_at", "state",
               "acknowledged_by", "acknowledged_at", "cleared_at"),  # fmt: skip
    "work_orders": ("id", "asset_id", "title", "description", "wo_type", "priority", "status",
                    "assignee", "created_by", "created_at", "updated_at", "due_date",
                    "completed_at", "source_alarm_id"),  # fmt: skip
    "wo_events": ("wo_id", "at", "actor", "action", "from_status", "to_status", "note"),
}


def default_seed() -> dict:
    text = resources.files("cmms_mcp").joinpath("data/seed.json").read_text(encoding="utf-8")
    return json.loads(text)


def connect(path: str = ":memory:", *, seed: dict | None = None) -> sqlite3.Connection:
    """Open the database. A new/empty database is created and seeded (default: fictional plant)."""
    conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    has_schema = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'assets'"
    ).fetchone()
    if not has_schema:
        conn.executescript(SCHEMA)
        load_seed(conn, seed if seed is not None else default_seed())
    return conn


def load_seed(conn: sqlite3.Connection, data: dict) -> None:
    conn.execute("BEGIN")
    try:
        for table, cols in _TABLE_COLUMNS.items():
            marks = ", ".join("?" for _ in cols)
            sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({marks})"
            for row in data.get(table, []):
                conn.execute(sql, [row.get(c) for c in cols])
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
