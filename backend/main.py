"""FastAPI entry point - REST + WebSocket for Chinese Chess AI Battle."""
import asyncio
import json
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent))

from engine.board import *
from engine.rules import *
from engine.notation import *
from game.state import GameState, GameStatus
from game.controller import BattleController

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Chinese Chess AI Battle")

frontend_dir = Path(__file__).parent.parent / "frontend"
app.mount("/static", StaticFiles(directory=str(frontend_dir)), name="static")

game = GameState()
controller = BattleController(game)

websocket_clients: list[WebSocket] = []


async def broadcast(data: dict):
    dead = []
    for ws in websocket_clients:
        try:
            await ws.send_json(data)
        except Exception:
            dead.append(ws)
    for ws in dead:
        websocket_clients.remove(ws)


@app.get("/", response_class=HTMLResponse)
async def index():
    return (frontend_dir / "index.html").read_text()


class ConfigRequest(BaseModel):
    black_base_url: str = ""
    black_model: str = ""
    black_api_key: str = ""
    red_base_url: str = ""
    red_model: str = ""
    red_api_key: str = ""
    move_delay: float = 2.0
    red_is_human: bool = False
    black_is_human: bool = False
    red_max_tokens: int = 0
    black_max_tokens: int = 0


@app.post("/api/config")
async def set_config(cfg: ConfigRequest):
    from game.state import GameConfig, auto_max_tokens
    red_mt = cfg.red_max_tokens if cfg.red_max_tokens > 0 else auto_max_tokens(cfg.red_model)
    black_mt = cfg.black_max_tokens if cfg.black_max_tokens > 0 else auto_max_tokens(cfg.black_model)
    game.config = GameConfig(
        black_base_url=cfg.black_base_url,
        black_model=cfg.black_model,
        black_api_key=cfg.black_api_key,
        red_base_url=cfg.red_base_url,
        red_model=cfg.red_model,
        red_api_key=cfg.red_api_key,
        move_delay=cfg.move_delay,
        red_is_human=cfg.red_is_human,
        black_is_human=cfg.black_is_human,
        red_max_tokens=red_mt,
        black_max_tokens=black_mt,
    )
    return {"status": "ok"}


@app.post("/api/move")
async def human_move(move: dict):
    """Submit a human player's move."""
    try:
        fc, fr = move['from']
        tc, tr = move['to']
        result = controller.submit_human_move(fc, fr, tc, tr)
        await broadcast(game.to_dict())
        return {"status": result}
    except (KeyError, TypeError):
        return {"status": "error", "message": "Invalid move format. Use {from: [c,r], to: [c,r]}"}


@app.get("/api/legal/{side}")
async def get_legal_moves(side: str):
    """Get legal moves for a side (for human player UI)."""
    from engine.rules import all_legal_moves
    if side not in ("red", "black"):
        return {"error": "Invalid side"}
    legal = all_legal_moves(game.board, side)
    return {"side": side, "moves": [[fc, fr, tc, tr] for (fc, fr), (tc, tr) in legal]}


@app.post("/api/start")
async def start_game():
    await controller.start()
    await broadcast(game.to_dict())
    return {"status": "ok"}


@app.post("/api/stop")
async def stop_game():
    await controller.stop()
    await broadcast(game.to_dict())
    return {"status": "ok"}


@app.get("/api/state")
async def get_state():
    return game.to_dict()


@app.post("/api/reset")
async def reset_game():
    await controller.stop()
    game.reset()
    await broadcast(game.to_dict())
    return {"status": "ok"}


@app.get("/api/debug/conversations/{side}")
async def get_conversations(side: str):
    """Get AI conversation history for a specific side (red or black)."""
    if side == "red":
        convs = game.red_conversations
    elif side == "black":
        convs = game.black_conversations
    else:
        return {"error": "Invalid side. Use 'red' or 'black'."}
    
    result = []
    for i, conv in enumerate(convs):
        result.append({
            "index": i,
            "messages": conv.messages,
            "response": conv.response,
            "move_result": conv.move_result,
            "timestamp": conv.timestamp,
        })
    return {"side": side, "count": len(result), "conversations": result}


@app.get("/api/debug/memory/{side}")
async def get_memory(side: str):
    """Get agent memory for a specific side (red or black)."""
    if side == "red":
        mem = game.red_memory
    elif side == "black":
        mem = game.black_memory
    else:
        return {"error": "Invalid side. Use 'red' or 'black'."}
    return {"side": side, "memory": mem.to_dict()}


@app.get("/api/debug/timers")
async def get_timers():
    """Get current timer status for both players."""
    return {
        "red": game.red_timer.to_dict(),
        "black": game.black_timer.to_dict(),
    }


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    websocket_clients.append(ws)
    try:
        await ws.send_json(game.to_dict())
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in websocket_clients:
            websocket_clients.remove(ws)
    except Exception:
        if ws in websocket_clients:
            websocket_clients.remove(ws)


async def state_broadcaster():
    while True:
        try:
            await asyncio.sleep(0.5)
            if websocket_clients:
                await broadcast(game.to_dict())
        except Exception:
            logger.error('State broadcaster error', exc_info=True)


@app.on_event("startup")
async def startup():
    asyncio.create_task(state_broadcaster())

@app.on_event("shutdown")
async def shutdown():
    from ai.client import shutdown_clients
    await shutdown_clients()
