"""Generic LLM API client compatible with OpenAI format.

Supports:
- Thinking-mode control per request (model-family dependent payload fields)
- SSE streaming with on_delta callbacks for live display
- Retry with backoff on transient errors (non-streaming path)
"""
import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# Global connection pool (one per base_url to avoid cross-contamination)
_client_pools: dict[str, httpx.AsyncClient] = {}

# Qwen thinking_budget by effort level
_QWEN_BUDGET = {"low": 1024, "medium": 4096, "high": 16384}


async def shutdown_clients():
    """Close all shared httpx clients on shutdown."""
    for url, client in _client_pools.items():
        await client.aclose()
    _client_pools.clear()


def _get_client(base_url: str) -> httpx.AsyncClient:
    """Get or create a connection pool for this base_url."""
    if base_url not in _client_pools:
        timeout = httpx.Timeout(connect=30.0, read=600.0, write=30.0, pool=30.0)
        _client_pools[base_url] = httpx.AsyncClient(timeout=timeout)
    return _client_pools[base_url]


async def _post_with_retry(client: httpx.AsyncClient, url: str, *, json_body: dict, headers: dict,
                           max_retries: int = 2) -> httpx.Response:
    """POST with exponential backoff on 429/5xx/network errors. 400 is never retried."""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            resp = await client.post(url, json=json_body, headers=headers)
            if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
                await asyncio.sleep(2 ** attempt)  # 1s, 2s
                continue
            return resp
        except (httpx.TransportError, httpx.TimeoutException) as e:
            last_exc = e
            if attempt < max_retries:
                await asyncio.sleep(2 ** attempt)
                continue
            raise
    raise last_exc  # unreachable


def thinking_payload(model: str, thinking: str, effort: str) -> dict:
    """Build provider-specific thinking/reasoning parameters.

    thinking: "auto" (send nothing, use model default) / "on" / "off"
    effort:   "low" / "medium" / "high" (only meaningful when thinking is on/auto)
    """
    if thinking not in ("on", "off"):
        return {}
    enabled = thinking == "on"
    m = (model or "").lower()

    if 'qwen' in m:
        payload = {"enable_thinking": enabled}
        if enabled:
            payload["thinking_budget"] = _QWEN_BUDGET.get(effort, 4096)
        return payload
    if 'glm' in m or 'deepseek' in m:
        # Zhipu GLM-4.5+ / DeepSeek V3.1+ style
        return {"thinking": {"type": "enabled" if enabled else "disabled"}}
    # OpenAI o-series/GPT-5, Gemini, Claude and most OpenAI-compatible gateways
    if not enabled:
        return {}
    return {"reasoning_effort": effort}


@dataclass
class AIResponse:
    """Structured response for tool-calling agent."""
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict] | None = None
    raw_message: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


async def _consume_sse_stream(resp, on_delta=None) -> dict:
    """Parse an OpenAI-compatible SSE stream into a final message dict.

    Accumulates delta.content / delta.reasoning_content / delta.tool_calls.
    Calls `await on_delta(kind, text)` per chunk ("reasoning" | "content").
    """
    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    # tool_calls accumulated by index: {index: {"id":..., "name":..., "arguments": str}}
    tool_acc: dict[int, dict] = {}
    usage = {}

    async for line in resp.aiter_lines():
        line = line.strip()
        if not line or not line.startswith("data:"):
            continue
        data_str = line[5:].strip()
        if data_str == "[DONE]":
            break
        try:
            chunk = json.loads(data_str)
        except json.JSONDecodeError:
            continue
        if chunk.get("usage"):
            usage = chunk["usage"]
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        r = delta.get("reasoning_content") or delta.get("reasoning")
        if r:
            reasoning_parts.append(r)
            if on_delta:
                try:
                    await on_delta("reasoning", r)
                except Exception:
                    pass
        c = delta.get("content")
        if c:
            content_parts.append(c)
            if on_delta:
                try:
                    await on_delta("content", c)
                except Exception:
                    pass
        for tc in delta.get("tool_calls") or []:
            idx = tc.get("index", 0)
            slot = tool_acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function") or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["arguments"] += fn["arguments"]

    tool_calls = None
    if tool_acc:
        tool_calls = [tool_acc[i] for i in sorted(tool_acc)]

    return {
        "content": "".join(content_parts) or None,
        "reasoning_content": "".join(reasoning_parts) or None,
        "tool_calls": tool_calls,
        "usage": usage,
    }


