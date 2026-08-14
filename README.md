# Chess Battle — AI vs AI 中国象棋

Two LLMs battle each other in Xiangqi (Chinese Chess). Each side independently configurable with any OpenAI-compatible API endpoint.

## Features

- **Agent Framework**: Models use MCP-style tools (virtual board simulation, move submission) to reason before deciding — analyze → `simulate_move` lookahead → `submit_move`
- **Engine Tactical Hints**: The rules engine computes hanging pieces, capture exchange values, check threats, and recapture risks, injecting verified facts into every prompt so models start from engine-confirmed analysis
- **Thinking Mode Control**: Per-side thinking toggle (model default / on / off) and effort level (low / medium / high), automatically translated to the right parameters for each model family (`reasoning_effort`, `enable_thinking` + `thinking_budget`, `thinking: {type}`)
- **Streaming Output**: All model calls (move decisions, tool reasoning, background analysis) use SSE streaming — thinking process and content are pushed to the frontend live via WebSocket, shown in the battle log and the debug modal
- **Anti-Timeout Protection**: Automatic degradation as the clock runs low — below 2 min: low-effort thinking, no background analysis; below 45 s: skip the multi-round agent phase. Agent tool loops have a soft deadline tied to remaining time. If the model still fails to produce a legal move, the engine plays a scored fallback move instead of forfeiting
- **Long-term Memory**: Cross-turn strategic memory — plans, assessments, and opponent analysis persist across moves
- **Background Pre-analysis**: The idle side analyzes the position during the opponent's turn (streamed live), giving models a head start
- **Rich Knowledge Base**: Opening theory, middle-game tactics, endgame principles, checkmate patterns
- **Real-time Web Frontend**: WebSocket-powered live board, move history, timers, streaming thought display, and debug panel

## Quick Start

```bash
pip install -r requirements.txt   # fastapi, uvicorn, httpx, openai, pydantic
pip install pillow                # board PNG for vision models
cd backend
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Or run `./start.sh` (Git Bash / Linux / macOS). Open `http://localhost:8000`, configure both sides in the web UI, and start the battle.

## Architecture

```
Frontend (index.html) ←REST/WS→ FastAPI ─→ BattleController
                                              ├─ ToolCallingAgent (5-iteration loop, soft time budget)
                                              │   ├─ simulate_move (virtual board lookahead)
                                              │   ├─ reset_simulation
                                              │   └─ submit_move (validated final decision)
                                              │        ↘ fallback: plain prompt + JSON parsing
                                              │             ↘ last resort: engine-scored move
                                              ├─ Tactical Analysis (engine/analysis.py: hanging pieces,
                                              │   capture exchanges, checks — injected into prompts)
                                              ├─ Memory (AgentMemory, cross-turn)
                                              ├─ Knowledge Base (openings/tactics/endgame)
                                              ├─ Background Pre-analysis (streamed, off-clock)
                                              └─ Rules Engine (validation + repetition/cycle detection)
AIClient: OpenAI-compatible, SSE streaming, retry with backoff,
          thinking-mode payload per model family
```

## Configuration

Each side independently configured via the web UI:

| Field | Options / Example |
|-------|---------|
| Base URL | `https://api.deepseek.com` or `https://coding.dashscope.aliyuncs.com/v1` |
| Model | `deepseek-v4-flash`, `glm-4.6`, `qwen3-max`, `kimi-k2.5` |
| API Key | Your API key |
| Thinking Mode | 模型默认 (auto) / 开启 (on) / 关闭 (off) |
| Thinking Effort | 低 / 中 / 高 — mapped per family: OpenAI/Claude/Gemini → `reasoning_effort`; Qwen → `enable_thinking` + `thinking_budget`; GLM/DeepSeek → `thinking: {type}` |
| Human Player | Checkbox — play against the AI yourself |
| Temperature | Per-side, default 0.7 (API only) |

Config persists in browser localStorage and is auto-loaded on page open.

## Time Control

- Default: 90 minutes base + 20 seconds increment per move
- **No per-move timeout** — models think freely within the total clock
- Low-clock auto-degradation protects against timeout losses (see Features)
- Total time exhaustion = loss

## Robustness

- HTTP retries with exponential backoff on 429/5xx/network errors (streaming and non-streaming)
- Separated connect/read timeouts (30 s / 600 s); streaming keeps connections alive during long thinking
- Move parsing takes the **last** JSON decision, ignoring chain-of-thought examples; for reasoning models only the text after 【最终决策】 is parsed
- If the model answers in plain text instead of calling tools, it is nudged to use `submit_move` before falling back
- Vision models receive a rendered board PNG **with coordinate labels** (multimodal detection by model family)

## Supported APIs

Any OpenAI-compatible API works, including:
- DeepSeek (deepseek-v4-flash, deepseek-v4-pro)
- Alibaba DashScope (glm, kimi, qwen)
- OpenAI (GPT-4o, GPT-5, o-series)
- Anthropic, Gemini via compatible gateways
- Local models via compatible endpoints

## License

MIT
