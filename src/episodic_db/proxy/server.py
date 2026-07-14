"""API proxy server — intercepts Claude API traffic for token capture."""

import json
import re
import ssl
import time

import aiohttp
import certifi
from aiohttp import web

from .sse_parser import ReconstructedResponse, accumulate_response
from .port_utils import find_available_port
from .token_bridge import TokenBridge
from episodic_db.store.db import Database


def _strip_system_reminders(text: str) -> str:
    """Remove <system-reminder>...</system-reminder> blocks from user message."""
    stripped = re.sub(r"<system-reminder>.*?</system-reminder>\s*", "", text, flags=re.DOTALL)
    return stripped.strip()


class ProxyServer:
    def __init__(
        self,
        db: Database,
        port: int = 8080,
        api_url: str = "https://api.anthropic.com",
    ):
        self.port = port
        self.api_url = api_url
        self.db = db
        self.bridge = TokenBridge(db)
        self.current_session_id = "default"
        self.call_counters: dict[str, int] = {}
        self._client_session: aiohttp.ClientSession | None = None

    def _next_call_index(self, session_id: str) -> int:
        self.call_counters.setdefault(session_id, 0)
        self.call_counters[session_id] += 1
        return self.call_counters[session_id]

    async def _get_client(self) -> aiohttp.ClientSession:
        if self._client_session is None or self._client_session.closed:
            ssl_ctx = ssl.create_default_context(cafile=certifi.where())
            connector = aiohttp.TCPConnector(ssl=ssl_ctx)
            self._client_session = aiohttp.ClientSession(connector=connector)
        return self._client_session

    async def handle_control_session(self, request: web.Request) -> web.Response:
        data = await request.json()
        self.current_session_id = data.get("session_id", "default")
        self.call_counters.setdefault(self.current_session_id, 0)
        return web.json_response({"status": "ok", "session_id": self.current_session_id})

    async def handle_control_status(self, request: web.Request) -> web.Response:
        return web.json_response({
            "status": "running",
            "session_id": self.current_session_id,
            "call_count": self.call_counters.get(self.current_session_id, 0),
        })

    def _extract_tool_use_ids(self, response: ReconstructedResponse | dict) -> list[str]:
        if isinstance(response, ReconstructedResponse):
            blocks = response.content_blocks
        else:
            blocks = response.get("content", [])
        return [
            b.get("id", "") for b in blocks
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
        ]

    def _extract_usage(self, response: ReconstructedResponse | dict) -> dict:
        if isinstance(response, ReconstructedResponse):
            return response.usage
        return response.get("usage", {})

    def _extract_model(self, response: ReconstructedResponse | dict, request_data: dict) -> str:
        if isinstance(response, ReconstructedResponse):
            return response.model or request_data.get("model", "unknown")
        return response.get("model", request_data.get("model", "unknown"))

    def _extract_assistant_text(self, response: ReconstructedResponse | dict) -> str:
        if isinstance(response, ReconstructedResponse):
            return response.assistant_text
        blocks = response.get("content", [])
        return "".join(
            b.get("text", "") for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        )

    def _extract_user_message(self, request_data: dict) -> str:
        """Extract the last user message, stripping system-reminder tags."""
        messages = request_data.get("messages", [])
        for msg in reversed(messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                text = "\n".join(parts)
            else:
                continue
            return _strip_system_reminders(text)
        return ""

    async def _log_call(self, session_id: str, request_data: dict, response, latency_ms: float, timestamp: str):
        call_index = self._next_call_index(session_id)
        tool_use_ids = self._extract_tool_use_ids(response)
        usage = self._extract_usage(response)
        model = self._extract_model(response, request_data)
        assistant_text = self._extract_assistant_text(response)
        user_message = self._extract_user_message(request_data)

        tokens = {
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
        }

        self.bridge.log_proxy_call(
            session_id=session_id,
            call_index=call_index,
            model=model,
            tokens=tokens,
            tool_use_ids=tool_use_ids,
            latency_ms=latency_ms,
            timestamp=timestamp,
            assistant_text=assistant_text,
            user_message=user_message,
        )

    async def handle_messages(self, request: web.Request) -> web.Response | web.StreamResponse:
        body = await request.read()
        request_data = json.loads(body)
        is_streaming = request_data.get("stream", False)
        start_time = time.time()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

        session_id = request.headers.get("X-Session-ID", self.current_session_id)

        headers = {}
        for key, value in request.headers.items():
            lower = key.lower()
            if lower in ("host", "content-length", "transfer-encoding"):
                continue
            headers[key] = value

        client = await self._get_client()
        target_url = f"{self.api_url}{request.path}"

        if is_streaming:
            return await self._handle_streaming(client, target_url, headers, body, request_data, request, start_time, timestamp, session_id)
        else:
            return await self._handle_non_streaming(client, target_url, headers, body, request_data, start_time, timestamp, session_id)

    async def _handle_streaming(
        self, client, target_url, headers, body, request_data, request, start_time, timestamp, session_id
    ) -> web.StreamResponse:
        async with client.post(target_url, headers=headers, data=body) as upstream:
            response = web.StreamResponse(status=upstream.status)
            response.content_type = "text/event-stream"
            response.headers["Cache-Control"] = "no-cache"
            for key in ("x-request-id", "request-id"):
                if key in upstream.headers:
                    response.headers[key] = upstream.headers[key]
            await response.prepare(request)

            accumulated_lines: list[str] = []
            buffer = b""

            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    decoded = line.decode("utf-8", errors="replace").strip()
                    if decoded:
                        accumulated_lines.append(decoded)

            if buffer:
                decoded = buffer.decode("utf-8", errors="replace").strip()
                if decoded:
                    accumulated_lines.append(decoded)

            await response.write_eof()

        latency_ms = (time.time() - start_time) * 1000
        events = []
        for line in accumulated_lines:
            if line.startswith("data: "):
                payload = line[6:]
                if payload == "[DONE]":
                    continue
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass

        reconstructed = accumulate_response(events)
        await self._log_call(session_id, request_data, reconstructed, latency_ms, timestamp)
        return response

    async def _handle_non_streaming(
        self, client, target_url, headers, body, request_data, start_time, timestamp, session_id
    ) -> web.Response:
        async with client.post(target_url, headers=headers, data=body) as upstream:
            response_body = await upstream.read()
            latency_ms = (time.time() - start_time) * 1000

            try:
                response_data = json.loads(response_body)
            except json.JSONDecodeError:
                response_data = {}

            await self._log_call(session_id, request_data, response_data, latency_ms, timestamp)

            resp_headers = {}
            for key in ("x-request-id", "request-id", "content-type"):
                if key in upstream.headers:
                    resp_headers[key] = upstream.headers[key]

            return web.Response(
                status=upstream.status,
                body=response_body,
                headers=resp_headers,
            )

    async def handle_catch_all(self, request: web.Request) -> web.Response:
        body = await request.read()
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding")
        }
        client = await self._get_client()
        target_url = f"{self.api_url}{request.path}"

        method = getattr(client, request.method.lower())
        async with method(target_url, headers=headers, data=body if body else None) as upstream:
            resp_body = await upstream.read()
            return web.Response(
                status=upstream.status,
                body=resp_body,
                content_type=upstream.headers.get("content-type", "application/json"),
            )

    async def on_shutdown(self, app: web.Application):
        if self._client_session and not self._client_session.closed:
            await self._client_session.close()

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/control/set-session", self.handle_control_session)
        app.router.add_get("/control/status", self.handle_control_status)
        app.router.add_post("/v1/messages", self.handle_messages)
        app.router.add_route("*", "/{path:.*}", self.handle_catch_all)
        app.on_shutdown.append(self.on_shutdown)
        return app

    async def start(self):
        app = self.create_app()
        runner = web.AppRunner(app)
        await runner.setup()

        available_port = find_available_port(self.port)
        if available_port is None:
            raise RuntimeError(f"Could not find available port starting from {self.port}")

        if available_port != self.port:
            self.port = available_port

        site = web.TCPSite(runner, "127.0.0.1", self.port)
        await site.start()
        return runner
