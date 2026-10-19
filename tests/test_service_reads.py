import pytest

from cmms_mcp.errors import Invalid, NotFound
from helpers import make


def test_overview_counts_match_the_seed():
    o = make().overview()
    assert o.assets_by_status == {"running": 13, "stopped": 2, "maintenance": 1}
    assert o.active_alarms_by_severity == {"info": 2, "warning": 3, "critical": 1}
    assert sum(o.work_orders_by_status.values()) == 9
    assert o.overdue_work_orders == 2
    assert o.as_of == "2026-10-09T06:00:00Z"
    assert o.critical_assets_with_active_alarms == ["AST-0001", "AST-0007", "AST-0008"]


def test_list_assets_filters_and_pages():
    s = make()
    page = s.list_assets(limit=5)
    assert page.total == 16 and len(page.items) == 5 and page.next_offset == 5
    last = s.list_assets(limit=5, offset=15)
    assert len(last.items) == 1 and last.next_offset is None
    ids = [a.id for p in (0, 5, 10, 15) for a in s.list_assets(limit=5, offset=p).items]
    assert len(ids) == len(set(ids)) == 16  # paging neither skips nor repeats
    assert {a.type for a in s.list_assets(type="pump").items} == {"pump"}
    assert [a.id for a in s.list_assets(status="stopped").items] == ["AST-0004", "AST-0016"]
    assert all(a.criticality == "A" for a in s.list_assets(criticality="A").items)
    assert [a.id for a in s.list_assets(query="vortex").items] == ["AST-0009", "AST-0010", "AST-0014"]


def test_search_treats_wildcards_literally_and_resists_injection():
    s = make()
    assert s.list_assets(query="%").total == 0
    # a literal underscore matches only the two types that contain one (unescaped it would match all)
    assert [a.id for a in s.list_assets(query="_").items] == ["AST-0007", "AST-0014"]
    assert s.list_assets(query="x' OR '1'='1").total == 0
    assert s.list_assets(query="'; DROP TABLE assets; --").total == 0
    assert s.list_assets().total == 16  # table still there


@pytest.mark.parametrize(
    "kwargs",
    [{"status": "broken"}, {"criticality": "Z"}, {"limit": 0}, {"limit": 101}, {"offset": -1}],
)
def test_list_assets_rejects_bad_input(kwargs):
    with pytest.raises(Invalid):
        make().list_assets(**kwargs)


def test_get_asset_detail():
    d = make().get_asset("AST-0001")
    assert d.asset.name == "Screw compressor C-1"
    assert [a.code for a in d.active_alarms] == ["OIL_TEMP_HIGH", "PRESS_DROP"]  # critical first
    assert [w.id for w in d.open_work_orders] == ["WO-2026-0003"]
    assert d.last_completed_work_order is None
    d9 = make().get_asset("AST-0009")
    assert d9.last_completed_work_order.id == "WO-2026-0005"
    assert [a.state for a in d9.active_alarms] == ["acknowledged"]


def test_get_asset_unknown_and_malformed():
    with pytest.raises(NotFound):
        make().get_asset("AST-9999")
    with pytest.raises(Invalid, match="AST-0001"):
        make().get_asset("1; DROP")


def test_alarm_filters_and_order():
    s = make()
    active = s.list_alarms(state="active")
    assert [a.severity for a in active.items] == ["critical", "warning", "warning", "warning", "info", "info"]
    assert [a.id for a in s.list_alarms(asset_id="AST-0001").items] == ["ALM-0001", "ALM-0002"]
    assert s.list_alarms(severity="critical").total == 4
    recent = s.list_alarms(since="2026-10-09T00:00:00Z")
    assert {a.id for a in recent.items} == {"ALM-0004", "ALM-0010"}
    assert s.list_alarms(since="2026-10-09T00:00:00").total == 2  # naive time is treated as UTC
    with pytest.raises(Invalid, match="since"):
        s.list_alarms(since="yesterday")


def test_work_order_filters_order_and_overdue_flag():
    s = make()
    allwo = s.list_work_orders()
    assert allwo.items[0].id == "WO-2026-0002"  # urgent first
    assert [w.id for w in s.list_work_orders(overdue_only=True).items] == ["WO-2026-0002", "WO-2026-0009"]
    assert all(w.overdue for w in s.list_work_orders(overdue_only=True).items)
    by_id = {w.id: w for w in allwo.items}
    assert not by_id["WO-2026-0005"].overdue  # completed work is never overdue
    assert not by_id["WO-2026-0001"].overdue  # due tomorrow
    assert [w.id for w in s.list_work_orders(assignee="tech.a").items] == ["WO-2026-0001", "WO-2026-0004"]
    assert s.list_work_orders(status="open", priority="low").total == 1


def test_overdue_depends_on_the_clock():
    s = make()
    assert not s.get_work_order("WO-2026-0001").work_order.overdue
    from datetime import UTC, datetime

    s.box["now"] = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    assert s.get_work_order("WO-2026-0001").work_order.overdue


def test_get_work_order_has_history():
    d = make().get_work_order("WO-2026-0004")
    assert [e.to_status for e in d.events] == ["open", "assigned", "in_progress", "on_hold"]
    assert "seal kit" in d.events[-1].note
    with pytest.raises(NotFound):
        make().get_work_order("WO-2026-0099")
    with pytest.raises(Invalid):
        make().get_work_order("WO-26-1")
