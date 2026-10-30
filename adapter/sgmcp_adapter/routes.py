"""Login and admin-configuration routes, built on the host app's own
WAI-authenticated identity - never a caller-chosen email."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from .core import Adapter, AdapterError


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: SecretStr = Field(min_length=1, max_length=4096)


class ConfigurationInput(BaseModel):
    """Everything an administrator fills in when connecting (or
    reconnecting) one employee to one Supabase project. ``tables``/
    ``denied``/``read_limit`` use the same JSON shape as
    ConnectionConfig.to_dict()/from_dict() in config.py.

    ``Optional[...]`` instead of ``X | None`` here on purpose: pydantic
    evaluates these annotations at class-definition time, where the
    newer ``|`` union syntax needs Python 3.10+ even with
    ``from __future__ import annotations`` active."""

    model_config = ConfigDict(extra="forbid")
    project_url: str = Field(min_length=1, max_length=2048)
    auth_mode: str = Field(pattern="^(rls_personal|service_role)$")
    anon_key: Optional[SecretStr] = Field(default=None, min_length=1, max_length=8192)
    service_role_key: Optional[SecretStr] = Field(default=None, min_length=1, max_length=8192)
    tables: dict = Field(min_length=1)
    denied: list = Field(default_factory=list)
    read_limit: int = Field(default=10000, ge=1, le=10000)


class PrivateValidationRoute(APIRoute):
    """Never let a validation or adapter error leak a stack trace or a
    secret - only fixed, pre-written messages cross this boundary. The one
    exception: a bounded, value-free "shape" of an unexpected sgmcp
    response (see response_shape.py) is attached for an administrator
    only, to help diagnose a misconfigured table/column allowlist."""

    def get_route_handler(self):
        handler = super().get_route_handler()

        async def sanitized(request: Request):
            try:
                return await handler(request)
            except RequestValidationError:
                return JSONResponse({"detail": {"code": "supabase_request_invalid", "message": "Solicitud de conexion no valida."}}, status_code=422)
            except AdapterError as error:
                detail = {"code": error.code, "message": str(error)}
                shape = getattr(error, "response_shape", None)
                authorize = getattr(request.state, "supabase_diagnostic_authorizer", None)
                email = getattr(request.state, "supabase_diagnostic_email", None)
                if shape is not None and authorize is not None and email:
                    try:
                        allowed = await authorize(email)
                    except Exception:
                        allowed = False
                    if allowed:
                        detail["response_shape"] = shape
                return JSONResponse({"detail": detail}, status_code=error.status_code)

        return sanitized


def connection_router(
    adapter: Adapter, authenticated_user_dependency, *, admin_dependency=None,
    configuration_loader=None, configuration_saver=None, can_configure=None,
    diagnostic_authorizer=None,
) -> APIRouter:
    """Supply the host app's own ``get_current_user`` dependency, never a
    caller-chosen email."""
    router = APIRouter(prefix="/api/supabase", tags=["Supabase"], route_class=PrivateValidationRoute)

    async def connected_user(request: Request, user=Depends(authenticated_user_dependency)):
        if configuration_loader:
            await configuration_loader()
        request.state.supabase_diagnostic_email = user.email
        request.state.supabase_diagnostic_authorizer = diagnostic_authorizer
        return user

    def configuration_metadata() -> dict:
        settings = adapter._settings
        return {
            "project_url": settings.supabase_url if settings else None,
            "auth_mode": adapter.config.auth_mode,
            "has_anon_key": settings is not None and settings.anon_key is not None,
            "has_service_role_key": settings is not None and settings.service_role_key is not None,
        }

    @router.get("/session")
    async def status(user=Depends(connected_user)):
        configurable = bool(can_configure and await can_configure(user.email))
        return {**(await adapter.session_status(user.email)), **configuration_metadata(), "can_configure": configurable}

    @router.get("/configuration")
    async def configuration(user=Depends(admin_dependency or authenticated_user_dependency)):
        if admin_dependency is None:
            raise HTTPException(403, {"code": "wai_permission_denied", "message": "Solo un administrador de este empleado puede consultar la configuracion."})
        if configuration_loader:
            await configuration_loader()
        error = adapter._configuration_error
        return {
            "configured": adapter._settings is not None,
            "configuration_code": None if adapter._settings is not None else (error.code if error else None),
            **configuration_metadata(),
            "tables": adapter.config.to_dict()["tables"],
            "denied": sorted(adapter.config.denied),
            "read_limit": adapter.config.read_limit,
            "can_configure": bool(can_configure and await can_configure(user.email)),
        }

    @router.put("/configuration")
    async def configure(body: ConfigurationInput, user=Depends(admin_dependency or authenticated_user_dependency)):
        if admin_dependency is None or configuration_saver is None:
            raise HTTPException(403, {"code": "wai_permission_denied", "message": "Solo un administrador de este empleado puede configurar esta conexion."})
        if configuration_loader:
            await configuration_loader()
        changed = await configuration_saver(body, user.email)
        return {"configured": True, "changed": changed, **configuration_metadata()}

    @router.post("/session")
    async def login(body: LoginInput, user=Depends(connected_user)):
        if adapter.config.auth_mode != "rls_personal":
            raise HTTPException(409, {"code": "supabase_auth_mode_mismatch", "message": "Este empleado usa modo service_role; no requiere inicio de sesion personal."})
        return await adapter.login(user.email, body.password.get_secret_value())

    @router.delete("/session")
    async def disconnect(user=Depends(connected_user)):
        if adapter.config.auth_mode != "rls_personal":
            return {"connected": True}
        return await adapter.disconnect(user.email)

    return router
