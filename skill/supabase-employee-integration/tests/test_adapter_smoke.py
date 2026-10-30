"""Runnable without RDWAI/WAI installed: everything here exercises only
sgmcp_adapter's own code (config validation, tool catalogue generation,
and Adapter.execute in service_role mode, which needs no Supabase Auth).

This is NOT a replacement for testing against the real deployed MCP, nor
for the rls_personal login/session flow (which needs a mocked Supabase
Auth transport - see the original Mara adapter's test suite for that
pattern, ported across the httpx client factory boundary the same way).
Run with: pip install -e ".[test]" && pytest
"""

import base64
import json

import pytest

from sgmcp_adapter.config import ConfigError, ConnectionConfig, TableConfig
from sgmcp_adapter.core import Adapter, AdapterError, ConfigurationError, LoginRequired, Settings
from sgmcp_adapter.tool_catalogue import tool_catalogue


REF = "abcdefghij1234567890"
URL = "https://%s.supabase.co" % REF


def _fake_key(role: str) -> str:
    def b64(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
    return b64({"alg": "HS256", "typ": "JWT"}) + "." + b64({"role": role, "ref": REF}) + ".sig"


ANON_KEY = _fake_key("anon")
SERVICE_ROLE_KEY = _fake_key("service_role")


@pytest.fixture
def orders_config():
    table = TableConfig.from_dict({
        "columns": {
            "id": {"type": "string"},
            "status": {"type": "string", "enum": ["active", "complete"]},
            "amount": {"type": "number"},
        },
        "writable": ["status", "amount"],
        "filterable": ["id", "status"],
    })
    return ConnectionConfig(auth_mode="service_role", tables={"orders": table})


class _FakeTransport:
    def __init__(self):
        self.calls = []

    async def call(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        if tool == "supabase_leer":
            body = {"data": [{"id": "1", "status": "active"}], "total": 1}
        else:
            body = [{"id": "2", "status": "active", "amount": 10}]
        return {"content": [{"type": "text", "text": json.dumps(body)}]}


class _RealisticSupabaseTransport:
    """Mirrors the actual deployed sgmcp's supabase_leer response shape
    (rows under "datos", total only under "total_registros" - never under
    "total"/"total_count"/etc) closely enough to exercise the
    _validated_page_total fallback path. _FakeTransport above always
    short-circuits that path by putting "total" at the top level, which is
    exactly why it never caught the bug this regression test is for."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def call(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        if tool != "supabase_leer":
            raise AssertionError("unexpected tool for this transport: %r" % tool)
        filtros = arguments.get("filtros") or {}
        rows = [row for row in self.rows if all(row.get(k) == v for k, v in filtros.items())]
        order_column = arguments.get("orden_columna") or "id"
        rows = sorted(rows, key=lambda row: row.get(order_column), reverse=not arguments.get("orden_asc", True))
        total = len(rows)
        offset = (arguments["pagina"] - 1) * arguments["limite"]
        page_rows = rows[offset:offset + arguments["limite"]]
        keep = [c.strip() for c in arguments.get("columnas", "").split(",") if c.strip()]
        if keep:
            page_rows = [{k: v for k, v in row.items() if k in keep} for row in page_rows]
        body = {"datos": page_rows, "total_registros": total}
        return {"content": [{"type": "text", "text": json.dumps(body)}]}


def _adapter(config, transport, *, active_token="good-token"):
    settings = Settings(URL, anon_key=ANON_KEY, service_role_key=SERVICE_ROLE_KEY)

    async def resolve_actor(run_token):
        return "chat-active" if run_token == active_token else None

    return Adapter(config, settings, lambda: None, transport, resolve_actor)


def test_table_config_rejects_unknown_writable_column():
    with pytest.raises(ConfigError):
        TableConfig.from_dict({"columns": {"x": {"type": "string"}}, "writable": ["y"]})


def test_settings_rejects_service_role_key_in_anon_slot():
    with pytest.raises(ConfigurationError):
        Settings(URL, anon_key=SERVICE_ROLE_KEY)


def test_settings_rejects_anon_key_in_service_role_slot():
    with pytest.raises(ConfigurationError):
        Settings(URL, service_role_key=ANON_KEY)


def test_tool_catalogue_reflects_config(orders_config):
    names = {tool["name"] for tool in tool_catalogue(orders_config)}
    assert names == {"leer_registros", "insertar_registros", "actualizar_registros", "eliminar_registros"}


def test_tool_catalogue_is_registered_before_any_table_is_configured():
    # All four tools must exist in the catalogue from the very first deploy,
    # before any admin has configured a table - the agent runtime discovers
    # this catalogue once, at startup, and never revisits it. core.Adapter
    # is what rejects an unconfigured/unknown table, not an absent tool.
    empty_config = ConnectionConfig(auth_mode="rls_personal", tables={})
    names = {tool["name"] for tool in tool_catalogue(empty_config)}
    assert names == {"leer_registros", "insertar_registros", "actualizar_registros", "eliminar_registros"}
    leer = next(tool for tool in tool_catalogue(empty_config) if tool["name"] == "leer_registros")
    assert "enum" not in leer["inputSchema"]["properties"]["tabla"]


def test_tool_catalogue_omits_denied_tools(orders_config):
    denied_config = ConnectionConfig(auth_mode="service_role", tables=orders_config.tables, denied=frozenset({"eliminar_registros"}))
    names = {tool["name"] for tool in tool_catalogue(denied_config)}
    assert "eliminar_registros" not in names


@pytest.mark.asyncio
async def test_read_in_service_role_mode(orders_config):
    transport = _FakeTransport()
    adapter = _adapter(orders_config, transport)
    result = await adapter.execute("good-token", "leer_registros", {"tabla": "orders", "filtros": {"status": "active"}})
    assert result["registros"] == [{"id": "1", "status": "active"}]
    assert result["total_visible"] == 1
    assert transport.calls[0][1]["supabaseKey"] == SERVICE_ROLE_KEY
    assert "userToken" not in transport.calls[0][1]


@pytest.mark.asyncio
async def test_write_requires_preflight_and_validates_columns(orders_config):
    transport = _FakeTransport()
    adapter = _adapter(orders_config, transport)

    with pytest.raises(LoginRequired):
        await adapter.execute("good-token", "insertar_registros", {"tabla": "orders", "datos": {"status": "active", "amount": 10}})

    context = await adapter.preflight_write("good-token", "insertar_registros", {"tabla": "orders", "datos": {"status": "active", "amount": 10}})
    await adapter.validate_write_context("good-token", context)
    result = await adapter.execute(
        "good-token", "insertar_registros", {"tabla": "orders", "datos": {"status": "active", "amount": 10}}, write_context=context,
    )
    assert result["resultado"] == [{"id": "2", "status": "active", "amount": 10}]

    with pytest.raises(AdapterError):
        await adapter.execute(
            "good-token", "insertar_registros", {"tabla": "orders", "datos": {"status": "nope", "amount": 10}}, write_context=context,
        )


@pytest.mark.asyncio
async def test_inactive_chat_run_is_rejected(orders_config):
    adapter = _adapter(orders_config, _FakeTransport())
    with pytest.raises(LoginRequired):
        await adapter.execute("stale-token", "leer_registros", {"tabla": "orders"})


# The exact field set a frontend built against this contract (see
# adapter/frontend/) needs from GET /api/supabase/session, regardless of
# auth_mode or whether a personal session exists. A missing field here is a
# silent frontend-breaking bug, not a type-checker catch - TypeScript only
# validates the frontend's own code, not that the backend actually sends
# what it expects.
_SESSION_SHAPE_FIELDS = {
    "connected", "email", "configured", "configuration_code",
    "state", "reason_code", "expires_at", "last_verified_at", "auth_mode",
}


def test_connection_status_shape_service_role_configured(orders_config):
    adapter = _adapter(orders_config, _FakeTransport())
    status = adapter.connection_status("person@example.com")
    assert _SESSION_SHAPE_FIELDS <= set(status)
    assert status["configured"] is True
    assert status["state"] == "connected"
    assert status["connected"] is True
    assert status["configuration_code"] is None
    assert status["reason_code"] is None


def test_connection_status_shape_service_role_unconfigured():
    config = ConnectionConfig(auth_mode="service_role", tables={})
    adapter = Adapter(config, None, lambda: None, _FakeTransport(), lambda token: None,
                       configuration_error=ConfigurationError("supabase_not_configured", "not configured"))
    status = adapter.connection_status("person@example.com")
    assert _SESSION_SHAPE_FIELDS <= set(status)
    assert status["configured"] is False
    assert status["state"] == "not_configured"
    assert status["connected"] is False
    assert status["configuration_code"] == "supabase_not_configured"


def test_connection_status_shape_rls_personal_disconnected(orders_config):
    rls_config = ConnectionConfig(auth_mode="rls_personal", tables=orders_config.tables)
    settings = Settings(URL, anon_key=ANON_KEY)
    adapter = Adapter(rls_config, settings, lambda: None, _FakeTransport(), lambda token: None)
    status = adapter.connection_status("person@example.com")
    assert _SESSION_SHAPE_FIELDS <= set(status)
    assert status["configured"] is True
    assert status["state"] == "disconnected"
    assert status["connected"] is False
    assert status["expires_at"] is None
    assert status["last_verified_at"] is None


@pytest.mark.asyncio
async def test_service_role_read_against_realistic_total_registros_shape(orders_config):
    # Regression for a real bug: execute()'s service_role branch passed
    # response=None into _validated_page_total instead of the already-
    # fetched response, which only mattered once the primary _read_page
    # call failed to find a top-level total - exactly what happens against
    # a transport (like the real deployed one) that only ever reports the
    # total under total_registros. Previously raised sgmcp_read_response_invalid
    # with response_shape {"kind": "null"} on every single read in this mode.
    transport = _RealisticSupabaseTransport([{"id": "1", "status": "active", "amount": 10}])
    adapter = _adapter(orders_config, transport)
    result = await adapter.execute("good-token", "leer_registros", {"tabla": "orders"})
    assert result["registros"] == [{"id": "1", "status": "active", "amount": 10}]
    assert result["total_visible"] == 1
    assert result["completa"] is True
