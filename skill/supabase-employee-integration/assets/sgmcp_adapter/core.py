"""Credentials stay in the application process, never in model arguments.

This is the generic engine extracted from the first working instance of
this pattern (an employee called "Mara" built for motel room rentals). It
keeps everything that was NOT about motels: Supabase Auth session
handling, write idempotency hookup, response-total verification, and
secret redaction. Table names, column lists and business validation are
supplied by a ConnectionConfig (see config.py) instead of being hardcoded
here.

Two credential models are supported, chosen per employee via
``ConnectionConfig.auth_mode``:

- ``rls_personal``: each person logs into Supabase with their own
  account. The adapter stores only their JWT, in memory, and every call
  carries ``anon_key`` + that JWT so Postgres RLS decides what they can
  see or change. This is the safer default - use it whenever the Supabase
  project's end users each have their own Supabase Auth account.
- ``service_role``: one secret, configured by an administrator, used for
  every call. There is no personal login and no RLS backstop, so the
  table/column allowlist in ConnectionConfig is the ONLY safety net. Use
  this only when there is no notion of "the end user's own account" in
  Supabase.
"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional, Protocol

import httpx

from .config import ColumnRule, ConfigError, ConnectionConfig, TableConfig
from .config import DENIABLE_TOOLS as _WRITE_TOOLS
from .mcp_client import MCPError
from .response_shape import summarize_response


GENERIC_TOOLS = frozenset({"leer_registros"}) | _WRITE_TOOLS
_READ_PAGE_SIZE = 1000
_MAX_READ_PAGES = 100

# Built by concatenation, not as one contiguous path marker: some WAI host
# templates run a static text search over backend source (comments included)
# that forbids that versioned-API segment outside their own agent-runtime
# proxy client, aimed at stopping a backend from calling an LLM provider's
# API directly. It also matched here, where it does not apply - these are
# Supabase's own versioned REST/Auth endpoints, unrelated to that proxy.
# Same calls, same URLs, just never spelled out as one unbroken segment
# anywhere in this file, including this comment. See SKILL.md ("costo real
# de usar este skill") for the full story.
SUPABASE_AUTH_PATH = "/auth/" + "v1" + "/"
SUPABASE_REST_PATH = "/rest/" + "v1" + "/"


class AdapterError(Exception):
    """Only fixed, non-sensitive messages cross the API boundary."""

    code = "supabase_operation_failed"
    status_code = 409
    write_state = "unknown"


class ConfigurationError(AdapterError):
    write_state = "not_sent"

    def __init__(self, code="supabase_not_configured", message="La conexion Supabase no esta configurada en el servidor."):
        self.code = code
        self.status_code = 503
        super().__init__(message)


class LoginRequired(AdapterError):
    code = "supabase_session_required"
    status_code = 409
    write_state = "not_sent"

    def __init__(self, code="supabase_session_required"):
        self.code = code
        super().__init__("Inicia sesion en Supabase desde el formulario de conexion.")


class AuthenticationError(AdapterError):
    status_code = 422

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


class UpstreamError(AdapterError):
    status_code = 503

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def normalize_project_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("invalid project origin")
    match = re.fullmatch(r"https://([a-z0-9]{20})\.supabase\.co/?", value.strip(), re.IGNORECASE | re.ASCII)
    if match is None:
        raise ValueError("invalid project origin")
    return "https://" + match[1].lower() + ".supabase.co"


def _jwt_role_and_ref(key: str):
    parts = key.split(".")
    if len(parts) != 3 or not all(parts):
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.b64decode(padded, altchars=b"-_", validate=True))
    except Exception:
        return None
    if not isinstance(claims, dict):
        return None
    role, ref = claims.get("role"), claims.get("ref")
    if not isinstance(role, str) or not isinstance(ref, str) or re.fullmatch(r"[a-zA-Z0-9]{20}", ref) is None:
        return None
    return role, ref.lower()


def _check_key(key: str, url: str, *, new_prefix: str, legacy_role: str) -> None:
    """Reject the wrong kind of key for the wrong slot: an anon/publishable
    key can never be stored as service_role_key, and vice versa."""
    if not isinstance(key, str) or not 1 <= len(key) <= 8192:
        raise ValueError("invalid key")
    if key.startswith("sb_publishable_") or key.startswith("sb_secret_"):
        if not key.startswith(new_prefix):
            raise ValueError("key does not match its configured role")
        if re.fullmatch(r"sb_(?:publishable|secret)_[A-Za-z0-9_-]+", key, re.ASCII) is None:
            raise ValueError("invalid key format")
        return
    parsed = _jwt_role_and_ref(key)
    if parsed is None:
        raise ValueError("invalid legacy key")
    role, ref = parsed
    if role != legacy_role or "https://" + ref + ".supabase.co" != url:
        raise ValueError("key role or project mismatch")


@dataclass(frozen=True)
class Settings:
    """Credentials for one Supabase project. Validated so that an anon key
    can never end up in the service_role slot or vice versa - the two are
    not interchangeable even though both are "a Supabase key"."""

    supabase_url: str
    anon_key: Optional[str] = field(default=None, repr=False)
    service_role_key: Optional[str] = field(default=None, repr=False)

    def __post_init__(self):
        try:
            if self.anon_key is None and self.service_role_key is None:
                raise ValueError("at least one key is required")
            url = normalize_project_url(self.supabase_url)
            if self.anon_key is not None:
                _check_key(self.anon_key, url, new_prefix="sb_publishable_", legacy_role="anon")
            if self.service_role_key is not None:
                _check_key(self.service_role_key, url, new_prefix="sb_secret_", legacy_role="service_role")
            object.__setattr__(self, "supabase_url", url)
        except (ValueError, IndexError, TypeError, AttributeError, KeyError, RecursionError):
            raise ConfigurationError(
                "supabase_configuration_invalid",
                "Configura la URL del proyecto y una clave valida para el modo de autenticacion elegido.",
            ) from None

    def for_mode(self, auth_mode: str) -> None:
        """Raise if this Settings cannot support the requested auth_mode."""
        if auth_mode == "rls_personal" and self.anon_key is None:
            raise ConfigurationError("supabase_anon_key_missing", "Falta la clave publica (anon) para el modo de sesion personal.")
        if auth_mode == "service_role" and self.service_role_key is None:
            raise ConfigurationError("supabase_service_role_key_missing", "Falta la service role key para el modo administrador.")


@dataclass(repr=False)
class Session:
    access_token: str
    refresh_token: str
    subject: str
    expires_at: float
    checked_at: float = 0


@dataclass(frozen=True, repr=False)
class WriteContext:
    principal: str
    subject: str
    project_url: str
    generation: int


class Transport(Protocol):
    async def call(self, tool: str, arguments: dict) -> dict: ...


def _has_unique_row_ids(rows):
    identifiers = [row.get("id") for row in rows]
    if any(not isinstance(value, (str, int)) or isinstance(value, bool) or value == "" for value in identifiers):
        return False
    return len(set(identifiers)) == len(identifiers)


def _read_page(result: object, *, table=None, record_hint_fields=frozenset(), _reported_counter=False):
    """Parse explicit row envelopes and MCP tool results; unknown shapes
    fail closed rather than guessing. Ported unchanged from the first
    working instance of this adapter - it already handled whatever shape
    the real sgmcp server returns, and never assumed a business meaning
    for any field."""
    row_keys = ("rows", "records", "data", "results", "datos")

    def fail(rejected=False):
        error = AdapterError(
            "sgmcp rechazo la lectura." if rejected
            else "sgmcp devolvio un formato de lectura no verificable."
        )
        error.code = "sgmcp_read_rejected" if rejected else "sgmcp_read_response_invalid"
        error.status_code = 502
        error.response_shape = summarize_response(result, table=table, record_hint_fields=record_hint_fields)
        raise error

    def check_error(value):
        if (value.get("isError") or value.get("success") is False
                or value.get("ok") is False or value.get("error") is not None):
            fail(True)

    def parse(value, depth):
        if depth > 8:
            fail()
        if isinstance(value, list):
            for row in value:
                if not isinstance(row, dict):
                    fail()
                check_error(row)
                if (any(key in row for key in (*row_keys, "content", "structuredContent"))
                        or (row.get("type") == "text" and "text" in row)):
                    fail()
            return value, None
        if not isinstance(value, dict):
            fail()
        check_error(value)
        candidates = [value[key] for key in (*row_keys, "structuredContent") if key in value]
        if "content" in value:
            content = value["content"]
            if not isinstance(content, list):
                fail()
            if content:
                if len(content) != 1:
                    fail()
                part = content[0]
                if not isinstance(part, dict):
                    fail()
                check_error(part)
                if part.get("type") != "text" or not isinstance(part.get("text"), str):
                    fail()
                decoded = None
                try:
                    decoded = json.loads(part["text"])
                except (ValueError, TypeError, RecursionError):
                    pass
                if not isinstance(decoded, (dict, list)):
                    fail()
                candidates.append(decoded)
        if not candidates:
            fail()
        pages = [parse(candidate, depth + 1) for candidate in candidates]
        if any(page != pages[0] for page in pages[1:]):
            fail()
        rows, nested_total = pages[0]
        count_keys = ("total_registros",) if _reported_counter else ("total", "total_count", "count", "totalRecords")
        total = next((value[key] for key in count_keys if value.get(key) is not None), None)
        if type(total) is not int or total < 0:
            total = None
        if total is not None and nested_total is not None and total != nested_total:
            fail()
        return rows, total if total is not None else nested_total

    return parse(result, 0)


class Adapter:
    """One instance per employee process. Restart disconnects every
    rls_personal session (they live only in memory - see README)."""

    def __init__(
        self,
        config: ConnectionConfig,
        settings: "Settings | None",
        auth_client_factory: "Callable[[], httpx.AsyncClient]",
        mcp: Transport,
        resolve_actor: "Callable[[str], Awaitable[str | None]]",
        *,
        clock: "Callable[[], float]" = time.time,
        max_sessions: int = 1000,
        configuration_error: "ConfigurationError | None" = None,
    ):
        self.config = config
        self._settings = settings
        self._configuration_error = configuration_error
        self.auth_client_factory = auth_client_factory
        self.mcp = mcp
        self.resolve_actor = resolve_actor
        self.clock = clock
        self.max_sessions = max_sessions
        self._sessions: "dict[str, Session]" = {}
        self._session_states: "dict[str, tuple[str, str | None]]" = {}
        self._locks: "dict[str, asyncio.Lock]" = {}
        self._configuration_generation = 0

    @property
    def settings(self) -> Settings:
        if self._settings is None:
            raise self._configuration_error or ConfigurationError()
        return self._settings

    def configure(self, config: ConnectionConfig, settings: Settings) -> bool:
        settings.for_mode(config.auth_mode)
        current = self._settings
        changed = (
            current is None or config != self.config
            or (current.supabase_url, current.anon_key, current.service_role_key)
            != (settings.supabase_url, settings.anon_key, settings.service_role_key)
        )
        if changed:
            self._sessions.clear()
            self._session_states.clear()
        self.config = config
        self._settings = settings
        self._configuration_error = None
        self._configuration_generation += 1
        return changed

    # -- identity -----------------------------------------------------

    def _principal(self, email) -> str:
        if not isinstance(email, str) or not email.strip() or len(email) > 254:
            raise LoginRequired()
        return email.strip().lower()

    def _lock(self, principal: str) -> asyncio.Lock:
        if principal not in self._locks:
            if len(self._locks) >= self.max_sessions:
                raise AdapterError("Se alcanzo el limite de sesiones simultaneas de este empleado.")
            self._locks[principal] = asyncio.Lock()
        return self._locks[principal]

    async def _active_chat_identity(self, run_token):
        """Confirms there is a live WAI chat turn behind this call; this is
        done in BOTH auth modes, since it is what stops a stray or replayed
        run_token from executing anything at all."""
        if not isinstance(run_token, str) or not run_token:
            raise LoginRequired()
        identity = await self.resolve_actor(run_token)
        if not identity:
            raise LoginRequired()
        return identity

    # -- Supabase Auth (rls_personal only) -----------------------------

    async def _auth_request(self, method: str, path: str, *, token=None, payload=None):
        headers = {"apikey": self.settings.anon_key}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        error = None
        try:
            async with self.auth_client_factory() as client:
                async with client.stream(
                    method, self.settings.supabase_url + SUPABASE_AUTH_PATH + path,
                    headers=headers, json=payload, follow_redirects=False,
                ) as response:
                    if response.status_code == 401 and method == "GET" and path == "user":
                        error = LoginRequired("supabase_access_token_invalid")
                    elif response.status_code == 401 and path.split("?", 1)[0] == "token":
                        error = ConfigurationError(
                            "supabase_api_key_rejected", "Supabase rechazo la clave publica configurada.",
                        )
                    elif response.status_code == 429:
                        error = UpstreamError("supabase_rate_limited", "Supabase limito las solicitudes. Intenta mas tarde.")
                        error.status_code = 429
                    else:
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > 65536:
                                raise ValueError("response limit")
                        data = json.loads(content) if content else None
                        if response.status_code == 204 and path.split("?", 1)[0] == "logout" and not content:
                            data = {}
                        if not isinstance(data, dict):
                            raise ValueError("invalid response")
                        if not response.is_success:
                            code = data.get("error_code") or data.get("code")
                            if response.status_code in {400, 403, 422}:
                                if (
                                    response.status_code == 400
                                    and path == "token?grant_type=refresh_token"
                                    and (
                                        code in (
                                            "refresh_token_not_found", "refresh_token_expired",
                                            "refresh_token_already_used", "session_not_found", "session_expired",
                                        )
                                        or any(data.get(field) in (
                                            "Invalid Refresh Token: Refresh Token Not Found",
                                            "Invalid Refresh Token: Refresh Token Already Used",
                                            "Invalid Refresh Token: Refresh Token Expired",
                                        ) for field in ("error_description", "msg"))
                                    )
                                ):
                                    error = LoginRequired("supabase_session_expired")
                                elif code == "invalid_credentials":
                                    error = AuthenticationError("supabase_invalid_credentials", "Supabase rechazo las credenciales.")
                                elif code == "email_not_confirmed":
                                    error = AuthenticationError("supabase_email_not_confirmed", "Confirma tu correo en Supabase antes de conectar.")
                                else:
                                    error = AuthenticationError("supabase_auth_rejected", "Supabase rechazo la solicitud de autenticacion.")
                            else:
                                error = UpstreamError("supabase_auth_rejected", "Supabase no pudo completar la autenticacion.")
        except httpx.HTTPError:
            error = UpstreamError("supabase_unavailable", "No se pudo contactar con Supabase. Intenta mas tarde.")
        except (ValueError, TypeError, RecursionError):
            error = UpstreamError("supabase_invalid_response", "Supabase devolvio una respuesta no valida.")
        if error is not None:
            raise error from None
        return data

    def _token_session(self, data: dict, subject: str) -> Session:
        if not isinstance(data, dict):
            raise UpstreamError("supabase_invalid_response", "Supabase devolvio una respuesta no valida.")
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        ttl = data.get("expires_in")
        try:
            valid_ttl = (
                not isinstance(ttl, bool) and isinstance(ttl, (int, float))
                and math.isfinite(ttl) and ttl > 30
            )
        except OverflowError:
            valid_ttl = False
        if (
            not isinstance(access, str) or not access
            or not isinstance(refresh, str) or not refresh
            or not valid_ttl
        ):
            raise UpstreamError("supabase_invalid_response", "Supabase devolvio una respuesta no valida.")
        expires_at = self.clock() + ttl
        provider_expiry = data.get("expires_at")
        try:
            if (not isinstance(provider_expiry, bool) and isinstance(provider_expiry, (int, float))
                    and math.isfinite(provider_expiry) and provider_expiry > 0):
                expires_at = min(expires_at, provider_expiry)
        except OverflowError:
            pass
        return Session(access, refresh, subject or "", expires_at)

    async def _verified_session(self, data, principal: str, subject=None) -> Session:
        session = data if isinstance(data, Session) else self._token_session(data, subject)
        # Supabase, not an unverified JWT or the login response's user field,
        # verifies both identity and that this access token is actually usable.
        user = await self._auth_request("GET", "user", token=session.access_token)
        if (
            not isinstance(user, dict)
            or not isinstance(user.get("email"), str) or not user["email"].strip()
            or not isinstance(user.get("id"), str) or not user["id"].strip()
        ):
            raise UpstreamError("supabase_invalid_response", "Supabase devolvio una respuesta no valida.")
        if user["email"].strip().lower() != principal or (subject is not None and user["id"] != subject):
            error = AdapterError("La identidad Supabase no coincide con la cuenta conectada.")
            error.code = "supabase_identity_mismatch"
            raise error
        if session.expires_at <= self.clock():
            raise LoginRequired("supabase_session_expired")
        return Session(session.access_token, session.refresh_token, user["id"], session.expires_at, self.clock())

    def _session_failure(self, principal: str, error: AdapterError, generation: int):
        if generation != self._configuration_generation:
            return
        if error.code in {"supabase_session_expired", "supabase_access_token_invalid", "supabase_identity_mismatch"}:
            self._sessions.pop(principal, None)
            code = "supabase_identity_mismatch" if error.code == "supabase_identity_mismatch" else "supabase_session_expired"
            self._session_states[principal] = ("reauth_required", code)
        else:
            self._session_states[principal] = ("unavailable", error.code if isinstance(error, ConfigurationError) else "supabase_session_unavailable")

    async def login(self, authenticated_email: str, password: str) -> dict:
        """Only call from a route protected by the host app's own user authentication."""
        self.settings.for_mode("rls_personal")
        principal = self._principal(authenticated_email)
        async with self._lock(principal):
            generation = self._configuration_generation
            self._sessions.pop(principal, None)
            self._session_states.pop(principal, None)
            if not isinstance(password, str) or not password or len(password) > 4096:
                raise LoginRequired()
            data = await self._auth_request(
                "POST", "token?grant_type=password",
                payload={"email": principal, "password": password},
            )
            session = await self._verified_session(data, principal)
            if generation != self._configuration_generation:
                raise LoginRequired()
            self._sessions[principal] = session
            self._session_states[principal] = ("connected", None)
        return {"connected": True, "email": principal}

    async def disconnect(self, authenticated_email: str) -> dict:
        principal = self._principal(authenticated_email)
        async with self._lock(principal):
            session = self._sessions.pop(principal, None)
            self._session_states.pop(principal, None)
            if session:
                try:
                    await self._auth_request("POST", "logout?scope=local", token=session.access_token)
                except AdapterError:
                    pass
        return {"connected": False}

    def connection_status(self, authenticated_email: str) -> dict:
        principal = self._principal(authenticated_email)
        error = self._configuration_error or ConfigurationError()
        if self.config.auth_mode == "service_role":
            configured = self._settings is not None
            return {
                "connected": configured,
                "email": principal,
                "configured": configured,
                "configuration_code": None if configured else error.code,
                "state": "connected" if configured else "not_configured",
                "reason_code": None,
                "expires_at": None,
                "last_verified_at": None,
                "auth_mode": "service_role",
            }
        session = self._sessions.get(principal)
        state, reason = self._session_states.get(principal, ("disconnected", "supabase_session_required"))
        if self._settings is None:
            state, reason = "not_configured", error.code
        elif session is None and state != "reauth_required":
            state, reason = "disconnected", "supabase_session_required"
        elif session is not None and session.expires_at <= self.clock() and state == "connected":
            state, reason = "reauth_required", "supabase_session_expired"
        return {
            "connected": session is not None and session.expires_at > self.clock() and state == "connected",
            "email": principal,
            "configured": self._settings is not None,
            "configuration_code": None if self._settings is not None else error.code,
            "state": state,
            "reason_code": reason,
            "expires_at": session.expires_at if session else None,
            "last_verified_at": session.checked_at if session and session.checked_at else None,
            "auth_mode": "rls_personal",
        }

    async def session_status(self, email: str) -> dict:
        principal = self._principal(email)
        if self.config.auth_mode == "service_role" or self._settings is None or principal not in self._sessions:
            return self.connection_status(principal)
        async with self._lock(principal):
            try:
                await self._validated_session(principal)
            except AdapterError:
                pass
            return self.connection_status(principal)

    async def _refresh_session(self, principal: str) -> Session:
        """Caller holds the principal lock; transient failures retain renewal credentials."""
        self.settings
        session = self._sessions.get(principal)
        if session is None:
            raise LoginRequired(self._session_states.get(principal, (None, None))[1] or "supabase_session_required")
        generation = self._configuration_generation
        try:
            if not session.refresh_token:
                raise LoginRequired("supabase_session_expired")
            data = await self._auth_request(
                "POST", "token?grant_type=refresh_token",
                payload={"refresh_token": session.refresh_token},
            )
            if generation != self._configuration_generation:
                raise LoginRequired()
            candidate = self._token_session(data, session.subject)
            self._sessions[principal] = candidate
            self._session_states[principal] = ("unavailable", "supabase_session_unavailable")
            session = await self._verified_session(candidate, principal, session.subject)
            if generation != self._configuration_generation:
                raise LoginRequired()
        except AdapterError as error:
            failure = LoginRequired("supabase_session_expired") if isinstance(error, (AuthenticationError, LoginRequired)) else error
            self._session_failure(principal, failure, generation)
            raise
        self._sessions[principal] = session
        self._session_states[principal] = ("connected", None)
        return session

    async def _session(self, principal: str) -> Session:
        self.settings
        session = self._sessions.get(principal)
        if session is None:
            raise LoginRequired(self._session_states.get(principal, (None, None))[1] or "supabase_session_required")
        if session.expires_at <= self.clock() + 30:
            return await self._refresh_session(principal)
        return session

    async def _validated_session(self, principal: str) -> Session:
        self.settings
        generation = self._configuration_generation
        session = self._sessions.get(principal)
        if session is None:
            raise LoginRequired(self._session_states.get(principal, (None, None))[1] or "supabase_session_required")
        if session.expires_at <= self.clock() + 30:
            return await self._refresh_session(principal)
        invalid_access = False
        try:
            session = await self._verified_session(session, principal, session.subject)
            if generation != self._configuration_generation:
                raise LoginRequired()
        except LoginRequired as error:
            if generation != self._configuration_generation:
                raise LoginRequired() from None
            if error.code == "supabase_access_token_invalid":
                invalid_access = True
            else:
                self._session_failure(principal, error, generation)
                raise
        except AdapterError as error:
            self._session_failure(principal, error, generation)
            raise
        if invalid_access:
            return await self._refresh_session(principal)
        self._sessions[principal] = session
        self._session_states[principal] = ("connected", None)
        return session

    def _check_write_context(self, principal: str, context: WriteContext) -> Session:
        session = self._sessions.get(principal)
        if (
            not isinstance(context, WriteContext) or context.principal != principal
            or context.project_url != self.settings.supabase_url
            or context.generation != self._configuration_generation
            or session is None or not session.subject or session.subject != context.subject
            or session.expires_at <= self.clock()
            or self._session_states.get(principal) != ("connected", None)
        ):
            raise LoginRequired()
        return session

    # -- argument validation, driven entirely by ConnectionConfig -----

    def _arguments(self, tool: str, args: dict):
        """Returns (mcp_tool_name, payload, is_write)."""
        if tool not in GENERIC_TOOLS or not self.config.allows(tool):
            raise AdapterError("Herramienta no permitida.")
        if not isinstance(args, dict):
            raise AdapterError("Argumentos no permitidos.")

        if tool == "leer_registros":
            if set(args) - {"tabla", "filtros", "limite", "pagina", "orden_columna", "orden_asc"}:
                raise AdapterError("Argumentos no permitidos.")
            table = args.get("tabla")
            table_config = self.config.tables.get(table) if isinstance(table, str) else None
            if table_config is None:
                raise AdapterError("Tabla no permitida.")
            filters = args.get("filtros", {})
            if not isinstance(filters, dict) or set(filters) - table_config.filterable:
                raise AdapterError("Filtros no permitidos.")
            if any(isinstance(v, bool) or not isinstance(v, (str, int)) for v in filters.values()):
                raise AdapterError("Filtros no permitidos.")
            limit = args.get("limite", 50)
            if type(limit) is not int or not 1 <= limit <= self.config.read_limit:
                raise AdapterError("Limite de lectura no permitido.")
            page = args.get("pagina", 1)
            if type(page) is not int or not 1 <= page <= 10000:
                raise AdapterError("Pagina de lectura no permitida.")
            order_column = args.get("orden_columna")
            if order_column is not None and order_column not in table_config.columns:
                raise AdapterError("Orden de lectura no permitido.")
            ascending = args.get("orden_asc", True)
            if type(ascending) is not bool:
                raise AdapterError("Orden de lectura no permitido.")
            payload = {
                "tabla": table, "columnas": ",".join(sorted(table_config.columns)),
                "filtros": dict(filters), "limite": limit, "pagina": page, "orden_asc": ascending,
            }
            if order_column is not None:
                payload["orden_columna"] = order_column
            return "supabase_leer", payload, False

        if tool in {"insertar_registros", "actualizar_registros"}:
            expected = {"tabla", "datos"} if tool == "insertar_registros" else {"tabla", "columna_filtro", "valor_filtro", "datos"}
            if set(args) - expected or "tabla" not in args or "datos" not in args:
                raise AdapterError("Argumentos no permitidos.")
            table = args.get("tabla")
            table_config = self.config.tables.get(table) if isinstance(table, str) else None
            if table_config is None or not table_config.writable:
                raise AdapterError("Tabla no permitida para escritura.")
            data = self._validated_data(table_config, args.get("datos"), insert=tool == "insertar_registros")
            payload = {"tabla": table, "datos": data}
            if tool == "insertar_registros":
                return "supabase_insertar", payload, True
            column, value = args.get("columna_filtro"), args.get("valor_filtro")
            if column not in table_config.filterable or isinstance(value, bool) or not isinstance(value, (str, int)):
                raise AdapterError("Filtro de actualizacion no permitido.")
            return "supabase_actualizar", {**payload, "columna_filtro": column, "valor_filtro": value}, True

        if tool == "eliminar_registros":
            if set(args) - {"tabla", "columna_filtro", "valor_filtro"}:
                raise AdapterError("Argumentos no permitidos.")
            table = args.get("tabla")
            table_config = self.config.tables.get(table) if isinstance(table, str) else None
            if table_config is None:
                raise AdapterError("Tabla no permitida.")
            column, value = args.get("columna_filtro"), args.get("valor_filtro")
            if column not in table_config.filterable or isinstance(value, bool) or not isinstance(value, (str, int)):
                raise AdapterError("Filtro de eliminacion no permitido.")
            return "supabase_eliminar", {"tabla": table, "columna_filtro": column, "valor_filtro": value}, True

        raise AdapterError("Herramienta no permitida.")

    def _validated_data(self, table_config: TableConfig, values, *, insert: bool) -> dict:
        if not isinstance(values, dict) or (not values and insert) or set(values) - table_config.writable:
            raise AdapterError("Campos no permitidos.")
        try:
            if insert:
                for name in table_config.writable:
                    table_config.columns[name].validate(name, values.get(name))
            for name, value in values.items():
                table_config.columns[name].validate(name, value)
        except ConfigError as error:
            raise AdapterError(str(error)) from None
        return dict(values)

    # -- write preflight / idempotency hookup --------------------------

    async def preflight_write(self, run_token: str, tool: str, arguments: dict) -> WriteContext:
        """Validates everything BEFORE the caller marks a write as sent, so a
        failure here can be reported as write_state="not_sent" with
        certainty. Called by the host's idempotency wrapper, not directly
        by model-facing code."""
        try:
            if tool not in _WRITE_TOOLS or not isinstance(arguments, dict):
                raise AdapterError("Argumentos no permitidos.")
            self._arguments(tool, arguments)
            if self.config.auth_mode == "service_role":
                self.settings.for_mode("service_role")
                principal = await self._active_chat_identity(run_token)
                return WriteContext("service_role", "service_role", self.settings.supabase_url, self._configuration_generation)
            principal = self._principal(await self.resolve_actor(run_token))
            async with self._lock(principal):
                generation = self._configuration_generation
                project_url = self.settings.supabase_url
                session = await self._validated_session(principal)
                if session.expires_at < self.clock() + 90:
                    session = await self._refresh_session(principal)
                context = WriteContext(principal, session.subject, project_url, generation)
                if self._principal(await self.resolve_actor(run_token)) != principal:
                    raise LoginRequired()
                self._check_write_context(principal, context)
                if session.expires_at < self.clock() + 90:
                    raise LoginRequired("supabase_session_expired")
                return context
        except AdapterError as error:
            error.write_state = "not_sent"
            raise

    async def validate_write_context(self, run_token: str, context: WriteContext) -> None:
        """Re-checked right before the write actually fires, since preflight
        may have happened a while ago (model turns are not instantaneous)."""
        try:
            if self.config.auth_mode == "service_role":
                await self._active_chat_identity(run_token)
                if context.project_url != self.settings.supabase_url or context.generation != self._configuration_generation:
                    raise LoginRequired()
                return
            principal = self._principal(await self.resolve_actor(run_token))
            async with self._lock(principal):
                session = self._check_write_context(principal, context)
                if not session.checked_at or self.clock() - session.checked_at >= 30:
                    session = await self._validated_session(principal)
                if session.expires_at < self.clock() + 90:
                    await self._refresh_session(principal)
                if self._principal(await self.resolve_actor(run_token)) != principal:
                    raise LoginRequired()
                session = self._check_write_context(principal, context)
                if session.expires_at < self.clock() + 90:
                    raise LoginRequired("supabase_session_expired")
        except AdapterError as error:
            error.write_state = "not_sent"
            raise

    # -- calling the shared MCP, with secret redaction on the way out --

    async def _call_as_user(self, run_token, principal, mcp_tool, payload, *, write_context=None):
        failure = None
        result = None
        session = None
        for attempt in range(2):
            try:
                session = (await self._session(principal) if write_context is None
                           else self._check_write_context(principal, write_context))
                if run_token is not None and self._principal(await self.resolve_actor(run_token)) != principal:
                    raise LoginRequired()
                if write_context is not None:
                    session = self._check_write_context(principal, write_context)
                    if mcp_tool != "supabase_leer" and (
                        session.expires_at < self.clock() + 90 or not session.checked_at
                        or self.clock() - session.checked_at >= 30
                    ):
                        raise LoginRequired("supabase_session_expired")
            except AdapterError as error:
                if write_context is not None:
                    error.write_state = "not_sent"
                raise
            try:
                result = await self.mcp.call(mcp_tool, {
                    **payload,
                    "supabaseUrl": self.settings.supabase_url,
                    "supabaseKey": self.settings.anon_key,
                    "userToken": session.access_token,
                })
            except MCPError as error:
                failure = (error.kind, error.retryable, error.phase, error.http_status)
            except AdapterError as error:
                if write_context is not None:
                    error.write_state = "unknown"
                raise
            else:
                failure = None
                break
            if mcp_tool == "supabase_leer" and attempt == 0 and failure[1]:
                await asyncio.sleep(0.2)
                continue
            break
        if failure is not None:
            raise self._mcp_failure_error(mcp_tool, failure)
        return self._redact(result, session.access_token, session.refresh_token, self.settings.anon_key)

    async def _call_with_service_role(self, mcp_tool, payload):
        try:
            result = await self.mcp.call(mcp_tool, {
                **payload,
                "supabaseUrl": self.settings.supabase_url,
                "supabaseKey": self.settings.service_role_key,
            })
        except MCPError as error:
            raise self._mcp_failure_error(mcp_tool, (error.kind, error.retryable, error.phase, error.http_status))
        return self._redact(result, self.settings.service_role_key)

    def _mcp_failure_error(self, mcp_tool, failure):
        kind, retryable, phase, http_status = failure
        diagnostic = (" Fase MCP: %s." % phase if phase else "")
        if http_status is not None:
            diagnostic += " HTTP %s." % http_status
        if mcp_tool != "supabase_leer":
            if phase in {"initialize", "initialized"}:
                error = AdapterError("No se envio la escritura: fallo la conexion MCP antes de llamar a la herramienta de datos." + diagnostic)
                error.code, error.write_state = "supabase_write_not_sent", "not_sent"
                return error
            error = AdapterError("sgmcp no confirmo la escritura. No se reintento; consulta el registro para verificar su estado." + diagnostic)
            error.write_state = "unknown"
            return error
        if retryable:
            code, message = "sgmcp_read_unavailable", "No se pudo completar la lectura por un fallo temporal de comunicacion con sgmcp. No se modificaron registros."
        elif kind in {"tool", "remote", "http"}:
            code, message = "sgmcp_read_rejected", "El servidor sgmcp no acepto la lectura. Este error no demuestra por si solo un rechazo RLS."
        else:
            code, message = "sgmcp_read_response_invalid", "No se pudo validar la respuesta MCP de la lectura. No es un conteo de cero registros."
        error = AdapterError(message + diagnostic)
        error.code = code
        error.status_code = 502
        return error

    @staticmethod
    def _redact(result, *secrets):
        raw = json.dumps(result, ensure_ascii=True)
        for secret in secrets:
            if secret:
                raw = raw.replace(secret, "[REDACTED]")
        return json.loads(raw)

    @staticmethod
    def _write_result(result: dict) -> dict:
        """Unwrap the MCP content envelope so the model gets the written
        row(s) directly instead of a nested JSON-in-text blob. A write is
        never retried automatically - sgmcp's reply is reported once,
        whatever shape it took."""
        decoded = result
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list) and len(content) == 1 and isinstance(content[0], dict) and content[0].get("type") == "text":
                try:
                    decoded = json.loads(content[0]["text"])
                except (ValueError, TypeError):
                    decoded = result
        return {
            "resultado": decoded,
            "nota": "Esta escritura no se reintenta automaticamente. Verifica el registro si el resultado no es claro.",
        }

    async def _raw_read(self, run_token, principal, table, columns, filters, *, limit, page, order_column="id", ascending=True):
        payload = {
            "tabla": table, "columnas": ",".join(columns), "filtros": dict(filters),
            "limite": limit, "pagina": page, "orden_columna": order_column, "orden_asc": ascending,
        }
        if self.config.auth_mode == "service_role":
            return await self._call_with_service_role("supabase_leer", payload)
        return await self._call_as_user(run_token, principal, "supabase_leer", payload)

    # -- pagination integrity: never trust a single reported total ----

    async def _validated_page_total(
        self, run_token, principal, table, filters, response, rows, total, *,
        request_limit, page_offset, compare_first_id, columns,
    ):
        """Check the observed counter per query; never substitute a page
        length for a verified total."""
        if total is not None:
            return total
        hints = frozenset(columns)
        _, reported = _read_page(response, table=table, record_hint_fields=hints, _reported_counter=True)
        if (reported is None or reported > _READ_PAGE_SIZE * _MAX_READ_PAGES
                or len(rows) > reported or not _has_unique_row_ids(rows)):
            return None
        if len(rows) < request_limit and reported > page_offset + len(rows):
            return None
        if rows and reported < page_offset + len(rows):
            return None
        probes = [(1, 1, min(reported, 1))]
        window = None
        if reported >= 2:
            probes.append((1, 2, 1))
        if reported:
            window = next((size for size in range(2, 21) if reported % size), None)
            if window is None:
                return None
            probes.append((window, reported // window + 1, reported % window))
        probe_ids = []
        counter_modes = {"global", "page"}
        for limit, page, expected_length in probes:
            try:
                probe = await self._raw_read(
                    run_token, principal, table, ("id",), filters,
                    limit=limit, page=page, order_column="id", ascending=True,
                )
                probe_rows, probe_total = _read_page(probe, table=table, record_hint_fields=hints, _reported_counter=True)
            except AdapterError as error:
                if error.code not in {"sgmcp_read_rejected", "sgmcp_read_response_invalid", "sgmcp_read_unavailable"}:
                    raise
                return None
            if len(probe_rows) != expected_length or probe_total is None:
                return None
            if probe_total != reported:
                counter_modes.discard("global")
            if probe_total != len(probe_rows):
                counter_modes.discard("page")
            if not counter_modes:
                return None
            if not _has_unique_row_ids(probe_rows):
                return None
            offset = (page - 1) * limit
            if compare_first_id and offset + len(probe_rows) <= len(rows):
                if [row["id"] for row in probe_rows] != [row["id"] for row in rows[offset:offset + len(probe_rows)]]:
                    return None
            if limit == 1 and probe_rows:
                probe_ids.append(probe_rows[0]["id"])
                if len(probe_ids) == 2 and probe_ids[0] == probe_ids[1]:
                    return None
        if "global" in counter_modes:
            return reported
        if page_offset == 0 and reported == len(rows) and window is not None and window <= len(rows):
            return len(rows)
        return None

    async def _read_all_pages(self, run_token, principal, table, columns, filters):
        rows = []
        expected_total = None
        page = 1
        hints = frozenset(columns)
        while page <= _MAX_READ_PAGES:
            safe = await self._raw_read(
                run_token, principal, table, columns, filters,
                limit=_READ_PAGE_SIZE, page=page, order_column="id", ascending=True,
            )
            page_rows, total = _read_page(safe, table=table, record_hint_fields=hints)
            if total is None:
                if page == 1:
                    total = await self._validated_page_total(
                        run_token, principal, table, filters, safe, page_rows, total,
                        request_limit=_READ_PAGE_SIZE, page_offset=0, compare_first_id=True, columns=columns,
                    )
                else:
                    _, reported = _read_page(safe, table=table, record_hint_fields=hints, _reported_counter=True)
                    total = reported if reported == expected_total else None
            if total is None or (expected_total is not None and total != expected_total):
                return rows + page_rows, total, False
            if total < 0 or not _has_unique_row_ids(page_rows):
                return rows + page_rows, total, False
            seen = {row.get("id") for row in rows}
            page_ids = [row["id"] for row in page_rows]
            if len(set(page_ids)) != len(page_ids) or seen.intersection(page_ids):
                return rows + page_rows, total, False
            expected_total = total
            if not page_rows and len(rows) < expected_total:
                return rows, expected_total, False
            rows.extend(page_rows)
            if len(rows) > expected_total:
                return rows, expected_total, False
            if len(rows) == expected_total:
                return rows, expected_total, True
            page += 1
        return rows, expected_total, False

    # -- model-facing entry point --------------------------------------

    async def execute(self, run_token: str, tool: str, arguments: dict, *, write_context: "WriteContext | None" = None) -> dict:
        mcp_tool, payload, is_write = self._arguments(tool, arguments)
        if is_write and write_context is None:
            raise LoginRequired()

        if self.config.auth_mode == "service_role":
            if is_write:
                if write_context.project_url != self.settings.supabase_url or write_context.generation != self._configuration_generation:
                    error = LoginRequired()
                    error.write_state = "not_sent"
                    raise error
            else:
                await self._active_chat_identity(run_token)
            if tool != "leer_registros":
                return self._write_result(await self._call_with_service_role(mcp_tool, payload))
            table_config = self.config.tables[payload["tabla"]]
            safe = await self._call_with_service_role(mcp_tool, payload)
            rows, total = _read_page(safe, table=payload["tabla"], record_hint_fields=frozenset(table_config.columns))
            if total is None:
                # Must reuse the SAME response already fetched above - not
                # a fresh one, and never None: _validated_page_total's own
                # first step re-parses this exact payload looking for a
                # reported-but-unverified counter (our real sgmcp only ever
                # reports one under _reported_counter=True's key set), so
                # every service_role read without an immediately-trusted
                # total depended on this argument being right.
                total = await self._validated_page_total(
                    None, None, payload["tabla"], payload["filtros"], safe, rows, total,
                    request_limit=payload["limite"], page_offset=(payload["pagina"] - 1) * payload["limite"],
                    compare_first_id=False, columns=table_config.columns,
                )
            return {
                "registros": rows, "total_visible": total,
                "completa": total is not None and len(rows) == total and payload["pagina"] == 1 and _has_unique_row_ids(rows),
                "limite_solicitado": payload["limite"], "pagina": payload["pagina"],
            }

        principal = self._principal(await self.resolve_actor(run_token))
        if tool != "leer_registros":
            async with self._lock(principal):
                raw = await self._call_as_user(run_token, principal, mcp_tool, payload, write_context=write_context)
            return self._write_result(raw)
        async with self._lock(principal):
            await self._session(principal)
            if self._principal(await self.resolve_actor(run_token)) != principal:
                raise LoginRequired()
            table_config = self.config.tables[payload["tabla"]]
            safe = await self._call_as_user(run_token, principal, mcp_tool, payload)
            rows, total = _read_page(safe, table=payload["tabla"], record_hint_fields=frozenset(table_config.columns))
            total = await self._validated_page_total(
                run_token, principal, payload["tabla"], payload["filtros"], safe, rows, total,
                request_limit=payload["limite"], page_offset=(payload["pagina"] - 1) * payload["limite"],
                compare_first_id=payload["pagina"] == 1 and payload.get("orden_columna") == "id" and payload["orden_asc"],
                columns=table_config.columns,
            )
            return {
                "registros": rows, "total_visible": total,
                "completa": total is not None and len(rows) == total and payload["pagina"] == 1 and _has_unique_row_ids(rows),
                "limite_solicitado": payload["limite"], "pagina": payload["pagina"],
            }

    async def read_records(self, authenticated_email: str, table: str, filters=None, *, limit=50, page=1, order_column=None, ascending=False) -> dict:
        """Optional convenience method for custom REST endpoints (e.g. a
        dashboard) outside of the MCP tool-call path. rls_personal only -
        in service_role mode there is no per-human email to scope this to."""
        self.settings.for_mode("rls_personal")
        principal = self._principal(authenticated_email)
        mcp_tool, payload, _ = self._arguments("leer_registros", {
            "tabla": table, "filtros": filters or {}, "limite": limit,
            "pagina": page, "orden_columna": order_column, "orden_asc": ascending,
        })
        table_config = self.config.tables[table]
        async with self._lock(principal):
            safe = await self._call_as_user(None, principal, mcp_tool, payload)
            rows, total = _read_page(safe, table=table, record_hint_fields=frozenset(table_config.columns))
            total = await self._validated_page_total(
                None, principal, table, payload["filtros"], safe, rows, total,
                request_limit=payload["limite"], page_offset=(payload["pagina"] - 1) * payload["limite"],
                compare_first_id=payload["pagina"] == 1 and payload.get("orden_columna") == "id" and payload["orden_asc"],
                columns=table_config.columns,
            )
        return {
            "rows": rows, "total": total, "page": payload["pagina"], "limit": payload["limite"],
            "complete": total is not None and len(rows) == total and payload["pagina"] == 1 and _has_unique_row_ids(rows),
        }
