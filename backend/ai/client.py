"""Generic LLM API client compatible with OpenAI format."""
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
        _client_pools[base_url] = httpx.AsyncClient(timeout=1800)
    return _client_pools[base_url]


@dataclass
class AIResponse:
    """Structured response for tool-calling agent."""
    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[dict] | None = None
    raw_message: dict = field(default_factory=dict)
    usage: dict = field(default_factory=dict)


class AIClient:
    def __init__(self, base_url: str, model: str, api_key: str, max_tokens: int = 65536):
        # Normalize: strip trailing slashes and /v1 suffix (client appends its own /v1)
        url = base_url.rstrip('/')
        if url.endswith('/v1'):
            url = url[:-3]
        self.base_url = url
        self.model = model
        self.api_key = api_key
        self.default_max_tokens = max_tokens

    async def chat(self, messages: list[dict], temperature: float = 1.0, max_tokens: int = 0) -> str:
        """Simple chat without tool-calling. Uses instance default if max_tokens=0."""
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
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
        resp = await client.post(url, json=payload, headers=headers)
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
        temperature: float = 1.0, max_tokens: int = 0,
    ) -> AIResponse:
        if max_tokens <= 0:
            max_tokens = self.default_max_tokens
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
        resp = await client.post(url, json=payload, headers=headers)
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
