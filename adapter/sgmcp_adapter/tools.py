"""Wires the generic tools into WAI's MCP registry for one employee.

At most one external send per (run, step, tool, args) key, not "exactly
once": a crash between the durable claim and the upstream call still
leaves write_state="unknown", which is why core.Adapter never retries a
write it cannot prove was not sent.
"""

from __future__ import annotations

import json
from contextlib import AsyncExitStack

from fastapi import HTTPException

from .config import DENIABLE_TOOLS as _WRITE_TOOLS
from .core import Adapter, AdapterError, LoginRequired
from .public_url import public_url_metadata
from .tool_catalogue import tool_catalogue


_EXTERNAL_WRITE_PENDING = {"_sgmcp_adapter_external_write_pending_v1": True}
_UNCONFIRMED = {
    "code": "supabase_operation_failed",
    "message": "No se pudo confirmar la operacion. No reintentes; consulta el registro para verificar el estado.",
    "write_state": "unknown",
}


class _WritePhases:
    """Keep canonical SQL phases serialized, without holding the slot over I/O."""

    def __init__(self, stack):
        self.stack = stack
        self.session = None

    async def execute(self, statement):
        from app.app_database import app_session, app_write

        if statement.is_select:
            async with app_session() as session:
                return await session.execute(statement)
        if self.session is None:
            self.session = await self.stack.enter_async_context(app_write())
        return await self.session.execute(statement)

    async def commit(self):
        await self.session.commit()
        await self.stack.aclose()
        self.session = None


