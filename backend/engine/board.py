"""Chinese chess board state management.

Board is a 9x10 grid (cols 0-8, rows 0-9).
Black occupies rows 0-4, Red occupies rows 5-9.

Piece encoding: lowercase = black, uppercase = red
  r/R = 車/俥  n/N = 馬/傌  b/B = 象/相  a/A = 士/仕
  k/K = 將/帥  c/C = 砲/炮  p/P = 卒/兵
  None = empty
"""
from copy import deepcopy

COLS = 9
ROWS = 10

CHINESE = {
    'r': '車', 'n': '馬', 'b': '象', 'a': '士', 'k': '將', 'c': '砲', 'p': '卒',
    'R': '俥', 'N': '傌', 'B': '相', 'A': '仕', 'K': '帥', 'C': '炮', 'P': '兵',
}

BLACK = 'black'
RED = 'red'
BLACK_PIECES = set('rnbakcp')
RED_PIECES = set('RNBAKCP')


def _side(piece: str | None) -> str | None:
    if piece is None:
        return None
    return BLACK if piece in BLACK_PIECES else RED


def initial_board() -> list[list[str | None]]:
    board: list[list[str | None]] = [[None] * COLS for _ in range(ROWS)]
    back = ['r', 'n', 'b', 'a', 'k', 'a', 'b', 'n', 'r']
    board[0] = list(back)
    board[2][1] = 'c'
    board[2][7] = 'c'
    for col in (0, 2, 4, 6, 8):
        board[3][col] = 'p'
    board[9] = ['R', 'N', 'B', 'A', 'K', 'A', 'B', 'N', 'R']
    board[7][1] = 'C'
    board[7][7] = 'C'
    for col in (0, 2, 4, 6, 8):
        board[6][col] = 'P'
    return board


def clone(board):
    return deepcopy(board)


def to_json(board):
    return [row[:] for row in board]


def from_json(data):
    return [row[:] for row in data]


def find_king(board, side):
    target = 'k' if side == BLACK else 'K'
    for r in range(ROWS):
        for c in range(COLS):
            if board[r][c] == target:
                return (c, r)
    return None


def is_own(piece, side):
    if piece is None:
        return False
    return (side == RED and piece in RED_PIECES) or (side == BLACK and piece in BLACK_PIECES)


def is_enemy(piece, side):
    if piece is None:
        return False
    return (side == RED and piece in BLACK_PIECES) or (side == BLACK and piece in RED_PIECES)
