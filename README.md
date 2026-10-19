![ci](https://github.com/<USERNAME>/cmms-mcp-server/actions/workflows/ci.yml/badge.svg)

# cmms-mcp-server

An [MCP](https://modelcontextprotocol.io) server that lets an AI assistant work with a **maintenance
management system (CMMS)**: look up assets, read and acknowledge alarms, and create and move work orders
through a real life cycle, with role checks and an audit trail.

Everything runs on **fictional data** (a made-up plant with Quanta compressors, Ironwood presses, Helix
lathes, Nimbus chillers and Vortex pumps; 16 assets, 12 alarms, 9 work orders, 5 users). There is no
connection to any real system.

```
AI client  --MCP/stdio-->  tools.py (schemas, annotations, error mapping)
                              |
                          service.py (validation, roles, state machine, transactions)
                              |
                          SQLite (db.py)
```

The domain logic in `service.py` knows nothing about MCP, so it is tested directly, and the MCP layer is
tested separately, in-process and over a real stdio subprocess.

## Run it

```bash
uv sync
uv run cmms-mcp-server                                        # stdio, in-memory, reseeded each start
uv run cmms-mcp-server --db plant.sqlite3                     # persistent (created and seeded when missing)
uv run cmms-mcp-server --read-only                            # write tools are not registered at all
uv run cmms-mcp-server --fixed-now 2026-10-09T06:00:00Z       # freeze the clock (the seed is set on that day)
uv run python examples/demo_client.py                         # scripted client session, see below
```

Add it to an MCP client with `.mcp.json.example` (command `uv run --directory ... cmms-mcp-server`).
`SKILL.md` is a companion skill that teaches an assistant the order of calls and the safety rules.

## Tools

| tool | kind | what it does |
|---|---|---|
| `get_plant_overview` | read | counts of assets/alarms/work orders, overdue work, class-A assets with unacknowledged alarms |
| `list_assets` | read | filter by status, type, location, criticality, free text; paged |
| `get_asset` | read | one asset + its uncleared alarms, active work orders, last completed work order |
| `list_alarms` | read | filter by state, severity, asset, time; most severe first |
| `list_work_orders` | read | filter by status, priority, asset, assignee, overdue; highest priority first |
| `get_work_order` | read | one work order with its full event history |
| `acknowledge_alarm` | write, idempotent | take ownership of an alarm; repeating it changes nothing |
| `create_work_order` | write | optionally linked to an alarm; **one active work order per alarm** |
| `assign_work_order` | write | supervisors only |
| `transition_work_order` | write | the life cycle below, with role and note rules |

Also: resource `cmms://glossary` and prompt `shift_handover`.

Work-order life cycle:

```
open -> assigned -> in_progress <-> on_hold -> completed -> closed
  \        \                          \          \-> in_progress   (reopen, supervisor)
   `--------`--------------------------`-> cancelled
```

| role | may |
|---|---|
| viewer | read |
| technician | acknowledge, create, start / hold / complete work assigned to them |
| supervisor | everything: assign, unassign, close, cancel, reopen, act for anyone |

A note (5+ characters) is required to hold, complete, cancel, unassign or reopen.

## What a session looks like

Real output of `examples/demo_client.py` (long values shortened by the script):

```
> acknowledge_alarm as a viewer (must fail)
  ERROR Error executing tool acknowledge_alarm: forbidden: view.d is a viewer and cannot acknowledge alarms

> acknowledge_alarm as tech.a
  {"alarm": {"id": "ALM-0001", "asset_id": "AST-0001", "severity": "critical", "code": "OIL_TEMP_HIGH", ...

> create_work_order
  {"work_order": {"id": "WO-2026-0010", "asset_id": "AST-0001", "title": "Oil temperature high: inspect cooler", ...

> create_work_order again for the same alarm (must fail)
  ERROR Error executing tool create_work_order: conflict: alarm ALM-0001 already has an active work order: WO-2026-0010

> transition straight to completed (must fail)
  ERROR Error executing tool transition_work_order: invalid: cannot move a work order from 'open' to 'completed'; allowed: assigned, cancelled
```

## Design decisions

* **Errors are written for a model.** A domain error becomes a tool error `code: reason` that says what
  was wrong and what is allowed ("allowed: assigned, cancelled"). Unexpected exceptions stay generic: the
  SDK hides their text, and a test checks that a crash message containing a path does not reach the client.
* **Typed outputs.** Tools return pydantic models, so each has an output schema and clients get
  `structuredContent`. A test fails if any tool or parameter lacks a description.
* **Annotations.** Reads are marked `readOnlyHint`; `acknowledge_alarm` and `assign_work_order` are
  idempotent; nothing is destructive (records are never deleted).
* **`--read-only` removes the write tools** instead of rejecting calls, so the model never sees them.
* **Duplicates are prevented where retries happen.** A model may call `create_work_order` twice; the
  second call for the same alarm is a conflict that names the existing work order.
* **Writes are all-or-nothing** (`BEGIN IMMEDIATE`). A test makes the event insert fail and checks that the
  status update is rolled back too.
* **Injection and wildcards.** All SQL is parameterised; `%` and `_` in search text are escaped.
* **Bounded output.** Every list has `limit` (1-100) and `next_offset`.
* **Injectable clock** (`--fixed-now`) so "overdue" and ids are deterministic in tests and demos.

## Tests

74 test cases (`uv run pytest`): state machine; every read filter and ordering; every write rule and
permission; rollback; tool inventory, annotations, schemas, documentation of every parameter; error
mapping; read-only mode; resource and prompt; and a full triage flow plus persistence across two server
processes over real **stdio**.

Mutation check: 20 deliberate bugs (viewer allowed to acknowledge, duplicate guard removed, ownership
check dropped, no rollback, unescaped LIKE, crash text leaking, write tools registered in read-only mode,
terminal state with an exit, ...) were each caught by at least one test. One mutant first survived because
it was not actually leaking; it was rewritten and then caught.

## Limitations

* **No authentication.** `actor` is a *claimed* identity checked against a user table. That is fine for a
  demo and for stdio on your own machine; a real deployment must take the identity from the transport
  (for example OAuth on streamable HTTP) and never from a tool argument. The `streamable-http` option
  exists but is unauthenticated.
* One process, one SQLite connection with a lock. No multi-writer support, no migrations.
* Offset paging: rows added between pages can shift the window.
* Not tested against a real assistant (Claude Desktop, Claude Code, ...); it is tested with the MCP SDK's
  own client. `SKILL.md` is guidance I wrote and have not measured.
* Written for `mcp` 2.x (`MCPServer`, `from mcp.client.client import Client`); version 1.x used
  `FastMCP` and different client entry points, hence the `mcp>=2.2,<3` pin.

## Development

```bash
uv sync
uv run pytest
uv run ruff check
```

## License

MIT, see `LICENSE`.
