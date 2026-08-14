"""Game state machine with time control and conversation history."""
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from engine.board import *
from engine.rules import *
from engine.notation import *
from ai.memory import AgentMemory

# Time control: 30 minutes base + 10 seconds per move
BASE_TIME_SECONDS = 90 * 60  # 60 minutes
TIME_INCREMENT = 20  # seconds per move


class GameStatus(Enum):
    WAITING = "waiting"
    RUNNING = "running"
    FINISHED = "finished"


@dataclass
class MoveRecord:
    move_num: int
    side: str
    from_pos: tuple[int, int]
    to_pos: tuple[int, int]
    chinese: str
    timestamp: float
    time_used: float = 0.0


@dataclass
class AIConversation:
    """Stores full AI conversation history for debug panel."""
    messages: list[dict] = field(default_factory=list)  # prompt messages
    response: str = ""  # AI response
    move_result: str = ""  # parsed move or error
    timestamp: float = 0.0


@dataclass
class PlayerTimer:
    """Individual player time tracking."""
    base_time: float = BASE_TIME_SECONDS
    remaining: float = BASE_TIME_SECONDS
    total_used: float = 0.0
    moves: int = 0
    last_move_time: float = 0.0
    increment: float = TIME_INCREMENT

    def use_time(self, elapsed: float):
        """Deduct time used and add increment."""
        self.remaining -= elapsed
        self.remaining += self.increment
        if self.remaining < 0:
            self.remaining = 0
        self.total_used += elapsed
        self.moves += 1
        self.last_move_time = time.time()

    def is_timeout(self) -> bool:
        return self.remaining <= 0

    def to_dict(self) -> dict:
        raw = self.remaining  # use unrounded for minute/second calculation
        return {
            'remaining': round(raw, 1),
            'total_used': round(self.total_used, 1),
            'moves': self.moves,
            'minutes': int(raw // 60),
            'seconds': int(raw % 60),
        }


@dataclass
class GameConfig:
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
    red_temperature: float = 0.7
    black_temperature: float = 0.7
    # Thinking-mode control per side: "auto" (model default) / "on" / "off"
    red_thinking: str = "auto"
    black_thinking: str = "auto"
    # Reasoning effort: "low" / "medium" / "high" (mapping is model-family dependent)
    red_effort: str = "medium"
    black_effort: str = "medium"


def auto_max_tokens(model: str) -> int:
    """Auto-detect max output tokens based on model name."""
    m = model.lower()
    if 'deepseek' in m:
        return 131072 if 'v4' in m else 65536
    if 'glm' in m:
        return 32768 if ('5' in m or '4' in m) else 16384
    if 'kimi' in m:
        return 65536 if 'k2' in m else 32768
    if 'gpt' in m:
        return 16384 if '4o' in m else 4096
    if 'claude' in m or 'sonnet' in m or 'opus' in m:
        return 8192
    if 'qwen' in m:
        return 32768 if '3' in m else 16384
    return 65536


class GameState:
    def __init__(self):
        self.board = initial_board()
        self.status = GameStatus.WAITING
        self.current_side = RED
        self.move_history: list[MoveRecord] = []
        self.move_count = 0
        self.config = GameConfig()
        self.winner: str | None = None
        self.last_thought: str = ""
        # Time control
        self.black_timer = PlayerTimer()
        self.red_timer = PlayerTimer()
        # Conversation history
        self.red_conversations: list[AIConversation] = []
        self.black_conversations: list[AIConversation] = []
        # Rule enforcement
        self.position_hash_history: list[str] = []  # for cycle detection
        self.cycle_detected = False
        self.cycle_violator: str | None = None
        # Agent memory
        self.red_memory = AgentMemory()
        self.black_memory = AgentMemory()
        self.red_prethought: str = ""
        self.black_prethought: str = ""

    def reset(self):
        self.board = initial_board()
        self.status = GameStatus.WAITING
        self.current_side = RED
        self.move_history = []
        self.move_count = 0
        self.winner = None
        self.last_thought = ""
        self.black_timer = PlayerTimer()
        self.red_timer = PlayerTimer()
        self.red_conversations = []
        self.black_conversations = []
        self.position_hash_history = []
        self.cycle_detected = False
        self.cycle_violator = None
        self.red_memory.reset()
        self.black_memory.reset()
        self.red_prethought = ""
        self.black_prethought = ""

    def record_position(self):
        """Save FEN-like hash of current board state for cycle detection (max 100)."""
        import hashlib
        # Build deterministic FEN-like string: row-by-row piece positions
        parts = []
        for r in range(ROWS):
            row_parts = []
            for c in range(COLS):
                piece = self.board[r][c]
                row_parts.append(piece if piece else '-')
            parts.append(''.join(row_parts))
        fen = '|'.join(parts) + f'|{self.current_side}'
        self.position_hash_history.append(hashlib.md5(fen.encode()).hexdigest())
        if len(self.position_hash_history) > 100:
            self.position_hash_history.pop(0)

    def add_conversation(self, side: str, conversation: AIConversation):
        if side == RED:
            self.red_conversations.append(conversation)
        else:
            self.black_conversations.append(conversation)

    def to_dict(self):
        return {
            'board': to_json(self.board),
            'status': self.status.value,
            'current_side': self.current_side,
            'move_count': self.move_count,
            'winner': self.winner,
            'last_thought': self.last_thought,
            'red_timer': self.red_timer.to_dict(),
            'black_timer': self.black_timer.to_dict(),
            'move_history': [
                {
                    'move_num': m.move_num,
                    'side': m.side,
                    'from': list(m.from_pos),
                    'to': list(m.to_pos),
                    'chinese': m.chinese,
                    'time_used': round(m.time_used, 1),
                }
                for m in self.move_history
            ],
            'rule_info': {
                'cycle_detected': self.cycle_detected,
                'cycle_violator': self.cycle_violator,
                'position_hash_count': len(self.position_hash_history),
            },
            'red_memory': self.red_memory.to_dict(),
            'black_memory': self.black_memory.to_dict(),
            'red_prethought': self.red_prethought,
            'black_prethought': self.black_prethought,
        }
