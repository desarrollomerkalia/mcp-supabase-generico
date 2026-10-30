#!/usr/bin/env python3
"""Run this FROM INSIDE a generated employee backend (same Python
environment where `app.*` is importable), before integrating
sgmcp_adapter and again before publishing/re-publishing this skill
version. It never connects to a database or Supabase - it only checks
that the host exposes the symbols/shapes sgmcp_adapter depends on.

Exit code 0 means the contract held. Any nonzero exit means STOP: do not
patch sgmcp_adapter to work around a mismatch, and do not silently adapt
app/main.py - report the exact mismatch so the skill (or the template)
gets fixed once, centrally.

Usage (from the employee backend's root, with its venv active):
    python checks/check_host_contract.py
"""

import asyncio
import dataclasses
import inspect
import sys


class Check:
    def __init__(self, label):
        self.label = label
        self.ok = None
        self.detail = ""

    def passed(self, detail=""):
        self.ok = True
        self.detail = detail

    def failed(self, detail):
        self.ok = False
        self.detail = detail


results = []


def check(label):
    c = Check(label)
    results.append(c)
    return c


def has_columns(cls, names):
    missing = [name for name in names if not hasattr(cls, name)]
    return missing


def run():
    # -- app.auth -----------------------------------------------------
    c = check("app.auth.require_project_access existe y es invocable")
    try:
        from app.auth import require_project_access
        c.passed() if callable(require_project_access) else c.failed("no es invocable")
    except Exception as error:
        c.failed(repr(error))

    c = check("app.auth.require_admin existe y es invocable")
    try:
        from app.auth import require_admin
        c.passed() if callable(require_admin) else c.failed("no es invocable")
    except Exception as error:
        c.failed(repr(error))

    c = check("app.auth.TokenPayload expone .email")
    try:
        from app.auth import TokenPayload
        fields = {f.name for f in dataclasses.fields(TokenPayload)} if dataclasses.is_dataclass(TokenPayload) else set(getattr(TokenPayload, "__annotations__", {}))
        c.passed() if "email" in fields else c.failed("no se encontro 'email' en los campos: %s" % sorted(fields))
    except Exception as error:
        c.failed(repr(error))

    # -- app.app_database ----------------------------------------------
    c = check("app.app_database.app_session y app_write existen")
    try:
        from app.app_database import app_session, app_write
        c.passed() if callable(app_session) and callable(app_write) else c.failed("alguno no es invocable")
    except Exception as error:
        c.failed(repr(error))

    # -- app.app_models.runtime -----------------------------------------
    c = check("AgentRun tiene id/run_token/mode/status/finished_at/request_meta_json")
    try:
        from app.app_models.runtime import AgentRun
        missing = has_columns(AgentRun, ["id", "run_token", "mode", "status", "finished_at", "request_meta_json"])
        c.passed() if not missing else c.failed("faltan columnas: %s" % missing)
    except Exception as error:
        c.failed(repr(error))

    c = check("AppSetting tiene key/value/updated_by")
    try:
        from app.app_models.runtime import AppSetting
        missing = has_columns(AppSetting, ["key", "value", "updated_by"])
        c.passed() if not missing else c.failed("faltan columnas: %s" % missing)
    except Exception as error:
        c.failed(repr(error))

    c = check("ToolCallLog tiene run_id/step_idx/tool_name/idempotency_key/result_hash/result_json")
    try:
        from app.app_models.runtime import ToolCallLog
        missing = has_columns(ToolCallLog, ["run_id", "step_idx", "tool_name", "idempotency_key", "result_hash", "result_json"])
        c.passed() if not missing else c.failed("faltan columnas: %s" % missing)
    except Exception as error:
        c.failed(repr(error))

    # -- app.channels.encryption -----------------------------------------
    c = check("encrypt_blob/decrypt_blob/encryption_available existen")
    try:
        from app.channels.encryption import decrypt_blob, encrypt_blob, encryption_available
        ok = all(callable(f) for f in (encrypt_blob, decrypt_blob, encryption_available))
        c.passed() if ok else c.failed("alguno no es invocable")
    except Exception as error:
        c.failed(repr(error))

    # -- app.mcp_server / app.mcp_actor -----------------------------------
    c = check("mcp_server.TOOLS y MCP_TOOL_SCHEMAS son diccionarios")
    try:
        from app import mcp_server
        ok = isinstance(mcp_server.TOOLS, dict) and isinstance(mcp_server.MCP_TOOL_SCHEMAS, dict)
        c.passed() if ok else c.failed("alguno no es dict")
    except Exception as error:
        c.failed(repr(error))

    c = check("mcp_actor.LEVEL_HUMAN existe y TOOL_MIN_ROLE es diccionario")
    try:
        from app.mcp_actor import LEVEL_HUMAN, TOOL_MIN_ROLE
        c.passed() if isinstance(TOOL_MIN_ROLE, dict) else c.failed("TOOL_MIN_ROLE no es dict")
    except Exception as error:
        c.failed(repr(error))

    c = check("mcp_server.TOOLS y mcp_actor.TOOL_MIN_ROLE son el MISMO objeto que ve el router MCP")
    try:
        from app import mcp_actor, mcp_server
        same_min_role = getattr(mcp_server, "TOOL_MIN_ROLE", None) is mcp_actor.TOOL_MIN_ROLE
        # Not every template re-exports TOOL_MIN_ROLE under mcp_server; absence
        # of the attribute is not itself a failure, only a confirmed MISMATCH is.
        if hasattr(mcp_server, "TOOL_MIN_ROLE") and not same_min_role:
            c.failed("mcp_server.TOOL_MIN_ROLE y mcp_actor.TOOL_MIN_ROLE son objetos DISTINTOS - mutar uno no afecta al otro")
        else:
            c.passed()
    except Exception as error:
        c.failed(repr(error))

    c = check("ToolCall (app.mcp_server) expone run_id/step_idx/name/arguments")
    try:
        from app.mcp_server import ToolCall
        if dataclasses.is_dataclass(ToolCall):
            names = {f.name for f in dataclasses.fields(ToolCall)}
        else:
            names = set(getattr(ToolCall, "__annotations__", {}))
        missing = {"run_id", "step_idx", "name", "arguments"} - names
        c.passed() if not missing else c.failed("faltan atributos: %s" % sorted(missing))
    except Exception as error:
        c.failed(repr(error))

    # -- app.autonomous.tool_idempotency -----------------------------------
    c = check("_hash y _replay_or_execute existen con la firma esperada")
    try:
        from app.autonomous.tool_idempotency import _hash, _replay_or_execute
        sig = inspect.signature(_replay_or_execute)
        expected = {"session", "run_id", "step_idx", "tool_name", "args", "do_work", "mode"}
        missing = expected - set(sig.parameters)
        is_async = asyncio.iscoroutinefunction(_replay_or_execute)
        if missing:
            c.failed("faltan parametros: %s" % sorted(missing))
        elif not is_async:
            c.failed("_replay_or_execute no es una corrutina")
        else:
            c.passed()
    except Exception as error:
        c.failed(repr(error))

    # -- app.users_directory -----------------------------------------------
    c = check("is_admin(email) existe y es asincrona")
    try:
        from app.users_directory import is_admin
        c.passed() if asyncio.iscoroutinefunction(is_admin) else c.failed("no es una corrutina")
    except Exception as error:
        c.failed(repr(error))

    # -- sgmcp_adapter itself ------------------------------------------------
    c = check("sgmcp_adapter importa sin errores en este entorno")
    try:
        import sgmcp_adapter  # noqa: F401
        from sgmcp_adapter import integration  # noqa: F401
        c.passed()
    except Exception as error:
        c.failed(repr(error))


def main():
    run()
    width = max(len(r.label) for r in results)
    failures = 0
    for r in results:
        status = "OK  " if r.ok else "FAIL"
        if not r.ok:
            failures += 1
        print(f"[{status}] {r.label.ljust(width)}  {r.detail}")
    print()
    if failures:
        print(f"{failures} comprobacion(es) fallaron. NO integres ni publiques - reporta el detalle exacto de cada fallo.")
        sys.exit(1)
    print("Todas las comprobaciones pasaron. El contrato del host coincide con lo que sgmcp_adapter asume.")
    sys.exit(0)


if __name__ == "__main__":
    main()
