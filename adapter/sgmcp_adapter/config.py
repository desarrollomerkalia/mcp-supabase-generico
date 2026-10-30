"""Declarative, per-employee configuration.

An administrator fills this in when building a new Supabase-connected
employee: which tables are visible, which columns each one exposes, which
columns can be written, and which generic tools are denied. Nobody writes
Python per employee for this - it is pure configuration, stored as JSON.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Tuple

AUTH_MODES = ("rls_personal", "service_role")

# Tools an administrator can deny per employee. Reading is controlled by
# which tables/columns are declared, not by a separate deny flag.
DENIABLE_TOOLS = frozenset({"insertar_registros", "actualizar_registros", "eliminar_registros"})

_TYPES = ("string", "integer", "number", "boolean")


class ConfigError(ValueError):
    """Raised when an administrator-supplied configuration is invalid."""


@dataclass(frozen=True)
class ColumnRule:
    """Validation for one column. Mirrors a JSON Schema subset on purpose,
    so an agent-building assistant can produce it directly from a business
    description without inventing a new format."""

    type: str = "string"
    enum: Optional[Tuple[str, ...]] = None
    pattern: Optional[str] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    required: bool = False

    def __post_init__(self):
        if self.type not in _TYPES:
            raise ConfigError("Tipo de columna no soportado: %r" % (self.type,))
        if self.enum is not None:
            object.__setattr__(self, "enum", tuple(self.enum))
            if not self.enum or not all(isinstance(v, str) for v in self.enum):
                raise ConfigError("'enum' debe ser una lista de strings no vacia.")
        if self.pattern is not None:
            try:
                re.compile(self.pattern)
            except re.error as error:
                raise ConfigError("Patron de columna invalido: %s" % (error,)) from None
        for name in ("min_length", "max_length"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value < 0):
                raise ConfigError("'%s' debe ser un entero no negativo." % (name,))

    def validate(self, name: str, value: object) -> None:
        """Raise ConfigError if value does not satisfy this rule."""
        if value is None:
            if self.required:
                raise ConfigError("Falta el campo requerido '%s'." % (name,))
            return
        python_type = {
            "string": str, "integer": int, "number": (int, float), "boolean": bool,
        }[self.type]
        if isinstance(value, bool) and self.type != "boolean":
            raise ConfigError("Campo '%s' debe ser de tipo %s." % (name, self.type))
        if not isinstance(value, python_type):
            raise ConfigError("Campo '%s' debe ser de tipo %s." % (name, self.type))
        if isinstance(value, str):
            if self.min_length is not None and len(value) < self.min_length:
                raise ConfigError("Campo '%s' es mas corto de lo permitido." % (name,))
            if self.max_length is not None and len(value) > self.max_length:
                raise ConfigError("Campo '%s' es mas largo de lo permitido." % (name,))
            if self.pattern is not None and re.fullmatch(self.pattern, value) is None:
                raise ConfigError("Campo '%s' no cumple el formato esperado." % (name,))
        if self.enum is not None and value not in self.enum:
            raise ConfigError("Campo '%s' debe ser uno de: %s." % (name, ", ".join(self.enum)))

    @classmethod
    def from_dict(cls, payload: dict) -> "ColumnRule":
        if not isinstance(payload, dict):
            raise ConfigError("La regla de una columna debe ser un objeto.")
        allowed = {"type", "enum", "pattern", "min_length", "max_length", "required"}
        if set(payload) - allowed:
            raise ConfigError("Campos no reconocidos en la regla de columna: %s" % (sorted(set(payload) - allowed),))
        return cls(**payload)

    def to_dict(self) -> dict:
        return {
            "type": self.type,
            "enum": list(self.enum) if self.enum is not None else None,
            "pattern": self.pattern,
            "min_length": self.min_length,
            "max_length": self.max_length,
            "required": self.required,
        }


@dataclass(frozen=True)
class TableConfig:
    """Which columns of one table are readable, writable and filterable."""

    columns: "dict" = field(default_factory=dict)
    writable: frozenset = frozenset()
    filterable: frozenset = frozenset()

    def __post_init__(self):
        if not self.columns:
            raise ConfigError("Una tabla necesita al menos una columna declarada.")
        for name in self.columns:
            if not isinstance(name, str) or not name:
                raise ConfigError("Nombre de columna invalido.")
        if not isinstance(self.writable, frozenset):
            object.__setattr__(self, "writable", frozenset(self.writable))
        if not isinstance(self.filterable, frozenset):
            object.__setattr__(self, "filterable", frozenset(self.filterable))
        if self.writable - set(self.columns):
            raise ConfigError("Hay columnas escribibles no declaradas en 'columns'.")
        if self.filterable - set(self.columns):
            raise ConfigError("Hay columnas filtrables no declaradas en 'columns'.")

    @classmethod
    def from_dict(cls, payload: dict) -> "TableConfig":
        if not isinstance(payload, dict):
            raise ConfigError("La configuracion de una tabla debe ser un objeto.")
        columns_payload = payload.get("columns")
        if not isinstance(columns_payload, dict):
            raise ConfigError("Cada tabla necesita un objeto 'columns' con al menos una columna.")
        columns = {name: ColumnRule.from_dict(rule) for name, rule in columns_payload.items()}
        return cls(
            columns=columns,
            writable=frozenset(payload.get("writable", ())),
            filterable=frozenset(payload.get("filterable", ())),
        )

    def to_dict(self) -> dict:
        return {
            "columns": {name: rule.to_dict() for name, rule in self.columns.items()},
            "writable": sorted(self.writable),
            "filterable": sorted(self.filterable),
        }


@dataclass(frozen=True)
class ConnectionConfig:
    """Everything an administrator configures for one Supabase-connected employee."""

    auth_mode: str = "rls_personal"
    tables: "dict" = field(default_factory=dict)
    denied: frozenset = frozenset()
    read_limit: int = 10000

    def __post_init__(self):
        if self.auth_mode not in AUTH_MODES:
            raise ConfigError("auth_mode debe ser uno de: %s." % (", ".join(AUTH_MODES),))
        # An empty 'tables' is allowed here on purpose: it is the bootstrap
        # placeholder before an administrator configures the employee for
        # the first time. integration.save_configuration() is what should
        # reject an empty allowlist for a config meant to go live.
        for name, table in self.tables.items():
            if not isinstance(name, str) or not name:
                raise ConfigError("Nombre de tabla invalido.")
            if not isinstance(table, TableConfig):
                raise ConfigError("Cada tabla debe ser un TableConfig.")
        if not isinstance(self.denied, frozenset):
            object.__setattr__(self, "denied", frozenset(self.denied))
        if self.denied - DENIABLE_TOOLS:
            raise ConfigError("'denied' solo admite: %s." % (", ".join(sorted(DENIABLE_TOOLS)),))
        if type(self.read_limit) is not int or not 1 <= self.read_limit <= 10000:
            raise ConfigError("read_limit debe ser un entero entre 1 y 10000.")

    def allows(self, tool: str) -> bool:
        return tool not in self.denied

    @classmethod
    def from_dict(cls, payload: dict) -> "ConnectionConfig":
        if not isinstance(payload, dict):
            raise ConfigError("La configuracion del conector debe ser un objeto.")
        tables_payload = payload.get("tables")
        if not isinstance(tables_payload, dict):
            raise ConfigError("Declara 'tables' como un objeto {nombre_tabla: configuracion}.")
        tables = {name: TableConfig.from_dict(value) for name, value in tables_payload.items()}
        return cls(
            auth_mode=payload.get("auth_mode", "rls_personal"),
            tables=tables,
            denied=frozenset(payload.get("denied", ())),
            read_limit=payload.get("read_limit", 10000),
        )

    def to_dict(self) -> dict:
        return {
            "auth_mode": self.auth_mode,
            "tables": {name: table.to_dict() for name, table in self.tables.items()},
            "denied": sorted(self.denied),
            "read_limit": self.read_limit,
        }
