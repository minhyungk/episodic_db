"""SSE stream parser and response reconstructor."""

import json
from dataclasses import dataclass, field


@dataclass
class ReconstructedResponse:
    model: str = ""
    message_id: str = ""
    stop_reason: str = ""
    usage: dict = field(default_factory=dict)
    content_blocks: list = field(default_factory=list)
    assistant_text: str = ""


def parse_sse_events(raw_lines: list[str]) -> list[dict]:
    events = []
    for line in raw_lines:
        line = line.strip()
        if line.startswith("data: "):
            payload = line[6:]
            if payload == "[DONE]":
                continue
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                pass
    return events


def accumulate_response(events: list[dict]) -> ReconstructedResponse:
    resp = ReconstructedResponse()
    content_blocks = {}
    text_parts = {}
    json_parts = {}

    for event in events:
        event_type = event.get("type", "")

        if event_type == "message_start":
            msg = event.get("message", {})
            resp.model = msg.get("model", "")
            resp.message_id = msg.get("id", "")
            usage = msg.get("usage", {})
            resp.usage.update(usage)

        elif event_type == "content_block_start":
            idx = event.get("index", 0)
            block = event.get("content_block", {})
            content_blocks[idx] = {
                "type": block.get("type", ""),
                "id": block.get("id", ""),
                "name": block.get("name", ""),
            }
            text_parts[idx] = []
            json_parts[idx] = []

        elif event_type == "content_block_delta":
            idx = event.get("index", 0)
            delta = event.get("delta", {})
            delta_type = delta.get("type", "")
            if delta_type == "text_delta":
                text_parts.setdefault(idx, []).append(delta.get("text", ""))
            elif delta_type == "input_json_delta":
                json_parts.setdefault(idx, []).append(delta.get("partial_json", ""))

        elif event_type == "message_delta":
            delta = event.get("delta", {})
            resp.stop_reason = delta.get("stop_reason", "")
            usage = event.get("usage", {})
            resp.usage.update(usage)

    for idx, block in sorted(content_blocks.items()):
        block_type = block["type"]
        if block_type == "text":
            text = "".join(text_parts.get(idx, []))
            resp.content_blocks.append({"type": "text", "text": text})
            resp.assistant_text += text
        elif block_type == "tool_use":
            raw_json = "".join(json_parts.get(idx, []))
            try:
                tool_input = json.loads(raw_json) if raw_json else {}
            except json.JSONDecodeError:
                tool_input = {"_raw": raw_json}
            resp.content_blocks.append({
                "type": "tool_use",
                "id": block["id"],
                "name": block["name"],
                "input": tool_input,
            })
        elif block_type == "tool_result":
            text = "".join(text_parts.get(idx, []))
            resp.content_blocks.append({"type": "tool_result", "content": text})
        else:
            resp.content_blocks.append({"type": block_type})

    return resp
