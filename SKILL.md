---
name: cmms-triage
description: Use when working with the plant CMMS MCP server (assets, alarms, work orders) - triaging alarms, creating or updating work orders, preparing shift handovers. Explains the order of calls, the safety rules for writes and how to read errors.
---

# CMMS triage

Companion skill for the `cmms-mcp-server` tools. The server is the source of truth; this file only
says how to use it well.

## Before anything else

1. Call `get_plant_overview`. It tells you what is on fire (critical alarms, overdue work) and which
   critical (class A) assets have alarms nobody has acknowledged.
2. Read the `cmms://glossary` resource once if you are unsure what a status or role means.

## Triage an alarm

1. `list_alarms(state="active", severity="critical")`, then `warning`. Work most severe first.
2. `get_asset(asset_id)` - shows the asset's other alarms, **its active work orders** and the last
   completed one. If a work order already covers the problem, do **not** create another: report it
   (and add context with `transition_work_order` only if the user asks).
3. Acknowledge with `acknowledge_alarm` (safe to repeat).
4. If work is needed, `create_work_order` with `source_alarm_id` set. One alarm can have only one
   active work order; the server rejects duplicates and names the existing one.
5. Assigning, closing, cancelling and reopening are supervisor actions. If the user is not a
   supervisor, say so and stop; do not try another username.

## Rules for every write

- `actor` is the username of the person you are acting for. Ask if you do not have it. Never make one up
  and never reuse another person's.
- Say what you are about to change (asset, alarm or work order id, new status) and get a clear yes
  before calling a write tool, unless the user already gave that exact instruction.
- Notes are part of the record. Write them for the next reader: what was found, what was done, what is
  still open. The server requires a note (5+ characters) to hold, complete, cancel, unassign or reopen.
- Do not loop on errors. Each error says what was wrong and, for status changes, what is allowed. Fix the
  call once; if it still fails, report the error to the user.

## Work-order life cycle

`open -> assigned -> in_progress <-> on_hold -> completed -> closed`; `cancelled` from open, assigned or
on_hold; a completed order can be reopened (supervisor). Use `assign_work_order` for `open -> assigned`;
`transition_work_order` for everything else.

## Reading results

- Lists return `items`, `total` and `next_offset`. If `next_offset` is not null there is more; ask
  before paging through a long list.
- `overdue` on a work order means the due date has passed and it is still active.
- Timestamps are UTC (`...Z`).

## Shift handover

Use the `shift_handover` prompt, or: overview -> active critical and warning alarms -> overdue work
orders -> work orders on hold. For each item give asset, problem, owner and what the next shift must do.
This is read-only: make no changes while preparing a handover.
