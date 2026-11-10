"""Regresiones locales con SQLite temporal de conftest, sin servicios externos.

NO es parte de la suite independiente de sgmcp_adapter (tests/test_adapter_smoke.py,
que corre sin RDWAI instalado). Este archivo necesita el entorno real de un
backend de empleado generado - en particular su conftest.py (ya provisto por
la plantilla, no por este skill), que levanta un SQLite temporal y los
modelos SQLAlchemy reales. Copialo a src/backend/tests/ del empleado donde
ya integraste sgmcp_adapter, y corre pytest desde ahi.

Cubre dos bugs reales encontrados y corregidos en sgmcp_adapter (v1.0.4):
persist() no hacia commit (la configuracion vivia solo en memoria del
proceso web, nunca llegaba a la base de datos), y generic_operation() no
exponia response_shape a un administrador cuando una lectura fallaba con
un formato no verificable. Ambos arreglos ya estan en el paquete; estas
pruebas existen para que nadie los reintroduzca sin darse cuenta.

Los AgentRun sinteticos solo soportan pruebas unitarias del handler registrado:
no son conversaciones reales ni pruebas E2E de chat o autenticacion WAI.
"""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI, HTTPException

from app import mcp_actor, mcp_server
from app.app_database import app_session, app_write
from app.app_models.runtime import AgentRun, AppSetting
from app.auth import require_admin
from app.channels import encryption
from sgmcp_adapter.config import ConnectionConfig
from sgmcp_adapter.core import Adapter, Settings
from sgmcp_adapter.integration import mount
from sgmcp_adapter.tools import register_tools
from sgmcp_adapter.wai_identity import active_chat_email


URL = "https://abcdefghijklmnopqrst.supabase.co"
TEST_KEY = "sb_secret_PRUEBA_solo_local"
ADMIN = "admin@example.invalid"
TABLES = {"notas": {"columns": {"id": {"type": "string"}}}}


@pytest.fixture(autouse=True)
def local_support(monkeypatch):
    # Copias reversibles, manteniendo el alias del mapa de roles del host.
    monkeypatch.setattr(mcp_server, "TOOLS", dict(mcp_server.TOOLS))
    monkeypatch.setattr(mcp_server, "MCP_TOOL_SCHEMAS", dict(mcp_server.MCP_TOOL_SCHEMAS))
    roles = dict(mcp_actor.TOOL_MIN_ROLE)
    monkeypatch.setattr(mcp_actor, "TOOL_MIN_ROLE", roles)
    monkeypatch.setattr(mcp_server, "TOOL_MIN_ROLE", roles)
    monkeypatch.setattr(encryption, "CHANNEL_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(encryption, "_DERIVED_KEY", None)

    async def no_network(*args, **kwargs):
        raise AssertionError("Esta prueba no admite llamadas externas")

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", no_network)


@pytest.mark.asyncio
async def test_configuration_put_commits_encrypted_setting_and_new_mount_reloads(monkeypatch):
    async def admin_user():
        return SimpleNamespace(email=ADMIN)

    from app import users_directory

    monkeypatch.setattr(users_directory, "is_admin", AsyncMock(return_value=True))

    def new_app():
        app = FastAPI()
        app.dependency_overrides[require_admin] = admin_user
        return app, mount(app, admin_user)

    app, adapter = new_app()
    validation_requests = []

    def validate(request):
        validation_requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "abcdefghijklmnopqrst.supabase.co"
        assert request.url.path == "/auth/" + "v1/admin/users"
        assert dict(request.url.params) == {"page": "1", "per_page": "1"}
        assert request.headers["apikey"] == TEST_KEY
        assert request.headers["authorization"] == "Bearer " + TEST_KEY
        return httpx.Response(200, json={"users": []})

    monkeypatch.setattr(adapter, "auth_client_factory", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(validate), trust_env=False,
    ))
    payload = {
        "project_url": URL, "auth_mode": "service_role",
        "service_role_key": TEST_KEY, "tables": TABLES,
        "denied": ["eliminar_registros"], "read_limit": 17,
        "public_base_url": "https://employee.example.invalid",
    }
    expected_config = ConnectionConfig.from_dict(payload)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/api/supabase/configuration", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True
    assert response.json()["changed"] is True
    assert response.json()["access_changed"] is True
    assert len(validation_requests) == 1
    assert TEST_KEY not in response.text

    # Otra sesion debe ver el commit, no el estado en memoria del adaptador.
    async with app_session() as session:
        row = await session.get(AppSetting, "sgmcp_adapter_connection")
        assert row is not None
        assert row.updated_by == ADMIN
        assert TEST_KEY not in row.value
        assert URL not in row.value
        assert payload["public_base_url"] not in row.value
        assert encryption.decrypt_blob(row.value) == {
            **payload, **expected_config.to_dict(), "anon_key": None,
        }

    restarted_app, restarted = new_app()
    assert restarted._settings is None
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted_app), base_url="http://test") as client:
        response = await client.get("/api/supabase/configuration")
    assert response.status_code == 200, response.text
    assert response.json()["configured"] is True
    assert response.json()["tables"] == expected_config.to_dict()["tables"]
    assert response.json()["denied"] == payload["denied"]
    assert response.json()["read_limit"] == 17
    assert restarted.settings == adapter.settings
    assert restarted.config == adapter.config
    assert restarted.config == expected_config
    assert response.json()["public_base_url"] == payload["public_base_url"]
    assert response.json()["effective_public_base_url"] == payload["public_base_url"]
    assert response.json()["public_base_url_source"] == "configuration"
    assert len(validation_requests) == 1
    assert TEST_KEY not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["admin", "member", "authorizer_error", "no_authorizer"])
