"""MCP-style tools for the Agent — wraps knowledge, memory, rules as callable tools."""
import json
from engine.board import *
from engine.board import _side, is_own
from engine.rules import all_legal_moves, is_legal_move
from ai.prompt import build_legal_moves_text


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "simulate_move",
            "description": "Execute a move on a VIRTUAL sandbox board (clone of the real board). Use to simulate sequences like 'if I play X, opponent might play Y, then I play Z'. The virtual board persists across calls within this turn. After each simulated move, the tool returns legal moves for the NEXT side — you never need to manually verify opponent moves.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2, "description": "Source [col, row] on virtual board"},
                    "to": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2, "description": "Destination [col, row] on virtual board"}
                },
                "required": ["from", "to"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reset_simulation",
            "description": "Reset the virtual board to the current real board state. Use to start fresh, discard a bad line, or return to the real position after simulation.",
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "submit_move",
            "description": "Submit the final move. MUST call this to end the turn. The move must be legal.",
            "parameters": {
                "type": "object",
                "properties": {
                    "from": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2, "description": "Source [col, row]"},
                    "to": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2, "description": "Destination [col, row]"},
                    "reasoning": {"type": "string", "description": "Tactical reason for this move"},
                    "positional_assessment": {"type": "string", "description": "Position assessment (optional)"},
                    "strategic_plan": {"type": "string", "description": "Strategic plan (optional)"}
                },
                "required": ["from", "to", "reasoning"]
            }
        }
    },
]


def execute_tool(name: str, arguments: dict, context: dict) -> str:
    """Dispatch a tool call and return the result as a JSON string."""
    try:
        if name == "simulate_move":
            return _simulate_move(arguments, context)
        elif name == "reset_simulation":
            return _reset_simulation(arguments, context)
        elif name == "submit_move":
            return _submit_move(arguments, context)
        return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)[:100]}, ensure_ascii=False)


def _submit_move(args: dict, ctx: dict) -> str:
    fc, fr = args["from"]
    tc, tr = args["to"]
    board = ctx["board"]
    side = ctx["side"]

    if not (0 <= fc < 9 and 0 <= fr < 10 and 0 <= tc < 9 and 0 <= tr < 10):
        return json.dumps({"valid": False, "error": "坐标越界"}, ensure_ascii=False)
    piece = board[fr][fc]
    if piece is None:
        return json.dumps({"valid": False, "error": "起始位置无子"}, ensure_ascii=False)
    if not is_own(piece, side):
        return json.dumps({"valid": False, "error": "不能移动对方棋子"}, ensure_ascii=False)
    target = board[tr][tc]
    if target and is_own(target, side):
        return json.dumps({"valid": False, "error": "目标有己方棋子"}, ensure_ascii=False)
    if target and target.lower() == 'k' and piece.lower() == 'k':
        pass  # flying general capture
    elif not is_legal_move(board, fc, fr, tc, tr, side):
        return json.dumps({"valid": False, "error": "走法违规"}, ensure_ascii=False)
    return json.dumps({
        "valid": True, "fc": fc, "fr": fr, "tc": tc, "tr": tr,
        "reasoning": args.get("reasoning", ""),
        "positional_assessment": args.get("positional_assessment", ""),
        "strategic_plan": args.get("strategic_plan", ""),
    }, ensure_ascii=False)


# =============================================================================
# Virtual Board Simulation Tools
# =============================================================================

def _clone_board(board):
    """Deep-copy a 10x9 board."""
    return [row[:] for row in board]


def _get_sim_state(ctx: dict) -> tuple:
    """Get or initialize virtual board state from context.
    Returns (sim_board, sim_side, sim_history, sim_count).
    """
    if "__sim_board" not in ctx or ctx["__sim_board"] is None:
        ctx["__sim_board"] = _clone_board(ctx["board"])
        ctx["__sim_side"] = ctx["side"]
        ctx["__sim_history"] = []
        ctx["__sim_count"] = 0
    return ctx["__sim_board"], ctx["__sim_side"], ctx["__sim_history"], ctx["__sim_count"]


def _side_name(side: str) -> str:
    return "黑方" if side == "black" else "红方"


def _simulate_move(args: dict, ctx: dict) -> str:
    sim_board, sim_side, sim_history, sim_count = _get_sim_state(ctx)
    fc, fr = args["from"]
    tc, tr = args["to"]

    piece = sim_board[fr][fc]
    if piece is None:
        return json.dumps({"error": f"虚拟棋盘 [{fc},{fr}] 无棋子", "virtual_side": sim_side}, ensure_ascii=False)
    if not is_own(piece, sim_side):
        side_names = {"red": "红方", "black": "黑方"}
        actual = side_names.get(_side(piece), "?")
        return json.dumps({"error": f"该棋子是{actual}的，当前虚拟棋盘轮到{_side_name(sim_side)}走", "virtual_side": sim_side}, ensure_ascii=False)

    target = sim_board[tr][tc]
    if target and is_own(target, sim_side):
        return json.dumps({"error": "目标位置有己方棋子", "virtual_side": sim_side}, ensure_ascii=False)

    # Allow flying general king capture on virtual board
    if not (target and target.lower() == 'k' and piece.lower() == 'k'):
        if not is_legal_move(sim_board, fc, fr, tc, tr, sim_side):
            return json.dumps({"error": "走法不符合规则", "virtual_side": sim_side}, ensure_ascii=False)

    # Execute move on virtual board
    piece_name = CHINESE.get(piece, piece)
    target_name = CHINESE.get(target, "") if target else ""
    sim_board[tr][tc] = piece
    sim_board[fr][fc] = None
    sim_count += 1
    ctx["__sim_count"] = sim_count

    move_desc = f"{sim_count}. {_side_name(sim_side)}: {piece_name}[{fc},{fr}]→[{tc},{tr}]"
    if target_name:
        move_desc += f" 吃{target_name}"
    sim_history.append(move_desc)
    if len(sim_history) > 10:
        sim_history.pop(0)

    # Toggle side and compute legal moves for the NEXT side
    next_side = "black" if sim_side == "red" else "red"
    ctx["__sim_side"] = next_side

    next_legal = all_legal_moves(sim_board, next_side)
    next_text = build_legal_moves_text(sim_board, next_legal, next_side)
    next_captures = []
    for (fc, fr), (tc, tr) in next_legal:
        target = sim_board[tr][tc]
        if target:
            piece = sim_board[fr][fc]
            next_captures.append(f"[{fc},{fr}]→[{tc},{tr}]{CHINESE.get(piece or '?','?')}吃{CHINESE.get(target,'')}")

    # Brief board summary for the returned state
    pieces_remaining = sum(1 for r in range(10) for c in range(9) if sim_board[r][c])

    return json.dumps({
        "move": move_desc,
        "virtual_turn": _side_name(next_side),
        "virtual_move_count": sim_count,
        "captures_available": len(next_captures),
        "top_captures": next_captures[:8],
        "legal_moves_count": len(next_legal),
        "legal_moves_summary": next_text[:1200],
    }, ensure_ascii=False)


def _reset_simulation(args: dict, ctx: dict) -> str:
    ctx["__sim_board"] = _clone_board(ctx["board"])
    ctx["__sim_side"] = ctx["side"]
    ctx["__sim_history"] = []
    ctx["__sim_count"] = 0
    return json.dumps({"status": "虚拟棋盘已重置为当前真实局面", "turn": _side_name(ctx["side"])}, ensure_ascii=False)
