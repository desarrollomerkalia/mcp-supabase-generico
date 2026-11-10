"""Integracion local: wrapper, identidad, bindings y core reales, sin red.

SQLite y modelos son los de conftest. Solo Auth HTTP y el transporte sgmcp
devuelven datos simulados; no se sustituye ningun resolutor de identidad.
Las pruebas de limpieza del ContextVar interrumpen execute deliberadamente.
No son E2E de autenticacion WAI, recepcion de WhatsApp ni politicas Supabase.
"""

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select, update

from app import mcp_actor, mcp_server, users_directory
from app.app_database import app_session, app_write
from app.app_models.runtime import AgentRun, AppSetting, ToolCallLog
from app.channels import encryption
from sgmcp_adapter.channel_binding import revoke_binding, set_binding, verify_token
from sgmcp_adapter.config import ConnectionConfig, DENIABLE_TOOLS
from sgmcp_adapter.core import Adapter, Settings, WriteContext
from sgmcp_adapter.tools import register_tools
from sgmcp_adapter.wai_identity import (
    _channel_run_id, active_chat_email, channel_run_context,
)


EMAIL = "persona@example.invalid"
DIRECTORY_EMAIL = "directorio@example.invalid"
CID = 7
SENDER = "+15550001001"
TOKEN = "a" * 32  # Exclusivamente local; no es un JWT ni una credencial real.
URL = "https://abcdefghijklmnopqrst.supabase.co"
ANON = "sb_publishable_PRUEBA_solo_local"
SERVICE = "sb_secret_PRUEBA_solo_local"
ACCESS = "PRUEBA-access-token"
TABLES = {"notas": {
    "columns": {"id": {"type": "string"}, "texto": {"type": "string"}},
    "writable": ["texto"], "filterable": ["id"],
}}
READ = {"tabla": "notas"}
WRITE = {"tabla": "notas", "datos": {"texto": "PRUEBA-nota"}}


@pytest.fixture(autouse=True)
def local_support(monkeypatch):
    monkeypatch.setattr(mcp_server, "TOOLS", dict(mcp_server.TOOLS))
    monkeypatch.setattr(mcp_server, "MCP_TOOL_SCHEMAS", dict(mcp_server.MCP_TOOL_SCHEMAS))
    roles = dict(mcp_actor.TOOL_MIN_ROLE)
    monkeypatch.setattr(mcp_actor, "TOOL_MIN_ROLE", roles)
    monkeypatch.setattr(mcp_server, "TOOL_MIN_ROLE", roles)
    monkeypatch.setattr(encryption, "CHANNEL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(encryption, "_DERIVED_KEY", None)

    async def no_network(*args, **kwargs):
        raise AssertionError("PRUEBA: HTTP externo prohibido")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)


def config(mode):
    return ConnectionConfig.from_dict({"auth_mode": mode, "tables": TABLES})


@pytest.fixture
def make_adapter(monkeypatch):
    def make(mode="rls_personal"):
        auth_requests = []

        def auth(request):
            auth_requests.append(request)
            assert request.url.host == "abcdefghijklmnopqrst.supabase.co"
            assert request.headers["apikey"] == ANON
            if request.method == "POST":
                assert request.url.path == "/auth/" + "v1/token"
                assert dict(request.url.params) == {"grant_type": "password"}
                assert json.loads(request.content) == {
                    "email": EMAIL, "password": "PRUEBA-password",
                }
                return httpx.Response(200, json={
                    "access_token": ACCESS, "refresh_token": "PRUEBA-refresh",
                    "expires_in": 3600,
                })
            assert request.method == "GET"
            assert request.url.path == "/auth/" + "v1/user"
            assert request.headers["authorization"] == "Bearer " + ACCESS
            return httpx.Response(200, json={"id": "PRUEBA-subject", "email": EMAIL})

        upstream = SimpleNamespace(call=AsyncMock(return_value={
            "data": [{"id": "PRUEBA-1", "texto": "PRUEBA-nota"}], "total": 1,
        }))
        adapter = Adapter(
            config(mode), Settings(URL, anon_key=ANON, service_role_key=SERVICE),
            lambda: httpx.AsyncClient(transport=httpx.MockTransport(auth), trust_env=False),
            upstream, active_chat_email,
        )
        # Espias: delegan en core e identidad reales, no conceden permisos.
        monkeypatch.setattr(adapter, "execute", AsyncMock(wraps=adapter.execute))
        monkeypatch.setattr(adapter, "resolve_actor", AsyncMock(wraps=active_chat_email))
        register_tools(adapter)
        return SimpleNamespace(adapter=adapter, upstream=upstream, auth_requests=auth_requests)

    return make