async def test_unit_read_response_shape_is_admin_only_and_fails_closed(role):
    email = ADMIN if role != "member" else "member@example.invalid"
    token = "a" * 32  # Token sintetico, exclusivamente para esta unidad local.
    async with app_write() as session:
        run = AgentRun(
            idempotency_key="PRUEBA-unit-diagnostics", run_token=token,
            mode="chat", status="RUNNING", prompt="PRUEBA-unitaria-no-chat-real",
            request_meta_json=json.dumps({"memory_user": email}),
        )
        session.add(run)
        await session.commit()
        run_id = run.id

    transport = SimpleNamespace(call=AsyncMock(return_value={
        "data": "PRUEBA-valor-privado-no-es-una-lista",
    }))
    adapter = Adapter(
        ConnectionConfig.from_dict({"auth_mode": "service_role", "tables": TABLES}),
        Settings(URL, service_role_key=TEST_KEY), None, transport, active_chat_email,
    )
    authorize = AsyncMock(return_value=role == "admin")
    if role == "authorizer_error":
        authorize.side_effect = RuntimeError("PRUEBA-fallo-privado-del-autorizador")
    register_tools(adapter, diagnostic_authorizer=None if role == "no_authorizer" else authorize)

    with pytest.raises(HTTPException) as raised:
        await mcp_server.TOOLS["leer_registros"](SimpleNamespace(
            run_id=run_id, name="leer_registros", arguments={"tabla": "notas"}, step_idx=0,
        ))

    assert raised.value.status_code == 502
    detail = raised.value.detail
    assert detail["code"] == "sgmcp_read_response_invalid"
    assert "write_state" not in detail
    if role == "admin":
        assert detail["response_shape"]["schema"] == "sgmcp_read_shape_v1"
        assert detail["response_shape"]["table"] == "notas"
        assert detail["response_shape"]["shape"]["fields"] == {"data": "string"}
        assert detail["response_shape"]["totals_verified"] is False
    else:
        assert "response_shape" not in detail
    if role == "no_authorizer":
        authorize.assert_not_awaited()
    else:
        authorize.assert_awaited_once_with(email)
    transport.call.assert_awaited_once()
    assert transport.call.await_args.args[0] == "supabase_leer"
    assert "PRUEBA-valor-privado" not in json.dumps(detail)
    assert "PRUEBA-fallo-privado" not in json.dumps(detail)
    assert TEST_KEY not in json.dumps(detail)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["rls_personal", "service_role"])
