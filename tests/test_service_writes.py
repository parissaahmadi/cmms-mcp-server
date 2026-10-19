import pytest

from cmms_mcp.errors import Conflict, Forbidden, Invalid, NotFound
from helpers import make


# ---------------------------------------------------------------- acknowledge
def test_acknowledge_changes_state_and_records_who_and_when():
    s = make()
    r = s.acknowledge_alarm("ALM-0001", "tech.a")
    assert not r.already_acknowledged
    assert (r.alarm.state, r.alarm.acknowledged_by, r.alarm.acknowledged_at) == (
        "acknowledged", "tech.a", "2026-10-09T06:00:00Z")  # fmt: skip


def test_acknowledge_is_idempotent_and_keeps_the_first_acknowledger():
    s = make()
    s.acknowledge_alarm("ALM-0001", "tech.a")
    s.box["now"] = s.box["now"].replace(hour=9)
    again = s.acknowledge_alarm("ALM-0001", "sup.c")
    assert again.already_acknowledged
    assert again.alarm.acknowledged_by == "tech.a" and again.alarm.acknowledged_at.endswith("06:00:00Z")


def test_acknowledge_rules():
    s = make()
    with pytest.raises(Forbidden, match="viewer"):
        s.acknowledge_alarm("ALM-0001", "view.d")
    with pytest.raises(Forbidden, match="unknown user"):
        s.acknowledge_alarm("ALM-0001", "nobody")
    with pytest.raises(Conflict, match="cleared"):
        s.acknowledge_alarm("ALM-0007", "tech.a")
    with pytest.raises(NotFound):
        s.acknowledge_alarm("ALM-0999", "tech.a")
    assert s.list_alarms(state="active").total == 6  # nothing above changed anything


# ---------------------------------------------------------------- create
def create(s, **kw):
    base = dict(actor="tech.a", asset_id="AST-0002", title="Replace intake filter")
    return s.create_work_order(**{**base, **kw})


def test_create_work_order_defaults_and_ids():
    s = make()
    d = create(s)
    wo = d.work_order
    assert wo.id == "WO-2026-0010" and wo.status == "open" and wo.priority == "medium"
    assert wo.created_by == "tech.a" and wo.assignee is None and wo.wo_type == "corrective"
    assert [(e.action, e.to_status) for e in d.events] == [("created", "open")]
    assert create(s).work_order.id == "WO-2026-0011"


def test_ids_restart_each_year():
    from datetime import UTC, datetime

    s = make()
    s.box["now"] = datetime(2027, 1, 5, 8, 0, tzinfo=UTC)
    assert create(s).work_order.id == "WO-2027-0001"


def test_create_from_alarm_links_it_and_blocks_duplicates():
    s = make()
    d = create(s, asset_id="AST-0001", source_alarm_id="ALM-0001", priority="urgent")
    assert d.work_order.source_alarm_id == "ALM-0001"
    with pytest.raises(Conflict, match=d.work_order.id):
        create(s, asset_id="AST-0001", source_alarm_id="ALM-0001")
    # once that work order is cancelled a new one may be raised for the same alarm
    s.transition_work_order(d.work_order.id, "cancelled", "sup.c", "Raised by mistake")
    assert create(s, asset_id="AST-0001", source_alarm_id="ALM-0001").work_order.id.endswith("0011")


@pytest.mark.parametrize(
    "kwargs,exc,msg",
    [
        ({"actor": "view.d"}, Forbidden, "viewer"),
        ({"asset_id": "AST-9999"}, NotFound, "does not exist"),
        ({"title": "x"}, Invalid, "title"),
        ({"title": "x" * 121}, Invalid, "title"),
        ({"description": "d" * 2001}, Invalid, "description"),
        ({"priority": "asap"}, Invalid, "priority"),
        ({"wo_type": "magic"}, Invalid, "wo_type"),
        ({"due_date": "2026-10-08"}, Invalid, "past"),
        ({"due_date": "next week"}, Invalid, "YYYY-MM-DD"),
        ({"source_alarm_id": "ALM-0003"}, Invalid, "belongs to AST-0003"),
        ({"asset_id": "AST-0007", "source_alarm_id": "ALM-0008"}, Invalid, "belongs to"),
        ({"asset_id": "AST-0010", "source_alarm_id": "ALM-0007"}, Conflict, "cleared"),
    ],
)
def test_create_rejects_bad_input_and_changes_nothing(kwargs, exc, msg):
    s = make()
    before = s.list_work_orders().total
    with pytest.raises(exc, match=msg):
        create(s, **kwargs)
    assert s.list_work_orders().total == before


def test_due_today_is_allowed():
    assert create(make(), due_date="2026-10-09").work_order.due_date == "2026-10-09"


