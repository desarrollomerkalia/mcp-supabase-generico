"""Standalone MCP endpoint - a protocol adapter, not the primary wiring.

This exposes the four generic tools as their own small MCP server,
useful for testing the adapter in isolation (see scripts/probe_handshake.py
in the original project) or for platforms that talk to an employee over
a standalone MCP endpoint instead of an internal tool registry.

IMPORTANT: writes made through this endpoint get the Adapter's session and
scope checks (preflight_write/validate_write_context), but NOT the durable
ToolCallLog idempotency journal in tools.py. For the employee's real chat
flow, use tools.register_tools() instead, which wires into WAI's internal
MCP registry and gets the durable journal. Do not mount both paths with
write access pointed at the same employee unless you understand that
tradeoff.
"""

from __future__ import annotations

import hmac
import json

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from .config import DENIABLE_TOOLS
from .core import Adapter, AdapterError
from .tool_catalogue import tool_catalogue


def mcp_router(adapter: Adapter, service_token: str) -> APIRouter:
    if not isinstance(service_token, str) or len(service_token) < 32:
        raise ValueError("Configura un secreto de servicio de al menos 32 caracteres para este endpoint MCP.")
    router = APIRouter()

    async def service_auth(authorization: "str | None" = Header(default=None)):
        if not authorization or not hmac.compare_digest(authorization, "Bearer " + service_token):
            raise HTTPException(401, "MCP no autorizado.")

    @router.get("/mcp/agent-runtime", dependencies=[Depends(service_auth)])
    async def runtime_configuration():
        return {"mcp": {}, "reasoning_effort": "", "extra_skills": [], "available_models": []}

    @router.get("/mcp/agent-runtime/ready", dependencies=[Depends(service_auth)])
    async def runtime_ready():
        return {"ready": True, "connectors": {}}

    @router.post("/mcp")
    @router.post("/mcp/supabase")
    async def rpc(request: Request, authorization: "str | None" = Header(default=None)):
        if not authorization or not hmac.compare_digest(authorization, "Bearer " + service_token):
            raise HTTPException(401, "MCP no autorizado.")
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 65536:
                raise HTTPException(413, "Solicitud demasiado grande.")
        try:
            message = json.loads(body)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Solicitud MCP no valida.") from None
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise HTTPException(400, "Solicitud MCP no valida.")
        method = message.get("method")
        request_id = message.get("id")
        if method == "notifications/initialized":
            return Response(status_code=202)
        if method == "initialize":
            result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}}, "serverInfo": {"name": "sgmcp-adapter-generic", "version": "0.1.0"}}
        elif method == "tools/list":
            result = {"tools": tool_catalogue(adapter.config)}
        elif method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict) or not isinstance(params.get("arguments"), dict):
                raise HTTPException(400, "Argumentos MCP no validos.")
            arguments = dict(params["arguments"])
            run_token = arguments.pop("run_token", None)
            name = params.get("name")
            try:
                if name in DENIABLE_TOOLS:
                    context = await adapter.preflight_write(run_token, name, arguments)
                    await adapter.validate_write_context(run_token, context)
                    output = await adapter.execute(run_token, name, arguments, write_context=context)
                else:
                    output = await adapter.execute(run_token, name, arguments)
                result = {"content": [{"type": "text", "text": json.dumps(output)}]}
            except AdapterError as error:
                result = {"isError": True, "content": [{"type": "text", "text": str(error)}]}
            except Exception:
                result = {"isError": True, "content": [{"type": "text", "text": "No se pudo confirmar la operacion."}]}
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Metodo no permitido."}})
        return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})

    return router
