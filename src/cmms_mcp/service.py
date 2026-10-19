"""CMMS business logic. Pure Python + sqlite3; knows nothing about MCP.

Every public method validates its input, checks the actor's role, and either does all of its
writes or none (one transaction). Reads never modify anything.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime

from . import state_machine as sm
from .errors import Conflict, Forbidden, Invalid, NotFound
from .models import (
    AckResult,
    Alarm,
    AlarmPage,
    Asset,
    AssetDetail,
    AssetPage,
    Overview,
    WorkOrder,
    WorkOrderDetail,
    WorkOrderEvent,
    WorkOrderPage,
)  # fmt: skip

Clock = Callable[[], datetime]

ASSET_STATUS = ("running", "stopped", "maintenance")
CRITICALITY = ("A", "B", "C")
SEVERITY = ("info", "warning", "critical")
ALARM_STATE = ("active", "acknowledged", "cleared")
PRIORITY = ("low", "medium", "high", "urgent")
WO_TYPE = ("corrective", "preventive", "inspection")
MAX_LIMIT = 100

_RE_ASSET = re.compile(r"^AST-\d{4}$")
_RE_ALARM = re.compile(r"^ALM-\d{4}$")
_RE_WO = re.compile(r"^WO-\d{4}-\d{4}$")
_RE_USER = re.compile(r"^[a-z][a-z0-9._-]{1,31}$")

_SEV_RANK = "CASE severity WHEN 'critical' THEN 3 WHEN 'warning' THEN 2 ELSE 1 END"
_PRIO_RANK = "CASE priority WHEN 'urgent' THEN 4 WHEN 'high' THEN 3 WHEN 'medium' THEN 2 ELSE 1 END"
_ACTIVE_SQL = ", ".join(f"'{s}'" for s in sorted(sm.ACTIVE))


def utc_now() -> datetime:
    return datetime.now(UTC)


def _stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _choice(value: str | None, allowed: tuple[str, ...], field: str) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise Invalid(f"{field} must be one of {', '.join(allowed)}; got {value!r}")
    return value


def _ident(value: str, rx: re.Pattern[str], field: str, example: str) -> str:
    if not isinstance(value, str) or not rx.match(value):
        raise Invalid(f"{field} must look like {example}; got {value!r}")
    return value


def _text(value: str | None, field: str, *, lo: int = 0, hi: int = 500) -> str:
    text = (value or "").strip()
    if not lo <= len(text) <= hi:
        raise Invalid(f"{field} must be {lo}-{hi} characters (got {len(text)})")
    return text


def _paging(limit: int, offset: int) -> None:
    if not 1 <= limit <= MAX_LIMIT:
        raise Invalid(f"limit must be between 1 and {MAX_LIMIT}")
    if offset < 0:
        raise Invalid("offset must be >= 0")


def _next(total: int, offset: int, n: int) -> int | None:
    return offset + n if offset + n < total else None


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


class Cmms:
    def __init__(self, conn: sqlite3.Connection, clock: Clock = utc_now) -> None:
        self.db = conn
        self.clock = clock
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ helpers
    def _now(self) -> str:
        return _stamp(self.clock())

    def _today(self) -> str:
        return self.clock().astimezone(UTC).date().isoformat()

    def _tx(self):
        return _Tx(self)

    def _user(self, username: str) -> sqlite3.Row:
        _ident(username, _RE_USER, "actor", "'tech.a'")
        row = self.db.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if row is None:
            raise Forbidden(f"unknown user {username!r}")
        return row

    def _asset_row(self, asset_id: str) -> sqlite3.Row:
        _ident(asset_id, _RE_ASSET, "asset_id", "AST-0001")
        row = self.db.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise NotFound(f"asset {asset_id} does not exist")
        return row

    def _alarm_row(self, alarm_id: str) -> sqlite3.Row:
        _ident(alarm_id, _RE_ALARM, "alarm_id", "ALM-0001")
        row = self.db.execute("SELECT * FROM alarms WHERE id = ?", (alarm_id,)).fetchone()
        if row is None:
            raise NotFound(f"alarm {alarm_id} does not exist")
        return row

    def _wo_row(self, wo_id: str) -> sqlite3.Row:
        _ident(wo_id, _RE_WO, "work_order_id", "WO-2026-0001")
        row = self.db.execute("SELECT * FROM work_orders WHERE id = ?", (wo_id,)).fetchone()
        if row is None:
            raise NotFound(f"work order {wo_id} does not exist")
        return row

    def _wo(self, row: sqlite3.Row) -> WorkOrder:
        d = dict(row)
        d["overdue"] = bool(d["due_date"] and d["due_date"] < self._today() and d["status"] in sm.ACTIVE)
        return WorkOrder(**d)

    def _detail(self, wo_id: str) -> WorkOrderDetail:
        rows = self.db.execute(
            "SELECT at, actor, action, from_status, to_status, note FROM wo_events "
            "WHERE wo_id = ? ORDER BY id", (wo_id,),
        ).fetchall()  # fmt: skip
        return WorkOrderDetail(
            work_order=self._wo(self._wo_row(wo_id)),
            events=[WorkOrderEvent(**dict(r)) for r in rows],
        )

    def _event(self, wo_id, actor, action, frm, to, note) -> None:
        self.db.execute(
            "INSERT INTO wo_events (wo_id, at, actor, action, from_status, to_status, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)", (wo_id, self._now(), actor, action, frm, to, note),
        )  # fmt: skip

    def _next_wo_id(self) -> str:
        year = self.clock().astimezone(UTC).year
        row = self.db.execute(
            "SELECT MAX(CAST(SUBSTR(id, 9) AS INTEGER)) AS n FROM work_orders WHERE id LIKE ?",
            (f"WO-{year}-%",),
        ).fetchone()  # fmt: skip
        return f"WO-{year}-{(row['n'] or 0) + 1:04d}"

    # ------------------------------------------------------------------ reads
    def list_assets(self, *, status=None, type=None, location=None, criticality=None,
                    query=None, limit=20, offset=0) -> AssetPage:  # fmt: skip
        _paging(limit, offset)
        where, args = [], []
        for col, val, allowed in (("status", status, ASSET_STATUS), ("criticality", criticality, CRITICALITY)):
            if _choice(val, allowed, col):
                where.append(f"{col} = ?")
                args.append(val)
        if type:
            where.append("type = ?")
            args.append(_text(type, "type", hi=40))
        if location:
            where.append("location = ?")
            args.append(_text(location, "location", hi=80))
        if query:
            q = _like(_text(query, "query", hi=80))
            cols = ("id", "name", "type", "location", "manufacturer", "model", "serial")
            where.append("(" + " OR ".join(f"{c} LIKE ? ESCAPE '\\'" for c in cols) + ")")
            args += [q] * len(cols)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self._lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM assets {clause}", args).fetchone()[0]
            rows = self.db.execute(
                f"SELECT * FROM assets {clause} ORDER BY criticality, id LIMIT ? OFFSET ?",
                [*args, limit, offset],
            ).fetchall()  # fmt: skip
        return AssetPage(items=[Asset(**dict(r)) for r in rows], total=total,
                         next_offset=_next(total, offset, len(rows)))  # fmt: skip

    def get_asset(self, asset_id: str) -> AssetDetail:
        with self._lock:
            asset = self._asset_row(asset_id)
            alarms = self.db.execute(
                f"SELECT * FROM alarms WHERE asset_id = ? AND state != 'cleared' "
                f"ORDER BY {_SEV_RANK} DESC, raised_at DESC", (asset_id,),
            ).fetchall()  # fmt: skip
            open_wos = self.db.execute(
                f"SELECT * FROM work_orders WHERE asset_id = ? AND status IN ({_ACTIVE_SQL}) "
                f"ORDER BY {_PRIO_RANK} DESC, id", (asset_id,),
            ).fetchall()  # fmt: skip
            last = self.db.execute(
                "SELECT * FROM work_orders WHERE asset_id = ? AND completed_at IS NOT NULL "
                "ORDER BY completed_at DESC LIMIT 1", (asset_id,),
            ).fetchone()  # fmt: skip
            return AssetDetail(
                asset=Asset(**dict(asset)),
                active_alarms=[Alarm(**dict(r)) for r in alarms],
                open_work_orders=[self._wo(r) for r in open_wos],
                last_completed_work_order=self._wo(last) if last else None,
            )

    def list_alarms(self, *, state=None, severity=None, asset_id=None, since=None,
                    limit=20, offset=0) -> AlarmPage:  # fmt: skip
        _paging(limit, offset)
        where, args = [], []
        if _choice(state, ALARM_STATE, "state"):
            where.append("state = ?")
            args.append(state)
        if _choice(severity, SEVERITY, "severity"):
            where.append("severity = ?")
            args.append(severity)
        if asset_id:
            _ident(asset_id, _RE_ASSET, "asset_id", "AST-0001")
            where.append("asset_id = ?")
            args.append(asset_id)
        if since:
            try:
                dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
            except ValueError:
                raise Invalid(f"since must be an ISO-8601 timestamp; got {since!r}") from None
            where.append("raised_at >= ?")
            args.append(_stamp(dt if dt.tzinfo else dt.replace(tzinfo=UTC)))
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self._lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM alarms {clause}", args).fetchone()[0]
            rows = self.db.execute(
                f"SELECT * FROM alarms {clause} ORDER BY {_SEV_RANK} DESC, raised_at DESC, id "
                "LIMIT ? OFFSET ?", [*args, limit, offset],
            ).fetchall()  # fmt: skip
        return AlarmPage(items=[Alarm(**dict(r)) for r in rows], total=total,
                         next_offset=_next(total, offset, len(rows)))  # fmt: skip

    def list_work_orders(self, *, status=None, priority=None, asset_id=None, assignee=None,
                         overdue_only=False, limit=20, offset=0) -> WorkOrderPage:  # fmt: skip
        _paging(limit, offset)
        where, args = [], []
        if _choice(status, sm.STATUSES, "status"):
            where.append("status = ?")
            args.append(status)
        if _choice(priority, PRIORITY, "priority"):
            where.append("priority = ?")
            args.append(priority)
        if asset_id:
            _ident(asset_id, _RE_ASSET, "asset_id", "AST-0001")
            where.append("asset_id = ?")
            args.append(asset_id)
        if assignee:
            _ident(assignee, _RE_USER, "assignee", "'tech.a'")
            where.append("assignee = ?")
            args.append(assignee)
        if overdue_only:
            where.append(f"due_date IS NOT NULL AND due_date < ? AND status IN ({_ACTIVE_SQL})")
            args.append(self._today())
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        with self._lock:
            total = self.db.execute(f"SELECT COUNT(*) FROM work_orders {clause}", args).fetchone()[0]
            rows = self.db.execute(
                f"SELECT * FROM work_orders {clause} ORDER BY {_PRIO_RANK} DESC, "
                "due_date IS NULL, due_date, id LIMIT ? OFFSET ?", [*args, limit, offset],
            ).fetchall()  # fmt: skip
            items = [self._wo(r) for r in rows]
        return WorkOrderPage(items=items, total=total, next_offset=_next(total, offset, len(items)))

    def get_work_order(self, wo_id: str) -> WorkOrderDetail:
        with self._lock:
            self._wo_row(wo_id)
            return self._detail(wo_id)

    def overview(self) -> Overview:
        def counts(sql: str, keys: tuple[str, ...]) -> dict[str, int]:
            found = {r[0]: r[1] for r in self.db.execute(sql)}
            return {k: found.get(k, 0) for k in keys}

        with self._lock:
            crit = [r[0] for r in self.db.execute(
                "SELECT DISTINCT a.id FROM assets a JOIN alarms l ON l.asset_id = a.id "
                "WHERE a.criticality = 'A' AND l.state = 'active' ORDER BY a.id")]  # fmt: skip
            return Overview(
                as_of=self._now(),
                assets_by_status=counts("SELECT status, COUNT(*) FROM assets GROUP BY status", ASSET_STATUS),
                active_alarms_by_severity=counts(
                    "SELECT severity, COUNT(*) FROM alarms WHERE state = 'active' GROUP BY severity",
                    SEVERITY),
                work_orders_by_status=counts(
                    "SELECT status, COUNT(*) FROM work_orders GROUP BY status", sm.STATUSES),
                overdue_work_orders=self.db.execute(
                    f"SELECT COUNT(*) FROM work_orders WHERE due_date IS NOT NULL AND due_date < ? "
                    f"AND status IN ({_ACTIVE_SQL})", (self._today(),)).fetchone()[0],
                critical_assets_with_active_alarms=crit,
            )  # fmt: skip

    # ------------------------------------------------------------------ writes
    def acknowledge_alarm(self, alarm_id: str, actor: str) -> AckResult:
        with self._lock, self._tx():
            user = self._user(actor)
            if user["role"] == "viewer":
                raise Forbidden(f"{actor} is a viewer and cannot acknowledge alarms")
            alarm = self._alarm_row(alarm_id)
            if alarm["state"] == "cleared":
                raise Conflict(f"alarm {alarm_id} is already cleared; nothing to acknowledge")
            if alarm["state"] == "acknowledged":
                return AckResult(alarm=Alarm(**dict(alarm)), already_acknowledged=True)
            self.db.execute(
                "UPDATE alarms SET state = 'acknowledged', acknowledged_by = ?, acknowledged_at = ? "
                "WHERE id = ?", (actor, self._now(), alarm_id),
            )  # fmt: skip
            return AckResult(alarm=Alarm(**dict(self._alarm_row(alarm_id))), already_acknowledged=False)

    def create_work_order(self, *, actor: str, asset_id: str, title: str, description: str = "",
                          priority: str = "medium", wo_type: str = "corrective",
                          due_date: str | None = None,
                          source_alarm_id: str | None = None) -> WorkOrderDetail:  # fmt: skip
        with self._lock, self._tx():
            user = self._user(actor)
            if user["role"] == "viewer":
                raise Forbidden(f"{actor} is a viewer and cannot create work orders")
            self._asset_row(asset_id)
            title = _text(title, "title", lo=5, hi=120)
            description = _text(description, "description", hi=2000)
            _choice(priority, PRIORITY, "priority")
            _choice(wo_type, WO_TYPE, "wo_type")
            if due_date is not None:
                try:
                    due = date.fromisoformat(due_date)
                except ValueError:
                    raise Invalid(f"due_date must be YYYY-MM-DD; got {due_date!r}") from None
                if due.isoformat() < self._today():
                    raise Invalid(f"due_date {due_date} is in the past (today is {self._today()})")
            if source_alarm_id:
                alarm = self._alarm_row(source_alarm_id)
                if alarm["asset_id"] != asset_id:
                    raise Invalid(f"alarm {source_alarm_id} belongs to {alarm['asset_id']}, not {asset_id}")
                if alarm["state"] == "cleared":
                    raise Conflict(f"alarm {source_alarm_id} is already cleared; omit source_alarm_id "
                                   "if the work is still needed")  # fmt: skip
                dup = self.db.execute(
                    f"SELECT id FROM work_orders WHERE source_alarm_id = ? AND status IN ({_ACTIVE_SQL})",
                    (source_alarm_id,),
                ).fetchone()  # fmt: skip
                if dup:
                    raise Conflict(f"alarm {source_alarm_id} already has an active work order: {dup['id']}")
            wo_id, now = self._next_wo_id(), self._now()
            self.db.execute(
                "INSERT INTO work_orders (id, asset_id, title, description, wo_type, priority, status, "
                "assignee, created_by, created_at, updated_at, due_date, completed_at, source_alarm_id) "
                "VALUES (?, ?, ?, ?, ?, ?, 'open', NULL, ?, ?, ?, ?, NULL, ?)",
                (wo_id, asset_id, title, description, wo_type, priority, actor, now, now, due_date,
                 source_alarm_id),
            )  # fmt: skip
            self._event(wo_id, actor, "created", None, "open", None)
            return self._detail(wo_id)

    def assign_work_order(self, wo_id: str, assignee: str, actor: str) -> WorkOrderDetail:
        with self._lock, self._tx():
            user = self._user(actor)
            if user["role"] != "supervisor":
                raise Forbidden("only a supervisor can assign work orders")
            target = self._user(assignee)
            if target["role"] == "viewer":
                raise Invalid(f"{assignee} is a viewer and cannot be assigned work")
            wo = self._wo_row(wo_id)
            if wo["status"] not in ("open", "assigned"):
                raise Invalid(f"a work order in status '{wo['status']}' cannot be (re)assigned")
            self.db.execute(
                "UPDATE work_orders SET assignee = ?, status = 'assigned', updated_at = ? WHERE id = ?",
                (assignee, self._now(), wo_id),
            )  # fmt: skip
            self._event(wo_id, actor, "assigned", wo["status"], "assigned", f"Assigned to {assignee}")
            return self._detail(wo_id)

    def transition_work_order(self, wo_id: str, to_status: str, actor: str,
                              note: str | None = None) -> WorkOrderDetail:  # fmt: skip
        with self._lock, self._tx():
            user = self._user(actor)
            wo = self._wo_row(wo_id)
            current = wo["status"]
            sm.check(current, to_status)
            if current == "open" and to_status == "assigned":
                raise Invalid("use assign_work_order to assign; it also records who the assignee is")
            reopen = current == "completed" and to_status == "in_progress"
            supervisor = user["role"] == "supervisor"
            if to_status in ("closed", "cancelled", "open") or reopen:
                if not supervisor:
                    raise Forbidden(f"only a supervisor can move a work order to '{to_status}'"
                                    + (" (reopen)" if reopen else ""))  # fmt: skip
            elif not (supervisor or wo["assignee"] == actor):
                raise Forbidden(f"only the assignee ({wo['assignee']}) or a supervisor can set '{to_status}'")
            note_text = _text(note, "note", hi=500)
            if sm.needs_note(current, to_status) and len(note_text) < 5:
                raise Invalid(f"a note of at least 5 characters is required to move to '{to_status}'")
            now = self._now()
            assignee = None if to_status == "open" else wo["assignee"]
            completed_at = now if to_status == "completed" else (None if reopen else wo["completed_at"])
            self.db.execute(
                "UPDATE work_orders SET status = ?, assignee = ?, completed_at = ?, updated_at = ? "
                "WHERE id = ?", (to_status, assignee, completed_at, now, wo_id),
            )  # fmt: skip
            self._event(wo_id, actor, "reopened" if reopen else "transition", current, to_status,
                        note_text or None)  # fmt: skip
            return self._detail(wo_id)


class _Tx:
    """All-or-nothing writes. Nested use joins the outer transaction."""

    def __init__(self, svc: Cmms) -> None:
        self.db = svc.db

    def __enter__(self):
        self.outer = self.db.in_transaction
        if not self.outer:
            self.db.execute("BEGIN IMMEDIATE")

    def __exit__(self, exc_type, exc, tb):
        if self.outer:
            return False
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