async def new_run(**overrides):
    values = {
        "idempotency_key": "PRUEBA-" + uuid4().hex,
        "prompt": "PRUEBA-local-sin-conversacion-real", "status": "RUNNING",
        "mode": "autonomous", "origen": "canal", "canal_id": CID,
        "solicitante": SENDER, "run_token": None,
        "request_meta_json": json.dumps({"memory_user": None}),
    }
    values.update(overrides)
    async with app_write() as session:
        row = AgentRun(**values)
        session.add(row)
        await session.commit()
        return row.id


def call_for(run_id, name="leer_registros", arguments=None):
    return SimpleNamespace(
        run_id=run_id, name=name, step_idx=0,
        arguments=dict(READ if arguments is None else arguments),
    )


async def invoke(run_id, name="leer_registros", arguments=None):
    return await mcp_server.TOOLS[name](call_for(run_id, name, arguments))


async def bind():
    await set_binding(CID, SENDER, EMAIL, actor_email="admin@example.invalid")


@pytest.mark.asyncio
@pytest.mark.parametrize("token,sender", [
    (None, SENDER), (TOKEN, SENDER), (None, "whatsapp:" + SENDER),
])
async def test_channel_read_real_wrapper_core_and_encrypted_binding(make_adapter, token, sender):
    env = make_adapter()
    rid = await new_run(run_token=token, solicitante=sender,
                        request_meta_json=json.dumps({"memory_user": DIRECTORY_EMAIL}))
    await bind()
    assert await env.adapter.login(EMAIL, "PRUEBA-password") == {"connected": True, "email": EMAIL}
    async with app_session() as session:
        row = await session.get(AppSetting, "sgmcp_adapter_channel_bindings")
        assert row is not None
        assert EMAIL not in row.value and SENDER not in row.value
        assert row.updated_by == "admin@example.invalid"
        assert encryption.decrypt_blob(row.value)["7:15550001001"]["principal"] == EMAIL

    result = await invoke(rid)
    assert result["registros"] == [{"id": "PRUEBA-1", "texto": "PRUEBA-nota"}]
    assert result["completa"] is True and result["total_visible"] == 1
    env.adapter.execute.assert_awaited_once_with(token or f"canal:{CID}:{sender}", "leer_registros", READ)
    assert env.adapter.resolve_actor.await_count >= 3
    env.upstream.call.assert_awaited_once_with("supabase_leer", {
        "tabla": "notas", "columnas": "id,texto", "filtros": {},
        "limite": 50, "pagina": 1, "orden_asc": True,
        "supabaseUrl": URL, "supabaseKey": ANON, "userToken": ACCESS,
    })
    assert len(env.auth_requests) == 2
    assert _channel_run_id.get() is None
    assert await active_chat_email(f"canal:{CID}:{sender}") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("memory_user", [None, DIRECTORY_EMAIL])
async def test_host_jsonrpc_real_run_resolver_role_gate_and_context_stripping(
    make_adapter, monkeypatch, memory_user,
):
    env = make_adapter()
    rid = await new_run(request_meta_json=json.dumps({"memory_user": memory_user}))
    await bind()
    await env.adapter.login(EMAIL, "PRUEBA-password")
    role = AsyncMock(return_value="admin")
    monkeypatch.setattr(users_directory, "resolve_role", role)
    args = {"run_id": rid, **READ}
    reply = await mcp_server._mcp_dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "leer_registros", "arguments": args},
    })
    assert reply["result"]["isError"] is False, reply
    assert json.loads(reply["result"]["content"][0]["text"])["total_visible"] == 1
    assert mcp_actor.TOOL_MIN_ROLE["leer_registros"] == mcp_actor.LEVEL_AGENT
    assert env.adapter.execute.await_args.args == (f"canal:{CID}:{SENDER}", "leer_registros", READ)
    assert args == {"run_id": rid, **READ}
    if memory_user is None:
        role.assert_not_awaited()
    else:
        role.assert_awaited_once_with(DIRECTORY_EMAIL)
    env.upstream.call.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["rls_personal", "service_role"])
