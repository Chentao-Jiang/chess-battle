"""Tool-calling Agent with per-iteration deadlines."""
import asyncio
import json
import time
import logging
from dataclasses import dataclass, field

from ai.client import AIClient, AIResponse
from ai.tools import TOOL_SCHEMAS, execute_tool
from ai.prompt import build_agent_system_prompt, build_agent_user_message

logger = logging.getLogger(__name__)


@dataclass
class AgentContext:
    board: list
    side: str
    move_count: int
    memory: object
    prethought: str
    legal_moves: list
    move_history: list
    remaining_seconds: float
    model: str = ""


@dataclass
class AgentResult:
    mode: str = ""
    fc: int | None = None
    fr: int | None = None
    tc: int | None = None
    tr: int | None = None
    parsed: dict | None = None
    fallback_text: str | None = None
    error: str | None = None
    messages: list = field(default_factory=list)


class ToolCallingAgent:
    """Manages tool-calling conversation loop. No per-call timeouts — model thinks freely."""

    def __init__(self, client: AIClient, max_iterations: int = 5, time_budget: float = 99999):
        self.client = client
        self.max_iterations = max_iterations
        self.time_budget = time_budget

    async def run(self, context: AgentContext, on_delta=None) -> AgentResult:
        messages = [
            {"role": "system", "content": build_agent_system_prompt(context)},
            {"role": "user", "content": build_agent_user_message(context)},
        ]

        start = time.time()
        urged = False
        for iteration in range(self.max_iterations):
            # Soft deadline: past budget, urge immediate submission (once), then bail
            if self.time_budget and time.time() - start > self.time_budget:
                if urged or iteration > 0:
                    break
                urged = True
                messages.append({
                    "role": "user",
                    "content": "⏰ 时间紧张！请立即调用 submit_move 提交当前最佳走法，不要再推演。",
                })

            try:
                response: AIResponse = await self.client.chat_with_tools(
                    messages, TOOL_SCHEMAS, on_delta=on_delta,
                )
            except Exception as e:
                err_msg = str(e)
                if "400" in err_msg or "Bad Request" in err_msg:
                    return AgentResult(mode="fallback", fallback_text=None, error="tools unsupported")
                logger.error(f"Agent API error (iter {iteration}): {e}")
                return self._fallback(messages, f"API error: {e}")

            if response.tool_calls:
                assistant_msg = {
                    "role": "assistant",
                    "content": response.content or "",
                }
                if response.reasoning_content:
                    assistant_msg["reasoning_content"] = response.reasoning_content
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.get("id") or f"call_{iteration}_{i}",
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                        }
                    }
                    for i, tc in enumerate(response.tool_calls)
                ]
                messages.append(assistant_msg)

                submit_result = None
                for tc in response.tool_calls:
                    result_str = execute_tool(tc["name"], tc["arguments"], {
                        "board": context.board,
                        "side": context.side,
                        "memory": context.memory,
                        "legal_moves": context.legal_moves,
                        "move_history": context.move_history,
                        "remaining_seconds": context.remaining_seconds,
                    })
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", f"call_{iteration}"),
                        "content": result_str,
                    })
                    if tc["name"] == "submit_move":
                        try:
                            submit_result = json.loads(result_str)
                        except json.JSONDecodeError:
                            pass

                if submit_result and submit_result.get("valid"):
                    return AgentResult(
                        mode="agent",
                        fc=submit_result["fc"], fr=submit_result["fr"],
                        tc=submit_result["tc"], tr=submit_result["tr"],
                        parsed=submit_result,
                        messages=messages,
                    )

            elif response.content:
                # Model replied with plain text instead of calling tools.
                # Nudge it once to use submit_move instead of dropping to fallback.
                messages.append({
                    "role": "assistant",
                    "content": response.content,
                })
                messages.append({
                    "role": "user",
                    "content": "请通过调用 submit_move 工具提交你的最终走法（坐标从合法走法列表复制）。如需推演可先调用 simulate_move。",
                })
                if iteration >= self.max_iterations - 1:
                    return AgentResult(
                        mode="fallback",
                        fallback_text=response.content,
                        messages=messages,
                    )

        return self._fallback(messages, "max iterations reached")

    def _fallback(self, messages, reason) -> AgentResult:
        for msg in reversed(messages):
            if msg["role"] == "assistant" and msg.get("content"):
                return AgentResult(
                    mode="exhausted", fallback_text=msg["content"],
                    error=reason, messages=messages,
                )
        return AgentResult(mode="exhausted", error=reason, messages=messages)