# ---------------------------------------------------------------- assign
def test_assign_rules():
    s = make()
    wo = create(s).work_order.id
    with pytest.raises(Forbidden, match="supervisor"):
        s.assign_work_order(wo, "tech.b", "tech.a")
    with pytest.raises(Invalid, match="viewer"):
        s.assign_work_order(wo, "view.d", "sup.c")
    with pytest.raises(Forbidden, match="unknown"):
        s.assign_work_order(wo, "ghost", "sup.c")
    d = s.assign_work_order(wo, "tech.b", "sup.c")
    assert (d.work_order.status, d.work_order.assignee) == ("assigned", "tech.b")
    assert d.events[-1].note == "Assigned to tech.b"
    d = s.assign_work_order(wo, "tech.e", "sup.c")  # reassign
    assert d.work_order.assignee == "tech.e"
    with pytest.raises(Invalid, match="cannot be"):
        s.assign_work_order("WO-2026-0007", "tech.a", "sup.c")  # closed


# ---------------------------------------------------------------- transitions
def started(s):
    wo = create(s).work_order.id
    s.assign_work_order(wo, "tech.b", "sup.c")
    return wo


def test_full_life_cycle():
    s = make()
    wo = started(s)
    s.transition_work_order(wo, "in_progress", "tech.b")
    s.box["now"] = s.box["now"].replace(hour=10)
    d = s.transition_work_order(wo, "completed", "tech.b", "Filter replaced, pressure normal.")
    assert d.work_order.completed_at == "2026-10-09T10:00:00Z"
    d = s.transition_work_order(wo, "closed", "sup.c")
    assert d.work_order.status == "closed"
    assert [e.to_status for e in d.events] == ["open", "assigned", "in_progress", "completed", "closed"]
    with pytest.raises(Invalid, match="terminal"):
        s.transition_work_order(wo, "open", "sup.c", "try again please")


def test_transition_permissions():
    s = make()
    wo = started(s)
    with pytest.raises(Forbidden, match="assignee"):
        s.transition_work_order(wo, "in_progress", "tech.a")  # not the assignee
    with pytest.raises(Forbidden, match="viewer|assignee"):
        s.transition_work_order(wo, "in_progress", "view.d")
    s.transition_work_order(wo, "in_progress", "sup.c")  # supervisor may act for anyone
    s.transition_work_order(wo, "completed", "tech.b", "Done and tested.")
    with pytest.raises(Forbidden, match="supervisor"):
        s.transition_work_order(wo, "closed", "tech.b")
    with pytest.raises(Forbidden, match="reopen"):
        s.transition_work_order(wo, "in_progress", "tech.b", "needs more work")


def test_cancel_and_unassign_are_supervisor_only():
    s = make()
    wo = started(s)
    with pytest.raises(Forbidden):
        s.transition_work_order(wo, "cancelled", "tech.b", "no longer needed")
    d = s.transition_work_order(wo, "open", "sup.c", "Reassigning to another shift")
    assert d.work_order.assignee is None and d.work_order.status == "open"


def test_notes_are_required_where_the_rules_say_so():
    s = make()
    wo = started(s)
    s.transition_work_order(wo, "in_progress", "tech.b")  # no note needed
    for note in (None, "", "   ", "ok"):
        with pytest.raises(Invalid, match="note"):
            s.transition_work_order(wo, "on_hold", "tech.b", note)
    assert s.get_work_order(wo).work_order.status == "in_progress"
    s.transition_work_order(wo, "on_hold", "tech.b", "Waiting for parts.")


def test_reopen_clears_completed_at_and_logs_it():
    s = make()
    d = s.transition_work_order("WO-2026-0005", "in_progress", "sup.c", "Seal is leaking again")
    assert d.work_order.completed_at is None
    assert d.events[-1].action == "reopened"
    assert s.get_asset("AST-0009").last_completed_work_order is None


def test_invalid_transitions_and_assigned_shortcut():
    s = make()
    with pytest.raises(Invalid, match="use assign_work_order"):
        s.transition_work_order("WO-2026-0003", "assigned", "sup.c")
    with pytest.raises(Invalid, match="cannot move"):
        s.transition_work_order("WO-2026-0003", "completed", "sup.c", "skipping ahead")
    with pytest.raises(Invalid, match="unknown status"):
        s.transition_work_order("WO-2026-0003", "done", "sup.c")
    with pytest.raises(NotFound):
        s.transition_work_order("WO-2026-0099", "cancelled", "sup.c", "does not exist")


def test_failed_write_rolls_back_completely():
    s = make()
    wo = started(s)
    events_before = len(s.get_work_order(wo).events)
    s.db.execute("CREATE TRIGGER boom BEFORE INSERT ON wo_events BEGIN SELECT RAISE(ABORT, 'disk on fire'); END")
    with pytest.raises(Exception, match="disk on fire"):
        s.transition_work_order(wo, "in_progress", "tech.b")
    s.db.execute("DROP TRIGGER boom")
    d = s.get_work_order(wo)
    assert d.work_order.status == "assigned"  # the UPDATE was rolled back with the failed INSERT
    assert len(d.events) == events_before
    assert not s.db.in_transaction