async def test_unbound_channel_denied_before_execute(make_adapter, mode, monkeypatch):
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://employee.example.invalid")
    env = make_adapter(mode)
    rid = await new_run(run_token=TOKEN)
    with pytest.raises(HTTPException) as error:
        await invoke(rid)
    assert error.value.status_code == 401
    assert error.value.detail["code"] == "supabase_channel_not_verified"
    env.adapter.execute.assert_not_awaited()
    env.upstream.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("public_origin", [
    "https://employee.example.invalid",
    "https://" + ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 61]) + ":65535",
])
async def test_verification_message_exact_through_mcp_and_whatsapp_transport(
    make_adapter, monkeypatch, public_origin,
):
    from urllib.parse import parse_qs, urlsplit

    from app.channels.base import Message, Reply
    from app.channels.whatsapp_twilio import WA_BODY_MAX, WhatsAppTwilioAdapter

    monkeypatch.setenv("APP_PUBLIC_BASE_URL", public_origin)
    env = make_adapter()
    rid = await new_run()
    reply = await mcp_server._mcp_dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "leer_registros", "arguments": {"run_id": rid, **READ}},
    })
    assert reply["result"]["isError"] is True
    error = json.loads(reply["result"]["content"][0]["text"])["error"]
    assert error["code"] == "supabase_channel_not_verified"
    message = error["message"]
    link, = [line for line in message.splitlines() if line.startswith("https://")]
    parsed = urlsplit(link)
    assert link.startswith(public_origin + "/supabase-canal-verificar?token=")
    assert not parsed.fragment
    assert verify_token(parse_qs(parsed.query)["token"][0]) == (CID, SENDER.lstrip("+"))
    assert message == (
        "*Vincula tu cuenta para continuar*\n\n"
        "Para atender tu consulta, necesitas vincular este WhatsApp con tu cuenta de Supabase.\n\n"
        "*Abre este enlace:*\n" + link + "\n\n"
        "Si aparece el acceso a WAI, inicia sesión. Después introduce tu contraseña de Supabase "
        "y pulsa «Verificar y vincular».\n\n"
        "*El enlace vence en 30 minutos.*\n"
        "No compartas tu contraseña por WhatsApp.\n\n"
        "Cuando termines, vuelve aquí y repite tu consulta."
    )
    assert len(message) <= WA_BODY_MAX
    # El modelo no participa: comprueba el transporte cuando reply.body copia message.
    transport = WhatsAppTwilioAdapter.__new__(WhatsAppTwilioAdapter)
    monkeypatch.setattr(transport, "_config", lambda: {"whatsapp_number": "+15550001002"})
    request = AsyncMock(return_value={"sid": "SM-test-only"})
    monkeypatch.setattr(transport, "_request", request)
    await transport.send_reply(Message(provider_message_id="SM-inbound-test-only", sender=SENDER), Reply(body=message))
    assert request.await_args.kwargs["data"]["Body"] == message
    env.adapter.execute.assert_not_awaited()
    env.upstream.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    None, {"status": "COMPLETED"}, {"status": "FAILED"}, {"status": "CANCELLED"},
    {"finished_at": datetime(2026, 1, 1)},
    {"origen": "programado"}, {"origen": "automatizacion"},
    {"canal_id": None}, {"solicitante": None},
])
async def test_invalid_or_scheduled_run_rejected_even_with_binding(make_adapter, overrides):
    env = make_adapter()
    await bind()
    rid = 999999 if overrides is None else await new_run(**overrides)
    with pytest.raises(HTTPException) as error:
        await invoke(rid)
    assert error.value.status_code == 401
    env.adapter.execute.assert_not_awaited()
    env.upstream.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["chat_token", "chat_id_only", "wrong_token_channel_id"])
