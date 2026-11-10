"""Identity integration for an active WAI employee chat, not a standalone login.

This file is already generic across every employee: it only resolves
"which authenticated person is behind this chat turn", using WAI's own
run tracking. No business logic lives here - do not add any.

A ``mode == "chat"`` run resolves identity from WAI's own authenticated
session (``request_meta_json.memory_user``), exactly as before. A
``mode != "chat"``, ``origen == "canal"`` run (a live channel
conversation - e.g. WhatsApp - not a scheduled/autonomous job) resolves
identity through a channel_binding.py verified binding instead - never
through the channel's own unverified contact-directory guess. Anything
else (scheduled/autonomous runs with no human on the other end) resolves
to no identity at all, same as before this file supported channels.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from contextvars import ContextVar


_channel_run_id: ContextVar[int | None] = ContextVar(
    "sgmcp_adapter_channel_run_id", default=None,
)


@contextmanager
def channel_run_context(run_id: int):
    """Vincula el identificador sintetico al run de esta invocacion."""
    marker = _channel_run_id.set(run_id)
    try:
        yield
    finally:
        _channel_run_id.reset(marker)


async def active_chat_email(run_token: str) -> "str | None":
    # Import lazily: this module is also importable by the adapter's offline tests.
    from sqlalchemy import select

    from app.app_database import app_session
    from app.app_models.runtime import AgentRun

    if not isinstance(run_token, str):
        return None
    try:
        if run_token.startswith("canal:"):
            run_id = _channel_run_id.get()
            if run_id is None:
                return None
            _, channel_id_text, solicitante = run_token.split(":", 2)
            channel_id = int(channel_id_text)
            if channel_id <= 0 or not solicitante:
                return None
            async with app_session() as session:
                active_run_id = (await session.execute(select(AgentRun.id).where(
                    AgentRun.id == run_id,
                    AgentRun.mode != "chat",
                    AgentRun.origen == "canal",
                    AgentRun.canal_id == channel_id,
                    AgentRun.solicitante == solicitante,
                    AgentRun.status == "RUNNING",
                    AgentRun.finished_at.is_(None),
                ))).scalar_one_or_none()
            if active_run_id is None:
                return None
            from .channel_binding import resolve_channel_principal

            return await resolve_channel_principal(channel_id, solicitante)
        if len(run_token) != 32:
            return None
        async with app_session() as session:
            rows = (await session.execute(select(
                AgentRun.mode, AgentRun.origen, AgentRun.canal_id,
                AgentRun.solicitante, AgentRun.request_meta_json,
            ).where(
                AgentRun.run_token == run_token,
                AgentRun.status == "RUNNING",
                AgentRun.finished_at.is_(None),
            ))).all()
        if len(rows) != 1:
            return None
        mode, origen, canal_id, solicitante, meta_json = rows[0]
        if mode == "chat":
            email = json.loads(meta_json or "{}").get("memory_user")
            return email.strip().lower() if isinstance(email, str) and email.strip() else None
        if origen == "canal" and canal_id is not None and solicitante:
            from .channel_binding import resolve_channel_principal

            return await resolve_channel_principal(canal_id, solicitante)
        return None
    except Exception:
        # No fallback to the employee owner or the autonomous runtime identity.
        return None
