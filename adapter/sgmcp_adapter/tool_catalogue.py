"""Model-facing tool definitions, generated from ConnectionConfig.

Nobody writes a tool description per employee: the table/column allowlist
an administrator fills in produces these MCP tool schemas directly. The
JSON Schema fragments per column (enum/pattern/min/max) are informational
hints for the model - core.Adapter re-validates everything at call time
regardless of what the model sent.

All four generic tools are registered UNCONDITIONALLY, even before any
table has been configured - this was not always true (an earlier version
hid every tool until at least one table existed), but that meant the
agent runtime's own one-time tool discovery at deploy time - before any
admin has had a chance to configure anything - would permanently miss
them. Every other WAI employee's tool catalogue is fixed at build time;
this is what makes ours behave the same way from the runtime's point of
view, even though the actual allowlist is filled in later. Calling a
tool before it is configured, or against a table outside the allowlist,
is still rejected - by core.Adapter at execution time, not by hiding the
tool from the catalogue.
"""

from __future__ import annotations

from .config import ConnectionConfig


def _writable_tables(config: ConnectionConfig):
    return sorted(name for name, table in config.tables.items() if table.writable)


def _table_hint(config: ConnectionConfig, *, writable_only: bool) -> str:
    parts = []
    for name in sorted(config.tables):
        table = config.tables[name]
        columns = sorted(table.writable if writable_only else table.columns)
        if writable_only and not columns:
            continue
        parts.append("%s (%s)" % (name, ", ".join(columns)))
    return "; ".join(parts) or "(todavia sin tablas configuradas)"


def _table_property(names):
    """An enum when there is something to constrain to; a free-form string
    (rejected later by core.Adapter) when the allowlist is still empty -
    an empty JSON Schema enum is degenerate and some clients reject it
    outright, which would be worse than just deferring to the runtime
    check that already exists."""
    return {"type": "string", "enum": names} if names else {"type": "string"}


def tool_catalogue(config: ConnectionConfig) -> list:
    table_names = sorted(config.tables)
    writable_tables = _writable_tables(config)
    tools = [{
        "name": "leer_registros",
        "description": (
            "Consulta registros de una tabla permitida; solo devuelve las columnas declaradas. "
            "Columnas filtrables por tabla: " + _table_hint(config, writable_only=False) + "."
        ),
        "inputSchema": {
            "type": "object", "additionalProperties": False,
            "required": ["run_token", "tabla"],
            "properties": {
                "run_token": {"type": "string", "description": "Identificador del turno de chat actual, nunca un token de Supabase."},
                "tabla": _table_property(table_names),
                "filtros": {"type": "object", "description": "Pares columna-valor; solo columnas filtrables de la tabla."},
                "limite": {"type": "integer", "minimum": 1, "maximum": config.read_limit},
                "pagina": {"type": "integer", "minimum": 1, "maximum": 10000},
                "orden_columna": {"type": "string"},
                "orden_asc": {"type": "boolean"},
            },
        },
        "annotations": {"readOnlyHint": True, "destructiveHint": False},
    }]

    if config.allows("insertar_registros"):
        tools.append({
            "name": "insertar_registros",
            "description": (
                "Inserta un registro en una tabla permitida, solo con columnas escribibles. "
                "Tablas y columnas escribibles: " + _table_hint(config, writable_only=True) + "."
            ),
            "inputSchema": {
                "type": "object", "additionalProperties": False,
                "required": ["run_token", "tabla", "datos"],
                "properties": {
                    "run_token": {"type": "string"},
                    "tabla": _table_property(writable_tables),
                    "datos": {"type": "object"},
                },
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": False},
        })

    if config.allows("actualizar_registros"):
        tools.append({
            "name": "actualizar_registros",
            "description": (
                "Actualiza registros de una tabla permitida, filtrando por una columna, solo con columnas escribibles. "
                "Tablas y columnas escribibles: " + _table_hint(config, writable_only=True) + "."
            ),
            "inputSchema": {
                "type": "object", "additionalProperties": False,
                "required": ["run_token", "tabla", "columna_filtro", "valor_filtro", "datos"],
                "properties": {
                    "run_token": {"type": "string"},
                    "tabla": _table_property(writable_tables),
                    "columna_filtro": {"type": "string"},
                    "valor_filtro": {"type": "string"},
                    "datos": {"type": "object"},
                },
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        })

    if config.allows("eliminar_registros"):
        tools.append({
            "name": "eliminar_registros",
            "description": "Elimina registros de una tabla permitida, filtrando por una columna.",
            "inputSchema": {
                "type": "object", "additionalProperties": False,
                "required": ["run_token", "tabla", "columna_filtro", "valor_filtro"],
                "properties": {
                    "run_token": {"type": "string"},
                    "tabla": _table_property(table_names),
                    "columna_filtro": {"type": "string"},
                    "valor_filtro": {"type": "string"},
                },
            },
            "annotations": {"readOnlyHint": False, "destructiveHint": True},
        })

    return tools
