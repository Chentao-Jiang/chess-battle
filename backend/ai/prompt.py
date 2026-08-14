"""Prompt builder for Chinese chess AI with Agent framework."""
import json
from engine.board import *
from engine.notation import to_chinese
from ai.image import get_content_format, _is_multimodal


def build_legal_moves_text(board, legal_moves, side=None):
    """Format legal moves with piece names, Chinese notation, and capture labels."""
    if not legal_moves:
        return "无合法走法"
    captures = []
    movements = []
    for (fc, fr), (tc, tr) in legal_moves:
        piece = board[fr][fc]
        name = CHINESE.get(piece, '?') if piece else '?'
        target = board[tr][tc]
        tname = CHINESE.get(target, '') if target else ''
        # Compute Chinese notation for this move (already includes 吃X for captures)
        notation = to_chinese(board, fc, fr, tc, tr, side) if side else ""
        entry = f"[{fc},{fr}]→[{tc},{tr}]{name}，{notation}" if notation else f"[{fc},{fr}]→[{tc},{tr}]{name}"
        if target:
            movements.append(entry)
    parts = []
    if captures:
        parts.append(f"吃子({len(captures)}): " + " ".join(captures))
    if movements:
        parts.append(f"行棋({len(movements)}): " + " ".join(movements))
    return "\n".join(parts)


def build_json_pieces(board):
    """Piece list with side: [[col, row, chinese_char, side], ...]."""
    pieces = []
    for r in range(ROWS):
        for c in range(COLS):
            piece = board[r][c]
            if piece is not None:
                side = "红" if piece.isupper() else "黑"
                pieces.append([c, r, CHINESE.get(piece, piece), side])
    return pieces


def build_ascii_board(board):
    """Compact ASCII board for visual reference."""
    header = "   " + "".join(f" {c}" for c in range(9))
    lines = [header]
    for r in range(10):
        cells = [f"{r} "]
        for c in range(9):
            p = board[r][c]
            cells.append(f" {CHINESE.get(p, '．')}" if p else " ．")
        cells.append(f" {r}")
        lines.append("".join(cells))
    lines.append(header)
    return "\n".join(lines)


# =============================================================================
# Passive fallback prompt
# =============================================================================

def build_prompt(board, side, move_history: list = None, attempt: int = 1,
                 remaining_seconds: float = 1800, last_error: str = "",
                 legal_moves: list = None, knowledge_text: str = "",
                 memory_context: str = "", prethought: str = "", model: str = ""):
    """Minimal passive prompt. Includes board image for vision models."""
    pieces = build_json_pieces(board)
    side_name = "黑方" if side == BLACK else "红方"

    system_msg = f"""{side_name}象棋AI。从合法走法列表中凭棋感选择最优一步。

## 铁律：直接从下方合法走法列表复制坐标！禁止自行换算路数→坐标！
合法走法格式 [col,row]→[col,row]棋子名，直接选中一行复制其坐标即可。

## 坐标参考: col 0=左 8=右, row 0=黑底线 9=红底线
黑方路数: col0=1路 col1=2路 ... col8=9路
红方路数: col0=9路 col1=8路 ... col8=1路
局面JSON: [col, row, 棋子名, 红/黑]

## 棋子对照 (黑/红走法相同): 將/帥 士/仕 象/相 車/俥 馬/傌 砲/炮 卒/兵

## 输出
{{"from":[col,row],"to":[col,row],"reasoning":"理由","positional_assessment":"形势","strategic_plan":"战略"}}"""

    time_info = f"剩余{int(remaining_seconds // 60)}分{int(remaining_seconds % 60)}秒"
    if remaining_seconds < 300:
        time_info += " 速决！"

    history_text = ""
    if move_history:
        items = []
        for m in move_history:
            side_label = "红" if m['side'] == 'red' else '黑'
            fc = m.get('from', [None, None])[0] if m.get('from') else None
            if fc is not None:
                items.append(f"{m['move_num']}.{side_label}:{m['chinese']}")
            else:
                items.append(f"{m['move_num']}.{side_label}:{m['chinese']}")
        history_text = "棋谱: " + ", ".join(items) + "\n"

    legal_text = build_legal_moves_text(board, legal_moves, side) if legal_moves else ""

    blocks = [f"{side_name}。{time_info}。"]
    if memory_context:
        blocks.append(memory_context)
    if prethought:
        blocks.append(prethought)
    if knowledge_text:
        blocks.append(knowledge_text)
    blocks.append(history_text.strip())
    blocks.append(f"局面: {json.dumps(pieces, ensure_ascii=False)}")
    blocks.append(legal_text)
    blocks.append("直接从上方列表复制一行坐标到JSON输出。不要自行换算路数。")

    user_msg = "\n".join(b for b in blocks if b)
    if attempt > 1:
        user_msg = f"⚠️ {last_error} 复制合法走法坐标！\n" + user_msg

    user_content = get_content_format(model, user_msg, board) if model else user_msg
    return [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": user_content},
    ]