class AIClient:
    def __init__(self, base_url: str, model: str, api_key: str, max_tokens: int = 65536,
                 temperature: float = 0.7, thinking: str = "auto", effort: str = "medium",
                 use_stream: bool = True):
        # Normalize: strip trailing slashes and /v1 suffix (client appends its own /v1)
        url = base_url.rstrip('/')
        if url.endswith('/v1'):
            url = url[:-3]
        self.base_url = url
        self.model = model
        self.api_key = api_key
        self.default_max_tokens = max_tokens
        self.temperature = temperature
        self.thinking = thinking
        self.effort = effort
        self.use_stream = use_stream

    def _payload(self, messages, temperature, max_tokens, tools) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            # NOTE: Do NOT send tool_choice — DeepSeek V4 thinking mode rejects it (400)
        payload.update(thinking_payload(self.model, self.thinking, self.effort))
        return payload

    def _headers(self) -> dict:
        return {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }

    async def _complete(self, payload: dict, on_delta=None) -> dict:
        """Send a completion (streaming if enabled). Returns final message dict."""
        url = f"{self.base_url}/v1/chat/completions"
        client = _get_client(self.base_url)
        if self.use_stream:
            stream_payload = dict(payload, stream=True)
            # Streaming: connection errors are retried before the stream starts;
            # once chunks are flowing, errors propagate to the caller.
            for attempt in range(3):
                try:
                    async with client.stream("POST", url, json=stream_payload,
                                             headers=self._headers()) as resp:
                        if resp.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                            await resp.aread()
                            await asyncio.sleep(2 ** attempt)
                            continue
                        resp.raise_for_status()
                        return await _consume_sse_stream(resp, on_delta)
                except (httpx.TransportError, httpx.TimeoutException) as e:
                    if attempt < 2:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise
        # Non-streaming path
        resp = await _post_with_retry(client, url, json_body=payload, headers=self._headers())
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        message.setdefault("usage", data.get("usage", {}))
        if on_delta and message.get("reasoning_content"):
            await on_delta("reasoning", message["reasoning_content"])
        if on_delta and message.get("content"):
            await on_delta("content", message["content"])
        return message

    async def chat(self, messages: list[dict], temperature: float | None = None, max_tokens: int = 0,
                   on_delta=None) -> str:
        """Simple chat without tool-calling. Uses instance default if max_tokens=0."""
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if temperature is None:
            temperature = self.temperature
        message = await self._complete(
            self._payload(messages, temperature, max_tokens, None), on_delta)
        thinking = message.get("reasoning_content", "")
        content_text = message.get("content", "")
        if thinking:
            return f"【思考过程】\n{thinking}\n\n【最终决策】\n{content_text}"
        return content_text

    async def chat_with_tools(
        self, messages: list[dict], tools: list[dict] | None = None,
        temperature: float | None = None, max_tokens: int = 0,
        on_delta=None,
    ) -> AIResponse:
        """Chat with OpenAI-compatible function calling (tools).
        Note: DeepSeek V4 thinking mode rejects tool_choice, so we omit it.
        """
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if temperature is None:
            temperature = self.temperature
        message = await self._complete(
            self._payload(messages, temperature, max_tokens, tools), on_delta)

        # Extract reasoning_content (DeepSeek-R1, Qwen3-thinking, etc.)
        reasoning = message.get("reasoning_content") or None

        # Extract tool_calls if present
        tool_calls = None
        raw_tool_calls = message.get("tool_calls") or []
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                try:
                    args = tc["arguments"]
                    if isinstance(args, str):
                        args = json.loads(args)
                except (json.JSONDecodeError, KeyError, TypeError):
                    args = {}
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": tc.get("name") or tc.get("function", {}).get("name", ""),
                    "arguments": args,
                })

        return AIResponse(
            content=message.get("content"),
            reasoning_content=reasoning,
            tool_calls=tool_calls,
            raw_message=message,
            usage=message.get("usage") or {},
        )
