"""End to end over a real subprocess and the stdio transport."""

from mcp_helpers import with_stdio


def test_triage_flow_over_stdio():
    async def body(c):
        out = {}
        out["tools"] = sorted(t.name for t in (await c.list_tools()).tools)
        out["overview"] = (await c.call_tool("get_plant_overview", {})).structured_content
        crit = (await c.call_tool("list_alarms", {"state": "active", "severity": "critical"})).structured_content
        out["crit"] = crit
        alarm = crit["items"][0]
        out["ack"] = (await c.call_tool("acknowledge_alarm", {"alarm_id": alarm["id"], "actor": "tech.a"})).structured_content
        wo = await c.call_tool("create_work_order", {
            "actor": "tech.a", "asset_id": alarm["asset_id"], "title": "Oil temperature high: inspect cooler",
            "priority": "urgent", "source_alarm_id": alarm["id"], "due_date": "2026-10-10"})  # fmt: skip
        out["wo"] = wo.structured_content
        wid = wo.structured_content["work_order"]["id"]
        await c.call_tool("assign_work_order", {"work_order_id": wid, "assignee": "tech.b", "actor": "sup.c"})
        await c.call_tool("transition_work_order", {"work_order_id": wid, "to_status": "in_progress", "actor": "tech.b"})
        done = await c.call_tool("transition_work_order", {"work_order_id": wid, "to_status": "completed", "actor": "tech.b", "note": "Cooler cleaned; oil at 78 C."})
        out["done"] = done.structured_content
        return out

    out = with_stdio(body)
    assert len(out["tools"]) == 10
    assert out["overview"]["as_of"] == "2026-10-09T06:00:00Z"  # --fixed-now reached the server
    assert out["crit"]["items"][0]["code"] == "OIL_TEMP_HIGH"
    assert out["ack"]["alarm"]["state"] == "acknowledged"
    assert out["wo"]["work_order"]["priority"] == "urgent"
    assert out["done"]["work_order"]["status"] == "completed"
    assert [e["action"] for e in out["done"]["events"]] == ["created", "assigned", "transition", "transition"]


def test_read_only_flag_over_stdio():
    async def body(c):
        return sorted(t.name for t in (await c.list_tools()).tools)

    assert len(with_stdio(body, "--read-only")) == 6


def test_database_file_persists_between_server_processes(tmp_path):
    db_file = str(tmp_path / "plant.sqlite3")

    async def write(c):
        r = await c.call_tool("create_work_order", {"actor": "sup.c", "asset_id": "AST-0016", "title": "Check forklift brakes"})
        return r.structured_content["work_order"]["id"]

    async def read(c):
        return (await c.call_tool("get_work_order", {"work_order_id": "WO-2026-0010"})).structured_content

    assert with_stdio(write, "--db", db_file) == "WO-2026-0010"
    again = with_stdio(read, "--db", db_file)
    assert again["work_order"]["title"] == "Check forklift brakes"
    assert again["events"][0]["actor"] == "sup.c"
