from cmms_mcp import db, service
from cmms_mcp.tools import GLOSSARY
from mcp_helpers import server, with_client

READS = {"get_plant_overview", "list_assets", "get_asset", "list_alarms", "list_work_orders",
         "get_work_order"}  # fmt: skip
WRITES = {"acknowledge_alarm", "create_work_order", "assign_work_order", "transition_work_order"}


def tools_by_name(srv):
    async def body(c):
        return {t.name: t for t in (await c.list_tools()).tools}

    return with_client(srv, body)


def has_description(prop: dict) -> bool:
    return "description" in prop or any(has_description(p) for p in prop.get("anyOf", []))


def test_tool_inventory():
    assert set(tools_by_name(server())) == READS | WRITES


def test_read_only_mode_does_not_even_register_write_tools():
    assert set(tools_by_name(server(read_only=True))) == READS

    async def body(c):
        r = await c.call_tool("create_work_order", {"actor": "sup.c", "asset_id": "AST-0001", "title": "Should not exist"})
        return r

    r = with_client(server(read_only=True), body)
    assert r.is_error and "Unknown tool" in r.content[0].text


def test_annotations_tell_clients_which_tools_are_safe():
    tools = tools_by_name(server())
    for name in READS:
        a = tools[name].annotations
        assert a.read_only_hint is True and a.destructive_hint is False
    for name in WRITES:
        assert tools[name].annotations.read_only_hint is False
    assert tools["acknowledge_alarm"].annotations.idempotent_hint is True
    assert tools["create_work_order"].annotations.idempotent_hint is False


def test_every_tool_is_documented_and_has_an_output_schema():
    for name, t in tools_by_name(server()).items():
        assert t.description and len(t.description) > 30, name
        assert t.output_schema and t.output_schema.get("properties"), name
        for pname, prop in t.input_schema.get("properties", {}).items():
            assert has_description(prop), f"{name}.{pname} has no description"


def test_required_arguments_are_what_the_docs_promise():
    t = tools_by_name(server())
    assert set(t["create_work_order"].input_schema["required"]) == {"actor", "asset_id", "title"}
    assert set(t["transition_work_order"].input_schema["required"]) == {"work_order_id", "to_status", "actor"}
    assert t["get_plant_overview"].input_schema.get("required", []) == []


def test_structured_results_round_trip():
    async def body(c):
        r = await c.call_tool("get_asset", {"asset_id": "AST-0001"})
        return r

    r = with_client(server(), body)
    assert not r.is_error
    sc = r.structured_content
    assert sc["asset"]["id"] == "AST-0001"
    assert [a["code"] for a in sc["active_alarms"]] == ["OIL_TEMP_HIGH", "PRESS_DROP"]


def test_domain_errors_reach_the_model_with_code_and_reason():
    async def body(c):
        return [
            await c.call_tool("get_asset", {"asset_id": "AST-9999"}),
            await c.call_tool("get_asset", {"asset_id": "drop table"}),
            await c.call_tool("acknowledge_alarm", {"alarm_id": "ALM-0001", "actor": "view.d"}),
            await c.call_tool("transition_work_order", {"work_order_id": "WO-2026-0003", "to_status": "completed", "actor": "sup.c", "note": "skip ahead"}),
        ]

    notfound, malformed, forbidden, invalid = with_client(server(), body)
    assert all(r.is_error for r in (notfound, malformed, forbidden, invalid))
    assert "not_found: asset AST-9999 does not exist" in notfound.content[0].text
    assert "invalid" in malformed.content[0].text and "AST-0001" in malformed.content[0].text
    assert "forbidden" in forbidden.content[0].text and "viewer" in forbidden.content[0].text
    assert "allowed: assigned, cancelled" in invalid.content[0].text  # tells the model what it may do
    assert "cannot move a work order from 'open' to 'completed'" in invalid.content[0].text


def test_schema_violations_are_rejected_before_the_service_runs():
    async def body(c):
        return [
            await c.call_tool("list_assets", {"limit": 500}),
            await c.call_tool("list_assets", {"status": "exploded"}),
            await c.call_tool("list_work_orders", {"overdue_only": "maybe"}),
            await c.call_tool("create_work_order", {"asset_id": "AST-0001"}),  # missing actor, title
        ]

    for r in with_client(server(), body):
        assert r.is_error


def test_unexpected_crashes_do_not_leak_internals():
    class Broken(service.Cmms):
        def overview(self):
            raise RuntimeError("secret: /srv/cmms/passwords.txt")

    srv = server(svc=Broken(db.connect()))

    async def body(c):
        return await c.call_tool("get_plant_overview", {})

    r = with_client(srv, body)
    assert r.is_error
    assert "secret" not in r.content[0].text and "passwords" not in r.content[0].text


def test_writes_are_visible_to_later_reads_in_the_same_session():
    async def body(c):
        before = (await c.call_tool("list_alarms", {"state": "active"})).structured_content["total"]
        await c.call_tool("acknowledge_alarm", {"alarm_id": "ALM-0001", "actor": "tech.a"})
        after = (await c.call_tool("list_alarms", {"state": "active"})).structured_content["total"]
        created = await c.call_tool("create_work_order", {
            "actor": "tech.a", "asset_id": "AST-0001", "title": "Inspect oil cooler",
            "priority": "high", "source_alarm_id": "ALM-0001"})  # fmt: skip
        dup = await c.call_tool("create_work_order", {
            "actor": "tech.a", "asset_id": "AST-0001", "title": "Inspect oil cooler again",
            "source_alarm_id": "ALM-0001"})  # fmt: skip
        listed = await c.call_tool("list_work_orders", {"asset_id": "AST-0001"})
        return before, after, created, dup, listed

    before, after, created, dup, listed = with_client(server(), body)
    assert (before, after) == (6, 5)
    new_id = created.structured_content["work_order"]["id"]
    assert new_id == "WO-2026-0010"
    assert dup.is_error and new_id in dup.content[0].text
    assert new_id in [w["id"] for w in listed.structured_content["items"]]


def test_resource_and_prompt():
    async def body(c):
        res = await c.list_resources()
        text = (await c.read_resource("cmms://glossary")).contents[0].text
        prompts = (await c.list_prompts()).prompts
        got = await c.get_prompt("shift_handover", {"area": "Utilities"})
        return res, text, prompts, got

    res, text, prompts, got = with_client(server(), body)
    assert [str(r.uri) for r in res.resources] == ["cmms://glossary"]
    assert text == GLOSSARY
    assert [p.name for p in prompts] == ["shift_handover"]
    assert "Utilities" in got.messages[0].content.text and "Do not change any records" in got.messages[0].content.text


def test_server_tells_clients_how_to_use_it():
    async def body(c):
        return c.instructions, c.server_info.name

    instructions, name = with_client(server(), body)
    assert "get_plant_overview" in instructions and "never invent" in instructions
    assert name == "cmms"
