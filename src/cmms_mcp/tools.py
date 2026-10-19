"""MCP surface: tools, one resource and one prompt. Thin wrappers over ``service.Cmms``."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Literal, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import models as m
from .errors import CmmsError
from .service import Cmms

T = TypeVar("T")

INSTRUCTIONS = """\
This server exposes a maintenance system (CMMS) for a fictional plant: assets, alarms and work orders.
Start with get_plant_overview. Use list_* tools to find things, get_* tools for detail.
Write tools (acknowledge_alarm, create_work_order, assign_work_order, transition_work_order) change
real records and are attributed to the `actor` you pass: use the username the person gave you and
never invent one. Before a write, check for an existing work order on the same asset or alarm.
Errors name what was wrong and what is allowed; read them and correct the call instead of retrying it unchanged.
"""

GLOSSARY = """\
Asset criticality: A = production stops or safety risk if it fails; B = degraded output; C = low impact.
Alarm severity: critical > warning > info. Alarm state: active (nobody has looked) -> acknowledged
(someone owns it) -> cleared (condition gone).
Work-order status: open -> assigned -> in_progress <-> on_hold -> completed -> closed.
  cancelled is possible from open, assigned and on_hold; a completed order can be reopened.
Roles: viewer (read only), technician (acknowledge, create, work on assigned orders),
  supervisor (everything, including assign, close, cancel, reopen).
