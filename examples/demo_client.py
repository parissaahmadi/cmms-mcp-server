"""Drive the server over stdio the way an AI client would: triage the worst alarm end to end.

    uv run python examples/demo_client.py
"""

import json
import os
import sys

import anyio
from mcp.client.client import Client
from mcp.client.stdio import StdioServerParameters

PARAMS = StdioServerParameters(
    command=sys.executable,
    args=["-m", "cmms_mcp", "--fixed-now", "2026-10-09T06:00:00Z"],
    env={**os.environ},
)


def show(title: str, result) -> None:
    body = result.structured_content if result.structured_content is not None else result.content[0].text
    text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    flag = "ERROR " if result.is_error else ""
    print(f"\n> {title}\n  {flag}{text[:230]}{'...' if len(text) > 230 else ''}")


async def main() -> None:
    async with Client(PARAMS) as c:
        tools = [t.name for t in (await c.list_tools()).tools]
        print("tools:", ", ".join(tools))
        show("get_plant_overview", await c.call_tool("get_plant_overview", {}))
        crit = await c.call_tool("list_alarms", {"state": "active", "severity": "critical", "limit": 3})
        show("list_alarms(active, critical)", crit)
        alarm = crit.structured_content["items"][0]
        show(f"get_asset({alarm['asset_id']})", await c.call_tool("get_asset", {"asset_id": alarm["asset_id"]}))
        show("acknowledge_alarm as a viewer (must fail)", await c.call_tool(
            "acknowledge_alarm", {"alarm_id": alarm["id"], "actor": "view.d"}))
        show("acknowledge_alarm as tech.a", await c.call_tool(
            "acknowledge_alarm", {"alarm_id": alarm["id"], "actor": "tech.a"}))
        wo = await c.call_tool("create_work_order", {
            "actor": "tech.a", "asset_id": alarm["asset_id"], "priority": "urgent",
            "title": "Oil temperature high: inspect cooler", "source_alarm_id": alarm["id"],
            "due_date": "2026-10-10"})  # fmt: skip
        show("create_work_order", wo)
        wid = wo.structured_content["work_order"]["id"]
        show("create_work_order again for the same alarm (must fail)", await c.call_tool(
            "create_work_order", {"actor": "tech.a", "asset_id": alarm["asset_id"],
                                  "title": "Oil temperature high (duplicate)",
                                  "source_alarm_id": alarm["id"]}))  # fmt: skip
        show("transition straight to completed (must fail)", await c.call_tool(
            "transition_work_order", {"work_order_id": wid, "to_status": "completed",
                                      "actor": "sup.c", "note": "skipping ahead"}))  # fmt: skip


anyio.run(main)