def register_tools(adapter: Adapter, *, configuration_loader=None, diagnostic_authorizer=None) -> None:
    """Call once per employee process, after the connection config is
    known. Re-call after an administrator changes the config so the
    catalogue (table/column allowlist) picked up by the model stays in
    sync - see integration.mount().

    ``diagnostic_authorizer(email) -> bool``, if given, is what lets an
    admin's failed tool call come back with the safe, value-free
    ``response_shape`` a read failure may carry (see response_shape.py) -
    the same thing routes.py already exposes for the HTTP admin endpoints.
    Without it, a bad response from sgmcp is a dead end that only shows up
    by digging through tool_use_log by hand.

    Registers at LEVEL_AGENT, not LEVEL_HUMAN - see the comment at the
    TOOL_MIN_ROLE assignment below for why. identity/authorization for
    every call is enforced inside generic_operation() itself, not by the
    host's own role gate."""
    from app import mcp_server
    from app.mcp_actor import LEVEL_AGENT, TOOL_MIN_ROLE

    async def generic_operation(call):
        from sqlalchemy import select

        from app.app_database import app_session
        from app.app_models.runtime import AgentRun

        async with app_session() as session:
            row = (await session.execute(select(
                AgentRun.run_token, AgentRun.mode, AgentRun.origen,
                AgentRun.canal_id, AgentRun.solicitante,
            ).where(
                AgentRun.id == call.run_id,
                AgentRun.status == "RUNNING",
                AgentRun.finished_at.is_(None),
            ))).one_or_none()
        if row is None:
            raise HTTPException(401, "Se requiere un turno de chat activo.")
        token, mode, origen, canal_id, solicitante = row
        if mode != "chat":
            # A live channel conversation (e.g. WhatsApp) - not a scheduled/
            # autonomous job, which has no human on the other end to verify
            # anything for - can use these tools only with a verified
            # channel_binding, never through the channel's own unverified
            # contact-directory guess at an email.
            if origen != "canal" or canal_id is None or not solicitante:
                raise HTTPException(401, "Se requiere un turno de chat activo.")
            from .channel_binding import resolve_channel_principal, sign_verification_token

            if await resolve_channel_principal(canal_id, solicitante) is None:
                if configuration_loader:
                    await configuration_loader()
                base_url = public_url_metadata(adapter.public_base_url)["effective_public_base_url"]
                if base_url is None:
                    raise HTTPException(503, {
                        "code": "supabase_public_base_url_required",
                        "message": "Un administrador debe configurar la URL publica HTTPS del empleado para verificar este canal.",
                    })
                link_token = sign_verification_token(canal_id, solicitante)
                link = base_url + "/supabase-canal-verificar?token=" + link_token
                raise HTTPException(401, {
                    "code": "supabase_channel_not_verified",
                    "message": (
                        "*Vincula tu cuenta para continuar*\n\n"
                        "Para atender tu consulta, necesitas vincular este WhatsApp con tu cuenta de Supabase.\n\n"
                        "*Abre este enlace:*\n"
                        + link + "\n\n"
                        "Si aparece el acceso a WAI, inicia sesión. Después introduce tu contraseña de Supabase "
                        "y pulsa «Verificar y vincular».\n\n"
                        "*El enlace vence en 30 minutos.*\n"
                        "No compartas tu contraseña por WhatsApp.\n\n"
                        "Cuando termines, vuelve aquí y repite tu consulta."
                    ),
                })
        try:
            if configuration_loader:
                await configuration_loader()
            personal_channel = (
                mode != "chat"
                and adapter.config.auth_mode == "rls_personal"
            )
            if personal_channel:
                if call.name in _WRITE_TOOLS:
                    raise HTTPException(403, "Las escrituras CRM requieren el chat WAI autenticado.")
                if not token:
                    token = f"canal:{canal_id}:{solicitante}"
            if call.name not in _WRITE_TOOLS:
                if personal_channel:
                    from .wai_identity import channel_run_context
                    with channel_run_context(call.run_id):
                        return await adapter.execute(token, call.name, call.arguments)
                return await adapter.execute(token, call.name, call.arguments)

            context = await adapter.preflight_write(token, call.name, call.arguments)
            scoped_args = {"arguments": call.arguments, "scope": [context.project_url, context.principal]}

            from sqlalchemy import delete, update

            from app.app_models.runtime import ToolCallLog
            from app.autonomous.tool_idempotency import _hash, _replay_or_execute

            # The run above is verified as chat; do not trust a caller-supplied mode.
            key = f"chat:{call.run_id}:{call.step_idx}:{call.name}:{_hash(scoped_args)}"
            async with AsyncExitStack() as stack:
                session = _WritePhases(stack)

                async def do_work():
                    await adapter.validate_write_context(token, context)
                    # Durable BEFORE upstream: even a crash or the helper's pending
                    # timeout must not send this external write a second time.
                    claimed = await session.execute(update(ToolCallLog).where(
                        ToolCallLog.idempotency_key == key,
                        ToolCallLog.result_json.is_(None),
                    ).values(result_json=json.dumps(_EXTERNAL_WRITE_PENDING)))
                    await session.commit()
                    if claimed.rowcount != 1:
                        raise HTTPException(409, dict(_UNCONFIRMED))
                    try:
                        return await adapter.execute(
                            token, call.name, call.arguments, write_context=context,
                        )
                    except AdapterError as error:
                        if error.write_state == "not_sent":
                            # Only proven pre-send failures can release the durable barrier.
                            await session.execute(delete(ToolCallLog).where(
                                ToolCallLog.idempotency_key == key,
                                ToolCallLog.result_hash.is_(None),
                                ToolCallLog.result_json == json.dumps(_EXTERNAL_WRITE_PENDING),
                            ))
                            await session.commit()
                        raise
                    except Exception:
                        raise HTTPException(409, dict(_UNCONFIRMED)) from None

                result, _ = await _replay_or_execute(
                    session, run_id=call.run_id, step_idx=call.step_idx,
                    tool_name=call.name, args=scoped_args,
                    do_work=do_work, mode="chat",
                )
            if result == _EXTERNAL_WRITE_PENDING:
                raise HTTPException(409, dict(_UNCONFIRMED))
            return result
        except AdapterError as error:
            detail = {"code": error.code, "message": str(error)}
            if call.name in _WRITE_TOOLS:
                detail["write_state"] = error.write_state
            shape = getattr(error, "response_shape", None)
            if shape is not None and diagnostic_authorizer is not None:
                try:
                    from .wai_identity import active_chat_email

                    email = await active_chat_email(token)
                    allowed = bool(email) and await diagnostic_authorizer(email)
                except Exception:
                    allowed = False
                if allowed:
                    detail["response_shape"] = shape
            raise HTTPException(
                getattr(error, "status_code", 401 if isinstance(error, LoginRequired) else 409),
                detail,
            ) from None

    for descriptor in tool_catalogue(adapter.config):
        name = descriptor["name"]

        async def described_operation(call):
            return await generic_operation(call)

        described_operation.__doc__ = descriptor["description"]
        mcp_server.TOOLS[name] = described_operation
        mcp_server.MCP_TOOL_SCHEMAS[name] = descriptor["inputSchema"]
        # LEVEL_AGENT, not LEVEL_HUMAN: the host's own gate_error() only ever
        # treats mode=="chat" as human (Actor.is_human), with no hook a
        # registered tool can use to say "this non-chat actor has a verified
        # identity too" - its one escape hatch (ADMIN_READ_TOOLS) is a closed
        # list of 8 host tool names requiring directory-admin role, unrelated
        # to channel_binding. generic_operation()'s own gate below is what
        # actually enforces identity now (chat, or a canal run with a
        # verified binding - anything else, including a scheduled/autonomous
        # run or an unbound canal run, is rejected there) - this trades the
        # host's independent check for our own, already covered by tests
        # that confirm a scheduled run is rejected even with a valid binding
        # for the same channel identity.
        TOOL_MIN_ROLE[name] = LEVEL_AGENT
