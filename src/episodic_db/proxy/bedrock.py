"""AWS Bedrock proxy — passthrough auth, same token capture logic."""

import json
import os
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

    def _get_bedrock_url(self) -> str:
        url = os.environ.get("AWS_BEDROCK_ENDPOINT", "")
        if not url:
            region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
            url = f"https://bedrock-runtime.{region}.amazonaws.com"
        return url

    async def handle_control_session(self, request: web.Request) -> web.Response:
        data = await request.json()
        self.current_session_id = data.get("session_id", "default")
        return web.json_response({"status": "ok", "session_id": self.current_session_id})

    def _extract_model_from_path(self, path: str) -> str:
        """Extract model ID from Bedrock URL path like /model/{model_id}/invoke."""
        parts = path.split("/")
        for i, part in enumerate(parts):
            if part == "model" and i + 1 < len(parts):
                return parts[i + 1]
        return "unknown"

    async def handle_invoke(self, request: web.Request) -> web.Response | web.StreamResponse:
        body = await request.read()
        if not body:
            return web.Response(status=200, text="ok")

        try:
            request_data = json.loads(body)
        except json.JSONDecodeError:
            return web.Response(status=400, text="Invalid JSON")

        is_streaming = "invoke-with-response-stream" in request.path or request_data.get("stream", False)
        start_time = time.time()
        timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
        session_id = request.headers.get("X-Session-ID", self.current_session_id)
        model_id = self._extract_model_from_path(request.path)

        headers = {
            k: v for k, v in request.headers.items()
            if k.lower() not in ("host", "content-length", "transfer-encoding")
        }

        bedrock_url = self._get_bedrock_url()
        target_url = f"{bedrock_url}{request.path}"
        client = await self._get_client()

        if is_streaming:
            return await self._handle_streaming(
                client, target_url, headers, body, request_data, request,
                start_time, timestamp, session_id, model_id,
            )
        else:
            return await self._handle_non_streaming(
                client, target_url, headers, body, request_data,
                start_time, timestamp, session_id, model_id,
            )

    async def _handle_streaming(
        self, client, target_url, headers, body, request_data, request,
        start_time, timestamp, session_id, model_id,
    ) -> web.StreamResponse:
        async with client.post(target_url, headers=headers, data=body) as upstream:
            response = web.StreamResponse(status=upstream.status)
            content_type = upstream.headers.get("content-type", "application/vnd.amazon.eventstream")
            response.content_type = content_type
            for key in ("x-amzn-requestid", "x-amzn-bedrock-invocation-latency"):
                if key in upstream.headers:
                    response.headers[key] = upstream.headers[key]
            await response.prepare(request)

            accumulated_chunks: list[bytes] = []

            async for chunk in upstream.content.iter_any():
                await response.write(chunk)
                accumulated_chunks.append(chunk)

            await response.write_eof()

        latency_ms = (time.time() - start_time) * 1000

        raw = b"".join(accumulated_chunks)
        events = self._decode_eventstream(raw)
        reconstructed = accumulate_response(events)
        if not reconstructed.model:
            reconstructed.model = model_id
        self._log_response(session_id, request_data, reconstructed, latency_ms, timestamp)
        return response

    def _decode_eventstream(self, raw: bytes) -> list[dict]:
        """Decode AWS event-stream binary protocol into a list of JSON events."""
        import base64
        import struct

        events = []
        offset = 0
        while offset + 12 <= len(raw):
            total_length = struct.unpack("!I", raw[offset:offset+4])[0]
            if total_length < 16 or offset + total_length > len(raw):
                break
            headers_length = struct.unpack("!I", raw[offset+4:offset+8])[0]

            payload_start = offset + 12 + headers_length
            payload_end = offset + total_length - 4
            payload = raw[payload_start:payload_end]

            if payload:
                try:
                    parsed = json.loads(payload)
                    if "bytes" in parsed:
                        decoded = base64.b64decode(parsed["bytes"])
                        try:
                            event = json.loads(decoded)
                            events.append(event)
                        except (json.JSONDecodeError, UnicodeDecodeError):
                            pass
                    elif "type" in parsed:
                        events.append(parsed)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass

            offset += total_length
        return events

    async def _handle_non_streaming(
        self, client, target_url, headers, body, request_data,
        start_time, timestamp, session_id, model_id,
    ) -> web.Response:
        async with client.post(target_url, headers=headers, data=body) as upstream:
            response_body = await upstream.read()
            latency_ms = (time.time() - start_time) * 1000

            try:
                response_data = json.loads(response_body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                response_data = {}

            if isinstance(response_data, dict) and "model" not in response_data:
                response_data["model"] = model_id
            self._log_response(session_id, request_data, response_data, latency_ms, timestamp)

            resp_headers = {}
            for key in ("x-amzn-requestid", "content-type"):
                if key in upstream.headers:
                    resp_headers[key] = upstream.headers[key]

            return web.Response(
                status=upstream.status,
                body=response_body,
                headers=resp_headers,
            )

    def _log_response(self, session_id, request_data, response, latency_ms, timestamp):
        if hasattr(response, "usage"):
            # ReconstructedResponse from streaming
            usage = response.usage
            content = response.content_blocks
            model = response.model or request_data.get("model", "unknown")
        else:
            # dict from non-streaming
            usage = response.get("usage", {})
            content = response.get("content", [])
            model = response.get("model", request_data.get("model", "unknown"))

        tool_use_ids = [
            b["id"] for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("id")
        ]

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