async def test_chat_jsonrpc_token_resolution_and_read_identity(make_adapter, monkeypatch, case):
    env = make_adapter()
    rid = await new_run(
        mode="chat", origen="chat", canal_id=None, solicitante=EMAIL, run_token=TOKEN,
        request_meta_json=json.dumps({"memory_user": "  Persona@EXAMPLE.INVALID  "}),
    )
    await env.adapter.login(EMAIL, "PRUEBA-password")
    assert await active_chat_email(TOKEN) == EMAIL
    role = AsyncMock(return_value="member")
    monkeypatch.setattr(users_directory, "resolve_role", role)
    monkeypatch.setattr(users_directory, "can_approve", AsyncMock(return_value=False))
    handler = AsyncMock(wraps=mcp_server.TOOLS["leer_registros"])
    monkeypatch.setitem(mcp_server.TOOLS, "leer_registros", handler)
    if case == "wrong_token_channel_id":
        channel_rid = await new_run()
        await bind()
        args = {"run_token": "b" * 32, "run_id": channel_rid, **READ}
    else:
        args = {**({"run_token": TOKEN} if case == "chat_token" else {"run_id": rid}), **READ}
    reply = await mcp_server._mcp_dispatch({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "leer_registros", "arguments": args},
    })
    payload = json.loads(reply["result"]["content"][0]["text"])
    if case == "chat_token":
        assert reply["result"]["isError"] is False, reply
        assert payload["registros"] == [{"id": "PRUEBA-1", "texto": "PRUEBA-nota"}]
        assert payload["total_visible"] == 1
        handler.assert_awaited_once()
        call = handler.await_args.args[0]
        assert call.run_id == rid and call.arguments == READ
        env.adapter.execute.assert_awaited_once_with(TOKEN, "leer_registros", READ)
        assert all(entry.args == (TOKEN,) for entry in env.adapter.resolve_actor.await_args_list)
        env.upstream.call.assert_awaited_once()
    else:
        assert reply["result"]["isError"] is True, reply
        # Rechazo del host al resolver el turno, no un fallo de Supabase/RLS.
        assert payload == {"ok": False, "error": (
            "mutating tools require a valid run_token from "
            "the [INTERNO] line \u2014 pass run_token on every call"
        )}
        handler.assert_not_awaited()
        env.adapter.execute.assert_not_awaited()
        env.upstream.call.assert_not_awaited()
        role.assert_not_awaited()
    assert _channel_run_id.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(DENIABLE_TOOLS))
