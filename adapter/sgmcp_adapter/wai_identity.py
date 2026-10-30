"""Identity integration for an active WAI employee chat, not a standalone login.

This file is already generic across every employee: it only resolves
"which authenticated person is behind this chat turn", using WAI's own
run tracking. No business logic lives here - do not add any.
"""

from __future__ import annotations

import json


async def active_chat_email(run_token: str) -> "str | None":
    # Import lazily: this module is also importable by the adapter's offline tests.
    from sqlalchemy import select

    from app.app_database import app_session
    from app.app_models.runtime import AgentRun

    if not isinstance(run_token, str) or len(run_token) != 32:
        return None
    try:
        async with app_session() as session:
            rows = (await session.execute(select(AgentRun.request_meta_json).where(
                AgentRun.run_token == run_token,
                AgentRun.mode == "chat",
                AgentRun.status == "RUNNING",
                AgentRun.finished_at.is_(None),
            ))).scalars().all()
        if len(rows) != 1:
            return None
        email = json.loads(rows[0] or "{}").get("memory_user")
        return email.strip().lower() if isinstance(email, str) and email.strip() else None
    except Exception:
        # No fallback to the employee owner or the autonomous runtime identity.
        return None
