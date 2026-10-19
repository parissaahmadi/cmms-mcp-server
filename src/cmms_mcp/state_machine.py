"""Work-order life cycle."""

from __future__ import annotations

from .errors import Invalid

STATUSES = ("open", "assigned", "in_progress", "on_hold", "completed", "closed", "cancelled")
TERMINAL = frozenset({"closed", "cancelled"})
# "Still needs work": used for overdue checks and duplicate prevention.
ACTIVE = frozenset({"open", "assigned", "in_progress", "on_hold"})

TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"assigned", "cancelled"}),
    "assigned": frozenset({"in_progress", "open", "cancelled"}),
    "in_progress": frozenset({"on_hold", "completed"}),
    "on_hold": frozenset({"in_progress", "cancelled"}),
    "completed": frozenset({"closed", "in_progress"}),
    "closed": frozenset(),
    "cancelled": frozenset(),
}

# Targets that need a written reason. Reopening (completed -> in_progress) does too.
NOTE_REQUIRED = frozenset({"on_hold", "completed", "cancelled", "open"})


def check(current: str, target: str) -> None:
    if target not in STATUSES:
        raise Invalid(f"unknown status {target!r}; expected one of {', '.join(STATUSES)}")
    if target not in TRANSITIONS[current]:
        allowed = ", ".join(sorted(TRANSITIONS[current])) or "none (terminal state)"
        raise Invalid(f"cannot move a work order from '{current}' to '{target}'; allowed: {allowed}")


def needs_note(current: str, target: str) -> bool:
    return target in NOTE_REQUIRED or (current == "completed" and target == "in_progress")