async def test_personal_channel_writes_are_403_before_preflight(make_adapter, monkeypatch, name):
    env = make_adapter()
    rid = await new_run()
    await bind()
    preflight = AsyncMock(wraps=env.adapter.preflight_write)
    monkeypatch.setattr(env.adapter, "preflight_write", preflight)
    with pytest.raises(HTTPException) as error:
        await invoke(rid, name, WRITE)
    assert error.value.status_code == 403
    preflight.assert_not_awaited()
    env.adapter.execute.assert_not_awaited()
    env.upstream.call.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("surface,mode", [
    ("chat", "rls_personal"), ("chat", "service_role"), ("canal", "service_role"),
])
async def test_existing_write_path_real_preflight_core_and_sqlite_replay(
    make_adapter, surface, mode,
):
    env = make_adapter(mode)
    rid = await new_run(
        mode="chat" if surface == "chat" else "autonomous", origen=surface,
        run_token=TOKEN, request_meta_json=json.dumps({"memory_user": EMAIL}),
    )
    if surface == "canal":
        await bind()
    if mode == "rls_personal":
        await env.adapter.login(EMAIL, "PRUEBA-password")
    result = await invoke(rid, "insertar_registros", WRITE)
    assert result["resultado"]["data"][0]["id"] == "PRUEBA-1"
    assert await invoke(rid, "insertar_registros", WRITE) == result
    env.adapter.execute.assert_awaited_once()
    args, kwargs = env.adapter.execute.await_args
    assert args == (TOKEN, "insertar_registros", WRITE)
    assert isinstance(kwargs["write_context"], WriteContext)
    assert kwargs["write_context"].principal == (EMAIL if mode == "rls_personal" else "service_role")
    env.upstream.call.assert_awaited_once()
    tool, payload = env.upstream.call.await_args.args
    assert tool == "supabase_insertar"
    assert payload["supabaseKey"] == (ANON if mode == "rls_personal" else SERVICE)
    assert payload.get("userToken") == (ACCESS if mode == "rls_personal" else None)
    async with app_session() as session:
        log = (await session.execute(select(ToolCallLog).where(ToolCallLog.run_id == rid))).scalar_one()
        assert log.result_hash and json.loads(log.result_json) == result


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [TOKEN, None])
async def test_service_channel_preserves_token_policy_without_personal_session(
    make_adapter, monkeypatch, token,
):
    env = make_adapter("service_role")
    rid = await new_run(run_token=token)
    await bind()
    personal_session = AsyncMock(side_effect=AssertionError("PRUEBA: no sesion personal en service_role"))
    monkeypatch.setattr(env.adapter, "_session", personal_session)
    if token is None:
        with pytest.raises(HTTPException) as error:
            await invoke(rid)
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "supabase_session_required"
        env.upstream.call.assert_not_awaited()
    else:
        assert (await invoke(rid))["total_visible"] == 1
        env.upstream.call.assert_awaited_once()
        assert "userToken" not in env.upstream.call.await_args.args[1]
    env.adapter.execute.assert_awaited_once_with(token, "leer_registros", READ)
    personal_session.assert_not_awaited()
    assert not env.auth_requests
    assert _channel_run_id.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("initial,final,name", [
    ("rls_personal", "service_role", "leer_registros"),
    ("service_role", "rls_personal", "leer_registros"),
    ("rls_personal", "service_role", "insertar_registros"),
    ("service_role", "rls_personal", "insertar_registros"),
])
async def test_configuration_loader_gates_using_loaded_mode(make_adapter, monkeypatch, initial, final, name):
    env = make_adapter(initial)
    token = TOKEN if final == "service_role" else None
    rid = await new_run(run_token=token)
    await bind()

    async def load():
        env.adapter.configure(config(final), env.adapter.settings)
        if final == "rls_personal":
            await env.adapter.login(EMAIL, "PRUEBA-password")

    loader = AsyncMock(side_effect=load)
    observed_contexts = []
    real_execute = env.adapter.execute

    async def observe(*args, **kwargs):
        observed_contexts.append(_channel_run_id.get())
        return await real_execute(*args, **kwargs)

    execute = AsyncMock(side_effect=observe)
    monkeypatch.setattr(env.adapter, "execute", execute)
    register_tools(env.adapter, configuration_loader=loader)
    arguments = READ if name == "leer_registros" else WRITE
    if final == "rls_personal" and name != "leer_registros":
        with pytest.raises(HTTPException) as error:
            await invoke(rid, name, arguments)
        assert error.value.status_code == 403
        execute.assert_not_awaited()
        env.upstream.call.assert_not_awaited()
    else:
        await invoke(rid, name, arguments)
        execute.assert_awaited_once()
        expected_token = TOKEN if final == "service_role" else f"canal:{CID}:{SENDER}"
        assert execute.await_args.args == (expected_token, name, arguments)
        assert observed_contexts == ([None] if final == "service_role" else [rid])
        if name != "leer_registros":
            assert execute.await_args.kwargs["write_context"].principal == "service_role"
        env.upstream.call.assert_awaited_once()
        payload = env.upstream.call.await_args.args[1]
        assert payload["supabaseKey"] == (SERVICE if final == "service_role" else ANON)
        assert payload.get("userToken") == (None if final == "service_role" else ACCESS)
    loader.assert_awaited_once_with()
    assert env.adapter.config.auth_mode == final
    assert _channel_run_id.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("synthetic", [
    f"canal:{CID + 1}:{SENDER}", f"canal:{CID}:+15550001999",
    f"canal:{CID}:15550001001",  # Mismo binding canonico, distinto remitente CRUDO.
    f"canal:{CID}:whatsapp:{SENDER}",
    "canal:7", "canal:no-entero:15550001001", "canal:0:15550001001",
    "canal:-1:15550001001", "canal:7:", "canal::", "corto", None,
])
async def test_identity_rejects_tampered_raw_sender_channel_and_malformed_tokens(synthetic):
    rid = await new_run()
    await bind()
    with channel_run_context(rid):
        assert await active_chat_email(synthetic) is None
    assert _channel_run_id.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides", [
    None, {"origen": "programado"}, {"origen": "automatizacion"},
    {"mode": "chat"}, {"status": "COMPLETED"},
    {"finished_at": datetime(2026, 1, 1)},
    {"canal_id": CID + 1}, {"solicitante": "+15550001999"},
])
async def test_synthetic_identity_requires_exact_active_channel_run(overrides):
    await bind()
    valid = await new_run()
    with channel_run_context(valid):
        assert await active_chat_email(f"canal:{CID}:{SENDER}") == EMAIL
    rid = 999999 if overrides is None else await new_run(**overrides)
    with channel_run_context(rid):
        assert await active_chat_email(f"canal:{CID}:{SENDER}") is None
    assert _channel_run_id.get() is None