async def test_public_url_updates_preserve_sessions_until_access_changes(monkeypatch, mode):
    from sqlalchemy.ext.asyncio import AsyncSession
    from sgmcp_adapter.core import WriteContext
    from app import users_directory

    async def admin_user():
        return SimpleNamespace(email=ADMIN)

    monkeypatch.setattr(users_directory, "is_admin", AsyncMock(return_value=True))
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://env.example.invalid/")
    app = FastAPI()
    app.dependency_overrides[require_admin] = admin_user
    adapter = mount(app, admin_user)
    requests = []

    def auth(request):
        requests.append(request)
        assert request.url.host == "abcdefghijklmnopqrst.supabase.co"
        if request.method == "POST":
            return httpx.Response(200, json={
                "access_token": "PRUEBA-access", "refresh_token": "PRUEBA-refresh", "expires_in": 3600,
            })
        if request.url.path.endswith("/user"):
            return httpx.Response(200, json={"email": ADMIN, "id": "PRUEBA-subject"})
        return httpx.Response(200, json={})

    monkeypatch.setattr(adapter, "auth_client_factory", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(auth), trust_env=False,
    ))
    payload = {
        "project_url": URL, "auth_mode": mode, "tables": TABLES,
        "anon_key": "sb_publishable_PRUEBA_local", "service_role_key": TEST_KEY,
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/api/supabase/configuration", json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["public_base_url_source"] == "environment"
        if mode == "rls_personal":
            await adapter.login(ADMIN, "PRUEBA-password")
        generation = adapter._configuration_generation
        sessions = dict(adapter._sessions)
        states = dict(adapter._session_states)
        config, settings = adapter.config, adapter.settings
        request_count = len(requests)
        configure = Mock(wraps=adapter.configure)
        monkeypatch.setattr(adapter, "configure", configure)
        payload.pop("anon_key")
        payload.pop("service_role_key")
        public = "https://employee.example.invalid"
        for update, expected in [
            ({"public_base_url": public.upper() + "/"}, public),
            ({}, public), ({"public_base_url": None}, public),
            ({"public_base_url": "   "}, None),
        ]:
            previous_public = adapter.public_base_url
            response = await client.put("/api/supabase/configuration", json={**payload, **update})
            assert response.status_code == 200, response.text
            metadata = response.json()
            assert metadata["access_changed"] is False
            assert metadata["changed"] is (previous_public != expected)
            assert metadata["public_base_url"] == expected
            assert metadata["effective_public_base_url"] == (expected or "https://env.example.invalid")
            assert metadata["public_base_url_source"] == ("configuration" if expected else "environment")
            assert adapter._configuration_generation == generation
            assert adapter._sessions == sessions and adapter._session_states == states
            assert adapter.config is config and adapter.settings is settings
            if mode == "rls_personal":
                adapter._check_write_context(ADMIN, WriteContext(ADMIN, "PRUEBA-subject", URL, generation))
        configure.assert_not_called()
        assert len(requests) == request_count

        invalid = await client.put("/api/supabase/configuration", json={**payload, "public_base_url": "https://secret@bad.invalid"})
        assert invalid.status_code == 503
        assert invalid.json()["detail"]["code"] == "supabase_public_base_url_invalid"
        assert "secret" not in invalid.text
        assert adapter.public_base_url is None
        async with app_session() as session:
            encrypted_before = (await session.get(AppSetting, "sgmcp_adapter_connection")).value

        with monkeypatch.context() as patch:
            patch.setattr(AsyncSession, "commit", AsyncMock(side_effect=RuntimeError("PRUEBA-storage")))
            failed = await client.put("/api/supabase/configuration", json={
                **payload, "read_limit": 19, "public_base_url": public,
            })
        assert failed.status_code == 503
        assert failed.json()["detail"]["code"] == "supabase_configuration_storage_unavailable"
        assert adapter.public_base_url is None
        assert adapter.config is config and adapter.settings is settings
        assert adapter._configuration_generation == generation
        assert adapter._sessions == sessions and adapter._session_states == states
        configure.assert_not_called()
        async with app_session() as session:
            assert (await session.get(AppSetting, "sgmcp_adapter_connection")).value == encrypted_before

        status = await client.get("/api/supabase/session")
        assert status.status_code == 200
        assert not {"public_base_url", "effective_public_base_url", "public_base_url_source"} & status.json().keys()
        changed = await client.put("/api/supabase/configuration", json={**payload, "read_limit": 18})
        assert changed.status_code == 200, changed.text
        assert changed.json()["access_changed"] is True
        assert adapter._configuration_generation == generation + 1
        assert adapter._sessions == {} and adapter._session_states == {}
        configure.assert_called_once()

    # Un blob anterior al campo sigue siendo legible y usa solo el fallback valido.
    async with app_write() as session:
        row = await session.get(AppSetting, "sgmcp_adapter_connection")
        legacy = encryption.decrypt_blob(row.value)
        legacy.pop("public_base_url")
        row.value = encryption.encrypt_blob(legacy)
        await session.commit()
    restarted_app = FastAPI()
    restarted_app.dependency_overrides[require_admin] = admin_user
    restarted = mount(restarted_app, admin_user)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=restarted_app), base_url="http://test") as client:
        loaded = await client.get("/api/supabase/configuration")
        assert loaded.status_code == 200
        assert loaded.json()["public_base_url"] is None
        assert loaded.json()["effective_public_base_url"] == "https://env.example.invalid"
        assert restarted.settings == settings
        monkeypatch.setenv("APP_PUBLIC_BASE_URL", "http://bad.invalid")
        loaded = await client.get("/api/supabase/configuration")
        assert loaded.json()["effective_public_base_url"] is None
        assert loaded.json()["public_base_url_source"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("is_admin", [True, False])
async def test_public_configuration_requires_real_admin_dependency(monkeypatch, is_admin):
    from app.auth import require_project_access
    from app import users_directory

    async def user():
        return SimpleNamespace(email=ADMIN)

    monkeypatch.setattr(users_directory, "is_admin", AsyncMock(return_value=is_admin))
    app = FastAPI()
    app.dependency_overrides[require_project_access] = user
    adapter = mount(app, user)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/api/supabase/configuration")
        assert response.status_code == (200 if is_admin else 403)
        if is_admin:
            assert response.json()["public_base_url"] is None
        else:
            response = await client.put("/api/supabase/configuration", json={
                "project_url": URL, "auth_mode": "service_role", "tables": TABLES,
                "service_role_key": TEST_KEY, "public_base_url": "https://admin.example.invalid",
            })
            assert response.status_code == 403
            assert adapter._settings is None and adapter.public_base_url is None
            async with app_session() as session:
                assert await session.get(AppSetting, "sgmcp_adapter_connection") is None