Priority: urgent > high > medium > low. Overdue = due date passed and the work order is still active.
"""

READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)
WRITE_IDEMPOTENT = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)

Limit = Annotated[int, Field(ge=1, le=100, description="Page size (1-100).")]
Offset = Annotated[int, Field(ge=0, description="Items to skip; use next_offset from the previous page.")]
AssetId = Annotated[str, Field(description="Asset id such as AST-0001.")]
AlarmId = Annotated[str, Field(description="Alarm id such as ALM-0001.")]
WoId = Annotated[str, Field(description="Work-order id such as WO-2026-0001.")]
Actor = Annotated[str, Field(description="Username performing the action, e.g. 'tech.a'. Never guess one.")]
Status = Annotated[
    Literal["open", "assigned", "in_progress", "on_hold", "completed", "closed", "cancelled"],
    Field(description="Work-order status."),
]
Priority = Annotated[Literal["low", "medium", "high", "urgent"], Field(description="Work-order priority.")]


def _run(fn: Callable[[], T]) -> T:
    """Domain errors become tool errors with a readable message; anything else stays generic."""
    try:
        return fn()
    except CmmsError as e:
        raise ToolError(f"{e.code}: {e}") from e


def build_server(svc: Cmms, *, read_only: bool = False) -> MCPServer:
    mcp = MCPServer(
        "cmms", title="Plant CMMS (fictional)", instructions=INSTRUCTIONS, version="0.1.0"
    )

    # ------------------------------------------------------------------ reads
    @mcp.tool(annotations=READ)
    def get_plant_overview() -> m.Overview:
        """Counts of assets, active alarms and work orders, overdue work, and critical (A) assets with unacknowledged alarms. Call this first."""
        return _run(svc.overview)

    @mcp.tool(annotations=READ)
    def list_assets(
        status: Annotated[Literal["running", "stopped", "maintenance"] | None, Field(description="Filter by status.")] = None,
        type: Annotated[str | None, Field(description="Exact asset type, e.g. 'pump' or 'compressor'.")] = None,
        location: Annotated[str | None, Field(description="Exact location name, e.g. 'Utilities'.")] = None,
        criticality: Annotated[Literal["A", "B", "C"] | None, Field(description="A is most critical.")] = None,
        query: Annotated[str | None, Field(description="Text search over id, name, type, location, maker, model, serial.")] = None,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> m.AssetPage:
        """List assets, most critical first. All filters are optional and combine with AND."""
        return _run(lambda: svc.list_assets(status=status, type=type, location=location,
                                            criticality=criticality, query=query, limit=limit, offset=offset))  # fmt: skip

    @mcp.tool(annotations=READ)
    def get_asset(asset_id: AssetId) -> m.AssetDetail:
        """One asset with its uncleared alarms, its active work orders and its last completed work order."""
        return _run(lambda: svc.get_asset(asset_id))

    @mcp.tool(annotations=READ)
    def list_alarms(
        state: Annotated[Literal["active", "acknowledged", "cleared"] | None, Field(description="'active' means nobody has acknowledged it yet.")] = None,
        severity: Annotated[Literal["info", "warning", "critical"] | None, Field(description="Filter by severity.")] = None,
        asset_id: Annotated[str | None, Field(description="Only alarms of this asset, e.g. AST-0001.")] = None,
        since: Annotated[str | None, Field(description="Only alarms raised at or after this ISO-8601 time (UTC if no zone).")] = None,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> m.AlarmPage:
        """List alarms, most severe and most recent first."""
        return _run(lambda: svc.list_alarms(state=state, severity=severity, asset_id=asset_id,
                                            since=since, limit=limit, offset=offset))  # fmt: skip

    @mcp.tool(annotations=READ)
    def list_work_orders(
        status: Annotated[Literal["open", "assigned", "in_progress", "on_hold", "completed", "closed", "cancelled"] | None, Field(description="Filter by status.")] = None,
        priority: Annotated[Literal["low", "medium", "high", "urgent"] | None, Field(description="Filter by priority.")] = None,
        asset_id: Annotated[str | None, Field(description="Only work orders of this asset, e.g. AST-0001.")] = None,
        assignee: Annotated[str | None, Field(description="Username the work is assigned to.")] = None,
        overdue_only: Annotated[bool, Field(description="Only active work orders past their due date.")] = False,
        limit: Limit = 20,
        offset: Offset = 0,
    ) -> m.WorkOrderPage:
        """List work orders, highest priority first, then earliest due date."""
        return _run(lambda: svc.list_work_orders(status=status, priority=priority, asset_id=asset_id,
                                                 assignee=assignee, overdue_only=overdue_only,
                                                 limit=limit, offset=offset))  # fmt: skip

    @mcp.tool(annotations=READ)
    def get_work_order(work_order_id: WoId) -> m.WorkOrderDetail:
        """One work order with its full history of events (who did what, when, and why)."""
        return _run(lambda: svc.get_work_order(work_order_id))

    # ------------------------------------------------------------------ writes
    if not read_only:

        @mcp.tool(annotations=WRITE_IDEMPOTENT)
        def acknowledge_alarm(alarm_id: AlarmId, actor: Actor) -> m.AckResult:
            """Take ownership of an active alarm. Safe to repeat: an already acknowledged alarm is returned unchanged with already_acknowledged=true."""
            return _run(lambda: svc.acknowledge_alarm(alarm_id, actor))

        @mcp.tool(annotations=WRITE)
        def create_work_order(
            actor: Actor,
            asset_id: AssetId,
            title: Annotated[str, Field(description="5-120 characters.")],
            description: Annotated[str, Field(description="What is wrong / what to do, up to 2000 characters.")] = "",
            priority: Priority = "medium",
            wo_type: Annotated[Literal["corrective", "preventive", "inspection"], Field(description="Kind of work.")] = "corrective",
            due_date: Annotated[str | None, Field(description="YYYY-MM-DD, today or later.")] = None,
            source_alarm_id: Annotated[str | None, Field(description="Alarm that triggered this work. Must belong to the same asset and not be cleared. An alarm can have only one active work order.")] = None,
        ) -> m.WorkOrderDetail:
            """Create a new work order (status open). Check list_work_orders for the asset first to avoid duplicates."""
            return _run(lambda: svc.create_work_order(actor=actor, asset_id=asset_id, title=title,
                                                      description=description, priority=priority,
                                                      wo_type=wo_type, due_date=due_date,
                                                      source_alarm_id=source_alarm_id))  # fmt: skip

        @mcp.tool(annotations=WRITE_IDEMPOTENT)
        def assign_work_order(work_order_id: WoId, assignee: Annotated[str, Field(description="Username of a technician or supervisor.")], actor: Actor) -> m.WorkOrderDetail:
            """Assign (or reassign) an open or assigned work order. Supervisors only."""
            return _run(lambda: svc.assign_work_order(work_order_id, assignee, actor))

        @mcp.tool(annotations=WRITE)
        def transition_work_order(
            work_order_id: WoId,
            to_status: Status,
            actor: Actor,
            note: Annotated[str | None, Field(description="Reason or result. Required (5+ characters) for on_hold, completed, cancelled, open (unassign) and reopening.")] = None,
        ) -> m.WorkOrderDetail:
            """Move a work order along its life cycle. The assignee or a supervisor can start, hold and complete; only a supervisor can close, cancel, unassign or reopen. Use assign_work_order for open -> assigned."""
            return _run(lambda: svc.transition_work_order(work_order_id, to_status, actor, note))

    # ------------------------------------------------------------------ resource + prompt
    @mcp.resource("cmms://glossary", mime_type="text/plain")
    def glossary() -> str:
        """Meaning of criticality, severities, statuses, roles and priorities."""
        return GLOSSARY

    @mcp.prompt()
    def shift_handover(area: str = "the whole plant") -> str:
        """Ask the assistant to prepare a shift handover note."""
        return (
            f"Prepare a shift handover note for {area}. Use get_plant_overview, then list active critical "
            "and warning alarms, overdue work orders and anything on hold. For each item give the asset, "
            "what is wrong, who owns it and what the next shift must do. Do not change any records."
        )

    return mcp