@pytest.mark.asyncio
async def test_binding_revocation_invalidates_synthetic_and_real_token_identity(make_adapter, monkeypatch):
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://employee.example.invalid")
    env = make_adapter()
    rid = await new_run(run_token=TOKEN)
    await bind()
    with channel_run_context(rid):
        assert await active_chat_email(f"canal:{CID}:{SENDER}") == EMAIL
        assert await active_chat_email(TOKEN) == EMAIL
        assert await revoke_binding(CID, SENDER, actor_email="admin@example.invalid") is True
        assert await active_chat_email(f"canal:{CID}:{SENDER}") is None
        assert await active_chat_email(TOKEN) is None
    with pytest.raises(HTTPException) as error:
        await invoke(rid)
    assert error.value.status_code == 401
    env.adapter.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("same_sender", [False, True])
async def test_concurrent_task_contexts_pin_exact_run_not_latest_sender(same_sender):
    first = await new_run()
    other_cid, other_sender = (CID, SENDER) if same_sender else (CID + 1, "+15550001002")
    second = await new_run(canal_id=other_cid, solicitante=other_sender)
    await bind()
    await set_binding(other_cid, other_sender, EMAIL if same_sender else "otra@example.invalid")
    ready = [asyncio.Event(), asyncio.Event()]
    release = asyncio.Event()
    first_done = asyncio.Event()

    # El segundo permanece RUNNING mientras se verifica el cierre del primero.
    async def first_worker():
        try:
            with channel_run_context(first):
                ready[0].set()
                await release.wait()
                assert _channel_run_id.get() == first
                assert await active_chat_email(f"canal:{CID}:{SENDER}") == EMAIL
                async with app_write() as session:
                    await session.execute(update(AgentRun).where(AgentRun.id == first).values(status="COMPLETED"))
                    await session.commit()
                assert await active_chat_email(f"canal:{CID}:{SENDER}") is None
            assert _channel_run_id.get() is None
        finally:
            first_done.set()

    async def second_worker():
        with channel_run_context(second):
            ready[1].set()
            await release.wait()
            await first_done.wait()
            assert _channel_run_id.get() == second
            assert await active_chat_email(f"canal:{other_cid}:{other_sender}") == (
                EMAIL if same_sender else "otra@example.invalid"
            )
        assert _channel_run_id.get() is None

    tasks = [asyncio.create_task(first_worker()), asyncio.create_task(second_worker())]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in ready)), timeout=5)
        assert _channel_run_id.get() is None
        release.set()
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=5)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert await active_chat_email(f"canal:{CID}:{SENDER}") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["exception", "cancellation"])
async def test_wrapper_context_restored_after_exception_or_cancellation(make_adapter, monkeypatch, ending):
    env = make_adapter()
    rid = await new_run()
    await bind()
    entered = asyncio.Event()
    blocker = asyncio.Event()
    outer = 999999

    async def interrupted(token, name, arguments):
        assert _channel_run_id.get() == rid
        assert await active_chat_email(token) == EMAIL
        entered.set()
        if ending == "exception":
            raise RuntimeError("PRUEBA-interrupcion")
        await blocker.wait()

    # Solo esta prueba interrumpe execute: mide finally/reset, no autorizacion core.
    monkeypatch.setattr(env.adapter, "execute", AsyncMock(side_effect=interrupted))

    async def worker():
        with channel_run_context(outer):
            try:
                await invoke(rid)
            finally:
                assert _channel_run_id.get() == outer
                assert await active_chat_email(f"canal:{CID}:{SENDER}") is None

    task = asyncio.create_task(worker())
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        assert _channel_run_id.get() is None
        if ending == "cancellation":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(RuntimeError, match="PRUEBA-interrupcion"):
                await task
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    env.upstream.call.assert_not_awaited()
    assert _channel_run_id.get() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("environment", [None, "http://bad.invalid", "https://bad.invalid/path"])
