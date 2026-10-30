"""Small, per-call MCP streamable HTTP client for the generic Supabase MCP.

Ported from the original Mara adapter without changes to the protocol
handling: it was already generic (it never knew about tables, columns or
business rules). The only change is which tool names it allows.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Callable

import httpx


_MAX_BYTES = 2 * 1024 * 1024
_TOOLS = frozenset({"supabase_leer", "supabase_insertar", "supabase_actualizar", "supabase_eliminar"})


class MCPError(Exception):
    """Public error that never exposes upstream details."""

    def __init__(self, *, kind="protocol", retryable=False, phase=None, http_status=None):
        super().__init__("MCP request failed")
        self.kind = (
            kind
            if isinstance(kind, str) and kind in {"protocol", "transport", "http", "tool", "remote"}
            else "protocol"
        )
        self.retryable = retryable is True
        self.phase = (
            phase
            if type(phase) is str and phase in {"initialize", "initialized", "tools_call"}
            else None
        )
        self.http_status = (
            http_status if type(http_status) is int and 100 <= http_status <= 599 else None
        )


def _result(message: object, request_id: str) -> dict:
    if (
        not isinstance(message, dict)
        or message.get("jsonrpc") != "2.0"
        or message.get("id") != request_id
    ):
        raise MCPError()
    if "error" in message:
        error = message["error"]
        if (
            not isinstance(error, dict)
            or type(error.get("code")) is not int
            or not isinstance(error.get("message"), str)
        ):
            raise MCPError()
        raise MCPError(kind="remote")
    if not isinstance(message.get("result"), dict):
        raise MCPError()
    if "isError" in message["result"]:
        if not isinstance(message["result"]["isError"], bool):
            raise MCPError()
        if message["result"]["isError"]:
            raise MCPError(kind="tool")
    return message["result"]


async def _read_result(response: httpx.Response, request_id: str) -> dict:
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type not in {"application/json", "text/event-stream"}:
        raise MCPError()

    size = 0
    pending = b""
    data = []
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > _MAX_BYTES:
            raise MCPError()
        pending += chunk
        if media_type == "application/json":
            continue
        while True:
            newline = re.search(rb"\r\n|\r|\n", pending)
            if newline is None:
                break
            # A CR at a chunk boundary may still be followed by LF.
            if newline.group() == b"\r" and newline.end() == len(pending):
                break
            line = pending[: newline.start()]
            pending = pending[newline.end() :]
            if line.startswith(b"data:"):
                value = line[5:]
                data.append(value[1:] if value.startswith(b" ") else value)
            elif not line and data:
                message = json.loads(b"\n".join(data))
                data = []
                if isinstance(message, dict) and message.get("id") == request_id:
                    return _result(message, request_id)

    if media_type == "application/json":
        return _result(json.loads(pending), request_id)
    # A final CR is itself a complete SSE event delimiter.
    if pending == b"\r" and data:
        return _result(json.loads(b"\n".join(data)), request_id)
    raise MCPError()


async def _read_initialized_response(response: httpx.Response) -> None:
    body = bytearray()
    async for chunk in response.aiter_bytes():
        if len(body) + len(chunk) > _MAX_BYTES:
            raise MCPError()
        body.extend(chunk)
    if not body.strip():
        return
    media_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if response.status_code != 200 or media_type != "application/json":
        raise MCPError()
    message = json.loads(body)
    if message == {"jsonrpc": "2.0", "result": {}}:
        # Observed sgmcp acknowledgement for this notification: no request id,
        # empty result. Do not accept arbitrary JSON or a JSON-RPC error here.
        return
    if (isinstance(message, dict) and message.get("jsonrpc") == "2.0"
            and isinstance(message.get("error"), dict)
            and type(message["error"].get("code")) is int
            and isinstance(message["error"].get("message"), str)):
        raise MCPError(kind="remote")
    raise MCPError()


class MCPClient:
    """Talks to one shared instance of the generic Supabase MCP.

    Because the MCP is shared across every employee, credentials never
    live here or in an environment variable: they are passed in on every
    call by whoever constructs the ``arguments`` dict (see core.Adapter).
    """

    def __init__(
        self,
        client_factory: "Callable[[], httpx.AsyncClient] | None" = None,
        url: str = "https://mcp-supabase-generico.onrender.com",
        service_header: "tuple[str, str] | None" = None,
    ):
        self.client_factory = client_factory
        self.url = url
        self.service_header = service_header

    async def call(self, tool: str, arguments: dict) -> dict:
        if tool not in _TOOLS or not isinstance(arguments, dict):
            raise MCPError() from None

        session_id = None
        protocol = "2024-11-05"
        failure = None
        result = None
        client = None
        phase = None
        http_status = None

        def headers() -> dict:
            values = {
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": protocol,
            }
            if self.service_header is not None:
                values[self.service_header[0]] = self.service_header[1]
            if session_id is not None:
                values["MCP-Session-Id"] = session_id
            return values

        try:
            client = (
                self.client_factory()
                if self.client_factory is not None
                else httpx.AsyncClient(timeout=30, trust_env=False)
            )
            request_id = uuid.uuid4().hex
            phase, http_status = "initialize", None
            async with client.stream(
                "POST",
                self.url,
                headers=headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": protocol,
                        "capabilities": {},
                        "clientInfo": {"name": "sgmcp-adapter-generic", "version": "1.0"},
                    },
                },
                follow_redirects=False,
            ) as response:
                http_status = response.status_code
                session_id = response.headers.get("MCP-Session-Id")
                response.raise_for_status()
                initialized = await _read_result(response, request_id)
            negotiated = initialized.get("protocolVersion")
            if not isinstance(negotiated, str) or not negotiated:
                raise MCPError()
            protocol = negotiated

            phase, http_status = "initialized", None
            async with client.stream(
                "POST",
                self.url,
                headers=headers(),
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                follow_redirects=False,
            ) as response:
                http_status = response.status_code
                response.raise_for_status()
                await _read_initialized_response(response)

            request_id = uuid.uuid4().hex
            phase, http_status = "tools_call", None
            async with client.stream(
                "POST",
                self.url,
                headers=headers(),
                json={
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": "tools/call",
                    "params": {"name": tool, "arguments": arguments},
                },
                follow_redirects=False,
            ) as response:
                http_status = response.status_code
                response.raise_for_status()
                result = await _read_result(response, request_id)
        except MCPError as error:
            failure = (error.kind, error.retryable)
        except (httpx.TimeoutException, httpx.NetworkError):
            failure = ("transport", True)
        except httpx.HTTPStatusError as error:
            http_status = error.response.status_code
            failure = ("http", http_status in {502, 503, 504})
        except Exception:
            failure = ("protocol", False)
        finally:
            try:
                if session_id is not None:
                    try:
                        async with client.stream(
                            "DELETE", self.url, headers=headers(), follow_redirects=False
                        ) as response:
                            response.raise_for_status()
                    except Exception:
                        # Cleanup cannot turn a committed write into an apparent failure.
                        pass
            finally:
                if client is not None:
                    try:
                        await client.aclose()
                    except Exception:
                        pass

        # Raise outside the handler: even __context__ must not retain HTTP errors.
        if failure is not None:
            raise MCPError(
                kind=failure[0], retryable=failure[1], phase=phase, http_status=http_status
            ) from None
        return result
