"""Contrato de contexto de las cinco herramientas, sin cambiar permisos."""

import pytest

from sgmcp_adapter.config import ConnectionConfig
from sgmcp_adapter.tool_catalogue import tool_catalogue


REQUIRED = {
    "leer_registros": ["tabla"],
    "insertar_registros": ["tabla", "datos"],
    "upsertar_registros": ["tabla", "datos"],
    "actualizar_registros": ["tabla", "columna_filtro", "valor_filtro", "datos"],
    "eliminar_registros": ["tabla", "columna_filtro", "valor_filtro"],
}


@pytest.mark.parametrize("mode", ["rls_personal", "service_role"])
@pytest.mark.parametrize("name,required", list(REQUIRED.items()))
def test_context_schema_keeps_business_fields_and_accepts_either_identifier(mode, name, required):
    tools = {tool["name"]: tool for tool in tool_catalogue(ConnectionConfig(auth_mode=mode, tables={}))}
    assert set(tools) == set(REQUIRED)
    schema = tools[name]["inputSchema"]
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert schema["required"] == required
    assert schema["anyOf"] == [
        {"required": ["run_token"]},
        {"required": ["run_id"]},
    ]
    props = schema["properties"]
    assert props["run_token"]["type"] == "string"
    assert "[INTERNO]" in props["run_token"]["description"]
    assert props["run_id"]["type"] == "integer"
    assert props["run_id"]["minimum"] == 1
    assert "WhatsApp" in props["run_id"]["description"]
    assert tools[name]["annotations"]["readOnlyHint"] is (name == "leer_registros")


@pytest.mark.parametrize("mode", ["rls_personal", "service_role"])
def test_context_schema_does_not_reenable_denied_writes(mode):
    config = ConnectionConfig(
        auth_mode=mode, tables={},
        denied=frozenset({"eliminar_registros", "upsertar_registros"}),
    )
    assert {tool["name"] for tool in tool_catalogue(config)} == {
        "leer_registros", "insertar_registros", "actualizar_registros",
    }
