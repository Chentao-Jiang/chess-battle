"""Generic LLM API client compatible with OpenAI format."""
import asyncio
import httpx
import json
import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Global connection pool (one per base_url to avoid cross-contamination)
_client_pools: dict[str, httpx.AsyncClient] = {}


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


@dataclass
class AIResponse:
    """Structured response for tool-calling agent."""
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict] | None = None
    raw_message: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


class AIClient:
    def __init__(self, base_url: str, model: str, api_key: str, max_tokens: int = 65536,
                 temperature: float = 0.7):
        # Normalize: strip trailing slashes and /v1 suffix (client appends its own /v1)
        url = base_url.rstrip('/')
        if url.endswith('/v1'):
            url = url[:-3]
        self.base_url = url
        self.model = model
        self.api_key = api_key
        self.default_max_tokens = max_tokens
        self.temperature = temperature

    async def chat(self, messages: list[dict], temperature: float | None = None, max_tokens: int = 0) -> str:
        """Simple chat without tool-calling. Uses instance default if max_tokens=0."""
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if temperature is None:
            temperature = self.temperature
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        client = _get_client(self.base_url)
        resp = await _post_with_retry(client, url, json_body=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]
        thinking = message.get("reasoning_content", "")
        content_text = message.get("content", "")
        if thinking:
            return f"【思考过程】\n{thinking}\n\n【最终决策】\n{content_text}"
        return content_text

    async def chat_with_tools(
        self, messages: list[dict], tools: list[dict] | None = None,
        temperature: float | None = None, max_tokens: int = 0,
    ) -> AIResponse:
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
        if temperature is None:
            temperature = self.temperature
        """Chat with OpenAI-compatible function calling (tools).
        Note: DeepSeek V4 thinking mode rejects tool_choice, so we omit it.
        """
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            # NOTE: Do NOT send tool_choice — DeepSeek V4 thinking mode rejects it (400)

        client = _get_client(self.base_url)
        resp = await _post_with_retry(client, url, json_body=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        message = data["choices"][0]["message"]

        # Extract reasoning_content (DeepSeek-R1, Qwen3-thinking, etc.)
        reasoning = message.get("reasoning_content") or None

        # Extract tool_calls if present
        tool_calls = None
        raw_tool_calls = message.get("tool_calls", [])
        if raw_tool_calls:
            tool_calls = []
            for tc in raw_tool_calls:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError, TypeError):
                    args = {}
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": tc["function"]["name"],
                    "arguments": args,
                })

        return AIResponse(
            content=message.get("content"),
            reasoning_content=reasoning,
            tool_calls=tool_calls,
            raw_message=message,
            usage=data.get("usage", {}),
        )
