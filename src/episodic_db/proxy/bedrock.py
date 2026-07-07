"""AWS Bedrock proxy — passthrough auth, same token capture logic."""

import json
import ssl
import time

import aiohttp
import certifi
from aiohttp import web

from .sse_parser import accumulate_response
from .port_utils import find_available_port
from .token_bridge import TokenBridge
from episodic_db.store.db import Database


class BedrockProxyServer:
    def __init__(
        self,
        db: Database,
        port: int = 8080,
    ):
        self.port = port
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
        return web.json_response({"status": "ok", "session_id": self.current_session_id})

    async def handle_invoke(self, request: web.Request) -> web.Response | web.StreamResponse:
        body = await request.read()
        request_data = json.loads(body)
        start_time = time.time()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())

        session_id = request.headers.get("X-Session-ID", self.current_session_id)

        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding")
        }

        bedrock_url = request.headers.get("X-Bedrock-Endpoint", "")
        if not bedrock_url:
            return web.Response(status=400, text="Missing X-Bedrock-Endpoint header")

        target_url = f"{bedrock_url}{request.path}"
        client = await self._get_client()

        async with client.post(target_url, headers=headers, data=body) as upstream:
            response_body = await upstream.read()
            latency_ms = (time.time() - start_time) * 1000

            try:
                response_data = json.loads(response_body)
            except json.JSONDecodeError:
                response_data = {}

            usage = response_data.get("usage", {})
            content = response_data.get("content", [])
            tool_use_ids = [
                b["id"] for b in content
                if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
            ]
            model = response_data.get("model", request_data.get("model", "unknown"))

            tokens = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            }

            call_index = self._next_call_index(session_id)
            self.bridge.log_proxy_call(
                session_id=session_id,
                call_index=call_index,
                model=model,
                tokens=tokens,
                tool_use_ids=tool_use_ids,
                latency_ms=latency_ms,
                timestamp=timestamp,
            )

            resp_headers = {}
            for key in ("x-request-id", "content-type"):
                if key in upstream.headers:
                    resp_headers[key] = upstream.headers[key]

            return web.Response(
                status=upstream.status,
                body=response_body,
                headers=resp_headers,
            )

    async def on_shutdown(self, app: web.Application):
        if self._client_session and not self._client_session.closed:
            await self._client_session.close()

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/control/set-session", self.handle_control_session)
        app.router.add_route("*", "/{path:.*}", self.handle_invoke)
        app.on_shutdown.append(self.on_shutdown)
        return app

    async def start(self):
        app = self.create_app()
        runner = web.AppRunner(app)
        await runner.setup()

        available_port = find_available_port(self.port)
        if available_port is None:
            raise RuntimeError(f"Could not find available port starting from {self.port}")
        self.port = available_port

        site = web.TCPSite(runner, "127.0.0.1", self.port)
        await site.start()
        return runner
