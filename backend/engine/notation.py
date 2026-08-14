"""Chinese chess notation: coordinate ↔ Chinese 记谱 conversion.

Chinese notation uses:
- Columns 1-9 from right to left (both sides use same numbering)
- Red uses Chinese numerals (一二三四五六七八九)
- Black uses Arabic numerals (123456789)
- Format: 棋子名 + 起始列 + 动作(进/退/平) + 目标描述
"""

from engine.board import *
from engine.board import _side

# Piece names for notation (use the character that distinguishes sides)
NOTATION_NAMES = {
    'r': '車', 'n': '馬', 'b': '象', 'a': '士', 'k': '將', 'c': '砲', 'p': '卒',
    'R': '俥', 'N': '傌', 'B': '相', 'A': '仕', 'K': '帥', 'C': '炮', 'P': '兵',
}

# Red uses Chinese numerals for file numbers
CHINESE_NUMS = ['', '一', '二', '三', '四', '五', '六', '七', '八', '九']


def col_to_number(col, side):
    """Convert 0-based column to file number (1-9).
    Each side numbers from own right to left:
      Red (bottom): right=col8, left=col0, so 9-col: col8=一, col7=二, col0=九
      Black (top): right=col0, left=col8, so col+1: col0=1, col1=2, col8=9
    """
    if side == RED:
        return 9 - col
    return col + 1


def _format_file(num, side):
    """Format file number: red uses 一二三, black uses 123."""
    if side == RED:
        return CHINESE_NUMS[num]
    return str(num)


def to_chinese(board, fc, fr, tc, tr, side):
    """Convert a move to Chinese notation, appending '吃X' for captures."""
    piece = board[fr][fc]
    if piece is None:
        return ""
    name = NOTATION_NAMES.get(piece, '?')
    target = board[tr][tc]
    capture = ""
    if target is not None:
        from engine.board import CHINESE
        capture = " 吃" + CHINESE.get(target, '?')

    from_col = col_to_number(fc, side)
    to_col = col_to_number(tc, side)
    from_str = _format_file(from_col, side)
    to_str = _format_file(to_col, side)

    if fr == tr:
        return f"{name}{from_str}平{to_str}{capture}"
    elif side == BLACK:
        direction = "进" if tr > fr else "退"
        if tc == fc:
            return f"{name}{from_str}{direction}{abs(tr - fr)}{capture}"
        return f"{name}{from_str}{direction}{to_str}{capture}"
    else:
        direction = "进" if tr < fr else "退"
        if tc == fc:
            return f"{name}{from_str}{direction}{abs(tr - fr)}{capture}"
        return f"{name}{from_str}{direction}{to_str}{capture}"


def to_json_board(board):
    """Convert board to JSON-serializable dict with piece positions."""
    result = {}
    for r in range(ROWS):
        for c in range(COLS):
            piece = board[r][c]
            if piece is not None:
                result[f"{c},{r}"] = {
                    'piece': piece,
                    'chinese': CHINESE.get(piece, '?'),
                    'side': _side(piece),
                }
    return result



