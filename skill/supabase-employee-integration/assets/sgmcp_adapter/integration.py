"""Explicit host wiring; does not alter or monkey-patch the host app's
base files. Call ``mount()`` once, from the employee's approved
application composition point - never from the model, never from the
agent process itself.

Configuration (project URL, auth_mode, keys, table/column allowlist) is
not read from the environment: it lives encrypted in the host app's own
settings store (the same ``AppSetting`` + ``encrypt_blob`` primitives WAI
already uses elsewhere), written through the ``PUT /api/supabase/
configuration`` endpoint. That endpoint is step 4 of the README's
four-step flow - an administrator fills project_url/anon_key/
service_role_key/tables/denied there, and this module takes it from there.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from .config import ConfigError, ConnectionConfig
from .core import SUPABASE_AUTH_PATH, Adapter, ConfigurationError, Settings
from .mcp_client import MCPClient
from .routes import connection_router
from .tools import register_tools
from .wai_identity import active_chat_email


_SETTINGS_KEY = "sgmcp_adapter_connection"


def mount(
    app,
    authenticated_user_dependency,
    *,
    service_token: "str | None" = None,
    mcp_url: str = "https://mcp-supabase-generico.onrender.com",
):
    """Mount the generic Supabase adapter on one employee's FastAPI app.

    ``service_token``: if the shared generic MCP requires a service
    header (recommended - see the repository's top-level README), pass
    the same secret configured there. Left as None while the shared MCP
    has no such check yet.
    """
    from app.auth import require_admin

    mcp_client = MCPClient(
        lambda: httpx.AsyncClient(timeout=30, trust_env=False),
        url=mcp_url,
        service_header=("X-WAI-MCP-Key", service_token) if service_token else None,
    )
    adapter = Adapter(
        ConnectionConfig(auth_mode="rls_personal", tables={}),
        None,
        lambda: httpx.AsyncClient(timeout=30, trust_env=False),
        mcp_client,
        active_chat_email,
        configuration_error=ConfigurationError(
            "supabase_not_configured",
            "Este empleado aun no tiene configurada su conexion a Supabase. Un administrador debe completarla.",
        ),
    )
    configuration_lock = asyncio.Lock()
    loaded = False

    async def validate_pair(settings: Settings, auth_mode: str) -> None:
        try:
            async with adapter.auth_client_factory() as client:
                if auth_mode == "rls_personal":
                    request = httpx.Request("GET", settings.supabase_url + SUPABASE_AUTH_PATH + "settings", headers={"apikey": settings.anon_key})
                else:
                    # Not the bare REST root (its OpenAPI exposure can be
                    # disabled by project settings, independent of whether the
                    # key is valid) - the Auth Admin users list only succeeds
                    # for a real service_role key, which is the actual thing
                    # being validated here. Some Supabase gateways also only
                    # honor privileged routes when both headers carry the key.
                    request = httpx.Request(
                        "GET", settings.supabase_url + SUPABASE_AUTH_PATH + "admin/users?page=1&per_page=1",
                        headers={
                            "apikey": settings.service_role_key,
                            "Authorization": "Bearer " + settings.service_role_key,
                        },
                    )
                response = await client.send(request, stream=True, follow_redirects=False)
                try:
                    if 300 <= response.status_code < 400:
                        raise ConfigurationError("supabase_configuration_invalid", "La validacion de Supabase no admite redirecciones.")
                    if response.status_code in (401, 403):
                        raise ConfigurationError("supabase_api_key_rejected", "Supabase rechazo la clave configurada para este modo.")
                    if not response.is_success:
                        raise ValueError("validation unavailable")
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 65536:
                            raise ValueError("response limit")
                        content.extend(chunk)
                    json.loads(content)
                finally:
                    await response.aclose()
        except ConfigurationError:
            raise
        except (httpx.HTTPError, ValueError, TypeError, RecursionError):
            raise ConfigurationError(
                "supabase_configuration_validation_unavailable",
                "La validacion de la conexion Supabase no esta disponible. Intenta mas tarde.",
            ) from None

    async def persist(payload: dict, actor_email: "str | None" = None) -> None:
        from app.app_database import app_write
        from app.app_models.runtime import AppSetting
        from app.channels.encryption import encrypt_blob

        try:
            encrypted = encrypt_blob(payload)
            async with app_write() as session:
                row = await session.get(AppSetting, _SETTINGS_KEY)
                if row is None:
                    session.add(AppSetting(key=_SETTINGS_KEY, value=encrypted, updated_by=actor_email or "system"))
                else:
                    row.value = encrypted
                    if actor_email is not None:
                        row.updated_by = actor_email
                # app_write() does not commit on its own (see tools.py's
                # _WritePhases, which commits explicitly for the same
                # reason) - without this, the in-memory adapter looks
                # configured for the rest of this process's life, but the
                # row was never actually written, and the next restart
                # reverts to unconfigured with no error anywhere.
                await session.commit()
        except Exception:
            raise ConfigurationError(
                "supabase_configuration_storage_unavailable",
                "No se pudo guardar la configuracion protegida; revisa la clave de cifrado del empleado.",
            ) from None

    def apply(config: ConnectionConfig, settings: Settings) -> bool:
        changed = adapter.configure(config, settings)
        register_tools(adapter, configuration_loader=load_configuration, diagnostic_authorizer=may_diagnose)
        return changed

    async def load_configuration() -> None:
        nonlocal loaded
        from app.app_database import app_session
        from app.app_models.runtime import AppSetting
        from app.channels.encryption import decrypt_blob, encryption_available

        async with configuration_lock:
            if loaded:
                return
            if not encryption_available():
                adapter._configuration_error = ConfigurationError(
                    "supabase_configuration_storage_unavailable", "Falta habilitar el cifrado de configuracion del empleado.",
                )
                loaded = True
                return
            try:
                async with app_session() as session:
                    row = await session.get(AppSetting, _SETTINGS_KEY)
                    encrypted = row.value if row else None
                if encrypted is None:
                    loaded = True
                    return
                payload = decrypt_blob(encrypted)
                if not isinstance(payload, dict):
                    raise ValueError("invalid configuration")
                config = ConnectionConfig.from_dict({
                    "auth_mode": payload.get("auth_mode"), "tables": payload.get("tables"),
                    "denied": payload.get("denied", ()), "read_limit": payload.get("read_limit", 10000),
                })
                settings = Settings(payload.get("project_url"), payload.get("anon_key"), payload.get("service_role_key"))
                settings.for_mode(config.auth_mode)
                apply(config, settings)
                loaded = True
            except ConfigurationError as failure:
                adapter._configuration_error = failure
                loaded = True
            except (ConfigError, ValueError, TypeError, KeyError):
                adapter._configuration_error = ConfigurationError(
                    "supabase_configuration_unreadable", "No se pudo leer la configuracion protegida de Supabase.",
                )
                loaded = True
            except Exception:
                adapter._configuration_error = ConfigurationError(
                    "supabase_configuration_unreadable", "No se pudo leer la configuracion protegida de Supabase.",
                )
                loaded = True

    async def save_configuration(body, actor_email: str) -> bool:
        from app.channels.encryption import encryption_available

        async with configuration_lock:
            if not encryption_available():
                raise ConfigurationError("supabase_configuration_storage_unavailable", "Falta habilitar el cifrado de configuracion del empleado.")
            try:
                config = ConnectionConfig.from_dict({
                    "auth_mode": body.auth_mode, "tables": body.tables,
                    "denied": body.denied, "read_limit": body.read_limit,
                })
            except ConfigError as error:
                raise ConfigurationError("supabase_configuration_invalid", str(error)) from None
            if not config.tables:
                raise ConfigurationError("supabase_tables_required", "Declara al menos una tabla permitida antes de activar la conexion.")
            current = adapter._settings
            settings = Settings(
                body.project_url,
                body.anon_key.get_secret_value() if body.anon_key is not None else (current.anon_key if current else None),
                body.service_role_key.get_secret_value() if body.service_role_key is not None else (current.service_role_key if current else None),
            )
            settings.for_mode(config.auth_mode)
            await validate_pair(settings, config.auth_mode)
            payload = {
                "project_url": settings.supabase_url, "auth_mode": config.auth_mode,
                "anon_key": settings.anon_key, "service_role_key": settings.service_role_key,
                "tables": config.to_dict()["tables"], "denied": sorted(config.denied), "read_limit": config.read_limit,
            }
            await persist(payload, actor_email)
            return apply(config, settings)

    async def may_configure(email: str) -> bool:
        from app.channels.encryption import encryption_available
        from app.users_directory import is_admin

        return encryption_available() and await is_admin(email)

    async def may_diagnose(email: str) -> bool:
        from app.users_directory import is_admin

        return await is_admin(email)

    app.include_router(connection_router(
        adapter, authenticated_user_dependency, admin_dependency=require_admin,
        configuration_loader=load_configuration, configuration_saver=save_configuration,
        can_configure=may_configure, diagnostic_authorizer=may_diagnose,
    ))
    register_tools(adapter, configuration_loader=load_configuration, diagnostic_authorizer=may_diagnose)
    app.state.supabase_adapter = adapter
    return adapter
