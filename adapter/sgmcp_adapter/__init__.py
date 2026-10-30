"""Generic Supabase session/write adapter for WAI employees.

Business logic (which tables, which columns, which auth mode) is supplied
as configuration by whoever builds the employee. Nothing in this package
is specific to one business.
"""

from .config import ColumnRule, ConfigError, ConnectionConfig, TableConfig
from .core import Adapter, AdapterError, ConfigurationError, LoginRequired, Settings

__all__ = [
    "Adapter",
    "AdapterError",
    "ColumnRule",
    "ConfigError",
    "ConfigurationError",
    "ConnectionConfig",
    "LoginRequired",
    "Settings",
    "TableConfig",
]
