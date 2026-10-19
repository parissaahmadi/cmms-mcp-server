"""Typed results. Tools return these, so every tool gets an output schema."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Asset(BaseModel):
    id: str
    name: str
    type: str
    location: str
    manufacturer: str
    model: str
    serial: str
    criticality: str = Field(description="A (critical) / B / C")
    status: str = Field(description="running / stopped / maintenance")
    installed_on: str
    meter_hours: int


class Alarm(BaseModel):
    id: str
    asset_id: str
    severity: str
    code: str
    message: str
    raised_at: str
    state: str = Field(description="active / acknowledged / cleared")
    acknowledged_by: str | None = None
    acknowledged_at: str | None = None
    cleared_at: str | None = None


class WorkOrder(BaseModel):
    id: str
    asset_id: str
    title: str
    description: str
    wo_type: str
    priority: str
    status: str
    assignee: str | None = None
    created_by: str
    created_at: str
    updated_at: str
    due_date: str | None = None
    completed_at: str | None = None
    source_alarm_id: str | None = None
    overdue: bool = False


class WorkOrderEvent(BaseModel):
    at: str
    actor: str
    action: str
    from_status: str | None = None
    to_status: str | None = None
    note: str | None = None


class WorkOrderDetail(BaseModel):
    work_order: WorkOrder
    events: list[WorkOrderEvent]


class AssetDetail(BaseModel):
    asset: Asset
    active_alarms: list[Alarm]
    open_work_orders: list[WorkOrder]
    last_completed_work_order: WorkOrder | None = None


class AssetPage(BaseModel):
    items: list[Asset]
    total: int
    next_offset: int | None = None


class AlarmPage(BaseModel):
    items: list[Alarm]
    total: int
    next_offset: int | None = None


class WorkOrderPage(BaseModel):
    items: list[WorkOrder]
    total: int
    next_offset: int | None = None


class AckResult(BaseModel):
    alarm: Alarm
    already_acknowledged: bool


class Overview(BaseModel):
    as_of: str
    assets_by_status: dict[str, int]
    active_alarms_by_severity: dict[str, int]
    work_orders_by_status: dict[str, int]
    overdue_work_orders: int
    critical_assets_with_active_alarms: list[str]
