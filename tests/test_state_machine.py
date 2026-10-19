import pytest

from cmms_mcp import state_machine as sm
from cmms_mcp.errors import Invalid


def test_every_status_has_a_row_and_targets_are_known():
    assert set(sm.TRANSITIONS) == set(sm.STATUSES)
    for targets in sm.TRANSITIONS.values():
        assert targets <= set(sm.STATUSES)


def test_terminal_states_have_no_exits():
    for s in sm.TERMINAL:
        assert sm.TRANSITIONS[s] == frozenset()


def test_every_state_is_reachable_from_open():
    seen, todo = {"open"}, ["open"]
    while todo:
        for nxt in sm.TRANSITIONS[todo.pop()]:
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    assert seen == set(sm.STATUSES)


@pytest.mark.parametrize(
    "a,b",
    [("open", "assigned"), ("assigned", "in_progress"), ("in_progress", "completed"),
     ("completed", "closed"), ("completed", "in_progress"), ("on_hold", "in_progress")],
)  # fmt: skip
def test_allowed(a, b):
    sm.check(a, b)


@pytest.mark.parametrize(
    "a,b",
    [("open", "completed"), ("open", "in_progress"), ("closed", "open"), ("cancelled", "open"),
     ("in_progress", "closed"), ("completed", "cancelled"), ("assigned", "assigned")],
)  # fmt: skip
def test_forbidden(a, b):
    with pytest.raises(Invalid, match="cannot move"):
        sm.check(a, b)


def test_unknown_target():
    with pytest.raises(Invalid, match="unknown status"):
        sm.check("open", "done")


def test_note_rules():
    assert sm.needs_note("in_progress", "completed")
    assert sm.needs_note("completed", "in_progress")  # reopen
    assert sm.needs_note("assigned", "open")  # unassign
    assert not sm.needs_note("assigned", "in_progress")
    assert not sm.needs_note("completed", "closed")