# =============================================================================
# Agent-mode prompts
# =============================================================================

def build_agent_system_prompt(context) -> str:
    side_name = "黑方" if context.side == BLACK else "红方"
    remaining_min = int(context.remaining_seconds / 60)
    return f"""{side_name}象棋AI。剩{remaining_min}分钟。分析后submit_move。

铁律: 直接从合法走法列表复制坐标到submit_move！禁止自行换算路数！
坐标: col0-8左→右, row0-9上→下, 黑row0-4,红row5-9。黑col0=1路,col8=9路。红col0=9路,col8=1路。
局面: [col,row,棋子,红/黑]。棋子黑/红: 將/帥士/仕象/相車/俥馬/傌砲/炮兵/卒。
走法: 車/俥直线|馬/傌日字蹩腿|砲/炮走同車吃隔子|象/相田字不过河|士/仕九宫斜一|將/帥九宫直一|卒/兵过河可横走
工具: simulate_move|reset_simulation|submit_move"""


def build_agent_user_message(context) -> str:
    import json as _json
    from ai.knowledge import select_knowledge

    pieces = build_json_pieces(context.board)
    legal_text = build_legal_moves_text(context.board, context.legal_moves, context.side)
    side_name = "黑方" if context.side == BLACK else "红方"
    time_str = f"剩{int(context.remaining_seconds // 60)}分{int(context.remaining_seconds % 60)}秒"

    history_text = ""
    if context.move_history:
        items = []
        for m in context.move_history[-8:]:
            side_label = "红" if m['side'] == 'red' else '黑'
            fc = m.get('from', [None, None])[0] if m.get('from') else None
            if fc is not None:
                items.append(f"{m['move_num']}.{side_label}:{m['chinese']}")
            else:
                items.append(f"{m['move_num']}.{side_label}:{m['chinese']}")
        history_text = " ".join(items) + "\n"

    # Pre-inject compact context so model can submit_move directly
    knowledge = select_knowledge(context.board, context.side)
    mem_ctx = ""
    if context.memory:
        mem_ctx = context.memory.to_context()
    pre = context.prethought or ""

    context_block = ""
    if mem_ctx or pre or knowledge:
        parts = []
        if mem_ctx:
            parts.append(mem_ctx)
        if pre:
            parts.append(pre)
        if knowledge:
            parts.append(knowledge[:400])
        context_block = "\n".join(parts) + "\n"

    return f"""{side_name}。{time_str}。第{context.move_count + 1}步。

{context_block}
{history_text}
局面: {_json.dumps(pieces, ensure_ascii=False)}

走法 ({len(context.legal_moves)}):
{legal_text}

选一步，直接 submit_move。"""

    if context.prethought:
        msg += f"\n【预分析】{context.prethought}"

    if context.model and _is_multimodal(context.model):
        return get_content_format(context.model, msg, context.board)
    return msg
