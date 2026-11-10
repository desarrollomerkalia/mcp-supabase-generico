"""Verified phone-to-Supabase-identity bindings for channel (WhatsApp,
etc.) access to rls_personal data.

Stored the same way as the main connection config: an encrypted blob in
the host's own ``AppSetting`` table (``app.channels.encryption``) - no new
table, no migration, so installing/updating this skill never touches the
host's schema.

A binding is only ever written by the caller of ``set_binding`` AFTER a
real Supabase login has succeeded (see routes.py's channel-session
endpoint) - this module never authenticates anything itself, it only
signs/verifies the short-lived verification token and stores the result.
The verification token reuses the same encrypt_blob/decrypt_blob
primitive as storage: no separate signing secret to provision or rotate.
"""

from __future__ import annotations

import time


_SETTINGS_KEY = "sgmcp_adapter_channel_bindings"
_TOKEN_SCHEMA = "sgmcp_channel_verify_v1"


def _canon_phone(value: str) -> str:
    return "".join(ch for ch in str(value) if ch.isdigit())


def _binding_key(channel_id: int, external_identity: str) -> str:
    return "%d:%s" % (int(channel_id), _canon_phone(external_identity))


def sign_verification_token(channel_id: int, external_identity: str, *, ttl_s: int = 1800) -> str:
    """Build the opaque token embedded in the WhatsApp verification link.
    Encrypted, not just encoded - a tampered or foreign blob fails to
    decrypt rather than silently parsing into a different channel/phone."""
    from app.channels.encryption import encrypt_blob

    if not isinstance(channel_id, int) or isinstance(channel_id, bool):
        raise ValueError("channel_id must be an int")
    identity = _canon_phone(external_identity)
    if not identity:
        raise ValueError("external_identity must contain at least one digit")
    payload = {
        "schema": _TOKEN_SCHEMA,
        "channel_id": channel_id,
        "external_identity": identity,
        "exp": time.time() + max(60, int(ttl_s)),
    }
    return encrypt_blob(payload)


def verify_token(token: str) -> "tuple[int, str] | None":
    """Returns (channel_id, external_identity) if the token is genuine,
    unexpired, and well-formed - None for anything else. Never raises."""
    from app.channels.encryption import decrypt_blob

    if not isinstance(token, str) or not token:
        return None
    try:
        payload = decrypt_blob(token)
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("schema") != _TOKEN_SCHEMA:
        return None
    channel_id = payload.get("channel_id")
    external_identity = payload.get("external_identity")
    exp = payload.get("exp")
    if not isinstance(channel_id, int) or isinstance(channel_id, bool):
        return None
    if not isinstance(external_identity, str) or not external_identity:
        return None
    if not isinstance(exp, (int, float)) or time.time() > exp:
        return None
    return channel_id, external_identity


async def _load_bindings(session) -> dict:
    from app.app_models.runtime import AppSetting
    from app.channels.encryption import decrypt_blob

    row = await session.get(AppSetting, _SETTINGS_KEY)
    if row is None:
        return {}
    try:
        bindings = decrypt_blob(row.value)
    except Exception:
        return {}
    return bindings if isinstance(bindings, dict) else {}


async def get_bindings() -> dict:
    """Read-only snapshot, for the admin UI's list/revoke screen."""
    from app.app_database import app_session

    async with app_session() as session:
        return await _load_bindings(session)


async def set_binding(channel_id: int, external_identity: str, principal: str, *, actor_email: "str | None" = None) -> None:
    """Called only after a real Supabase login succeeded for ``principal``
    - see routes.py. Overwrites any prior binding for the same
    (channel_id, external_identity), un-revoking it if it had been."""
    from app.app_database import app_write
    from app.app_models.runtime import AppSetting
    from app.channels.encryption import encrypt_blob

    key = _binding_key(channel_id, external_identity)
    async with app_write() as session:
        row = await session.get(AppSetting, _SETTINGS_KEY)
        bindings = await _load_bindings(session)
        bindings[key] = {
            "channel_id": int(channel_id),
            "external_identity": _canon_phone(external_identity),
            "principal": principal,
            "bound_at": time.time(),
            "revoked_at": None,
        }
        encrypted = encrypt_blob(bindings)
        if row is None:
            session.add(AppSetting(key=_SETTINGS_KEY, value=encrypted, updated_by=actor_email or "system"))
        else:
            row.value = encrypted
            if actor_email is not None:
                row.updated_by = actor_email
        await session.commit()


async def revoke_binding(channel_id: int, external_identity: str, *, actor_email: "str | None" = None) -> bool:
    """Returns False if there was nothing to revoke (no error either way -
    revoking twice, or something never bound, is not exceptional)."""
    from app.app_models.runtime import AppSetting
    from app.app_database import app_write
    from app.channels.encryption import encrypt_blob

    key = _binding_key(channel_id, external_identity)
    async with app_write() as session:
        row = await session.get(AppSetting, _SETTINGS_KEY)
        bindings = await _load_bindings(session)
        entry = bindings.get(key)
        if entry is None or entry.get("revoked_at") is not None:
            return False
        entry["revoked_at"] = time.time()
        encrypted = encrypt_blob(bindings)
        if row is None:
            session.add(AppSetting(key=_SETTINGS_KEY, value=encrypted, updated_by=actor_email or "system"))
        else:
            row.value = encrypted
            if actor_email is not None:
                row.updated_by = actor_email
        await session.commit()
        return True


async def resolve_channel_principal(channel_id: int, external_identity: str) -> "str | None":
    """Looks up a non-revoked binding. Used by the (future, Phase 2)
    tools.py gate for channel-originated runs."""
    bindings = await get_bindings()
    entry = bindings.get(_binding_key(channel_id, external_identity))
    if not entry or entry.get("revoked_at") is not None:
        return None
    principal = entry.get("principal")
    return principal if isinstance(principal, str) and principal else None