async def test_missing_public_origin_never_signs_or_returns_link(make_adapter, monkeypatch, environment):
    from sgmcp_adapter import channel_binding

    if environment is None:
        monkeypatch.delenv("APP_PUBLIC_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("APP_PUBLIC_BASE_URL", environment)
    signer = AsyncMock(side_effect=AssertionError("PRUEBA-no-token"))
    monkeypatch.setattr(channel_binding, "sign_verification_token", signer)
    env = make_adapter()
    rid = await new_run()
    with pytest.raises(HTTPException) as raised:
        await invoke(rid)
    assert raised.value.detail == {
        "code": "supabase_public_base_url_required",
        "message": "Un administrador debe configurar la URL publica HTTPS del empleado para verificar este canal.",
    }
    assert "token" not in json.dumps(raised.value.detail)
    assert "/supabase-canal-verificar" not in json.dumps(raised.value.detail)
    signer.assert_not_called()
    env.adapter.execute.assert_not_awaited()
    env.upstream.call.assert_not_awaited()


@pytest.mark.asyncio
async def test_first_channel_link_loads_encrypted_origin_before_signing(monkeypatch):
    from fastapi import FastAPI
    from urllib.parse import parse_qs, urlsplit
    from sgmcp_adapter.channel_binding import verify_token
    from sgmcp_adapter.integration import mount

    public = "https://configured.example.invalid:8443"
    async with app_write() as session:
        session.add(AppSetting(key="sgmcp_adapter_connection", value=encryption.encrypt_blob({
            "project_url": URL, "auth_mode": "rls_personal", "anon_key": ANON,
            "tables": TABLES, "public_base_url": public,
        })))
        await session.commit()
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://env.example.invalid")
    async def user():
        return SimpleNamespace(email=EMAIL)

    adapter = mount(FastAPI(), user)
    assert adapter.public_base_url is None
    rid = await new_run()
    with pytest.raises(HTTPException) as raised:
        await invoke(rid)
    assert raised.value.detail["code"] == "supabase_channel_not_verified"
    link, = [line for line in raised.value.detail["message"].splitlines() if line.startswith("https://")]
    parsed = urlsplit(link)
    assert parsed.scheme == "https" and parsed.netloc == "configured.example.invalid:8443"
    assert parsed.path == "/supabase-canal-verificar"
    assert verify_token(parse_qs(parsed.query)["token"][0]) == (CID, "15550001001")
    assert adapter.public_base_url == public


@pytest.mark.asyncio
async def test_invalid_run_never_loads_public_origin(make_adapter):
    env = make_adapter()
    loader = AsyncMock(side_effect=AssertionError("PRUEBA-no-load"))
    register_tools(env.adapter, configuration_loader=loader)
    for rid in (999999, await new_run(origen="programado")):
        with pytest.raises(HTTPException) as raised:
            await invoke(rid)
        assert raised.value.status_code == 401
    loader.assert_not_awaited()
