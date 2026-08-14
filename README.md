# Chess Battle — AI vs AI 中国象棋

Two LLMs battle each other in Xiangqi (Chinese Chess). Each side independently configurable with any OpenAI-compatible API endpoint.

## Features

- **Agent Framework**: Models use MCP-style tools (virtual board simulation, position analysis, move submission) to make decisions
- **Long-term Memory**: Cross-turn strategic memory — plans, assessments, and opponent analysis persist across moves
- **Background Pre-analysis**: The idle side analyzes the position during the opponent's turn, giving models a head start
- **Deep Thinking**: Full model capability with thinking mode enabled, configurable time controls
- **Rich Knowledge Base**: Opening theory, middle-game tactics (10 patterns), endgame principles, checkmate patterns
- **Real-time Web Frontend**: WebSocket-powered live board, move history, timers, and debug panel

## Quick Start

```bash
cd backend
pip install httpx fastapi uvicorn
python3 -m uvicorn main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`, configure both sides' API endpoints, and start the battle.

## Architecture

```
Frontend (index.html) ←→ FastAPI + WebSocket → BattleController
                                                    ├─ Agent (ToolCallingAgent)
                                                    │   ├─ simulate_move (virtual board)
                                                    │   ├─ reset_simulation
                                                    │   └─ submit_move (final decision)
                                                    ├─ Memory (AgentMemory)
                                                    ├─ Knowledge Base (openings/tactics/endgame)
                                                    └─ Rules Engine (validation + cycle detection)
```

## Configuration

Each side independently configured via the web UI:

| Field | Example |
|-------|---------|
| Base URL | `https://api.deepseek.com` or `https://coding.dashscope.aliyuncs.com/v1` |
| Model | `deepseek-v4-flash`, `glm-5`, `kimi-k2.5` |
| API Key | Your API key |
| Time Control | 60 min base + 20s increment |

## Time Control

- Default: 60 minutes base + 20 seconds per move
- **No per-move timeout** — models think freely within total clock
- Total time exhaustion = loss

## Supported APIs

Any OpenAI-compatible API works, including:
- DeepSeek (deepseek-v4-flash, deepseek-v4-pro)
- Alibaba DashScope (glm-5, kimi-k2.5)
- OpenAI (GPT-4, GPT-4o)
- Local models via compatible endpoints

## License

MIT
