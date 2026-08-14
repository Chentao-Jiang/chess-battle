"""Complete Chinese chess move validation engine."""

from engine.board import *
from engine.board import _side


def in_board(c, r):
    return 0 <= c < COLS and 0 <= r < ROWS


def in_palace(c, r, side):
    """Check if position is within the 9-palace for given side."""
    if c < 3 or c > 5:
        return False
    if side == BLACK:
        return 0 <= r <= 2
    else:
        return 7 <= r <= 9


def in_own_half(r, side):
    if side == BLACK:
        return 0 <= r <= 4
    else:
        return 5 <= r <= 9


def _count_screens(board, fc, fr, tc, tr):
    """Count pieces between (exclusive) two points on same line."""
    count = 0
    if fc == tc:  # vertical
        step = 1 if tr > fr else -1
        for r in range(fr + step, tr, step):
            if board[r][fc] is not None:
                count += 1
    elif fr == tr:  # horizontal
        step = 1 if tc > fc else -1
        for c in range(fc + step, tc, step):
            if board[fr][c] is not None:
                count += 1
    return count


def raw_moves_for_piece(board, col, row):
    """Return list of (tc, tr) valid destinations for piece at (col, row)."""
    piece = board[row][col]
    if piece is None:
        return []
    side = _side(piece)
    moves = []
    low = piece.lower()

    if low == 'r':  # 車/俥 - rook
        for dc, dr in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            c, r = col + dc, row + dr
            while in_board(c, r):
                if board[r][c] is None:
                    moves.append((c, r))
                elif is_enemy(board[r][c], side):
                    moves.append((c, r))
                    break
                else:
                    break
                c += dc
                r += dr

    elif low == 'n':  # 馬/傌 - knight
        for dc, dr, bc, br in [
            (1, 2, 0, 1), (-1, 2, 0, 1),
            (1, -2, 0, -1), (-1, -2, 0, -1),
            (2, 1, 1, 0), (2, -1, 1, 0),
            (-2, 1, -1, 0), (-2, -1, -1, 0),
        ]:
            nc, nr = col + dc, row + dr
            bc, br = col + bc, row + br
            if in_board(nc, nr) and in_board(bc, br) and board[br][bc] is None:
                if not is_own(board[nr][nc], side):
                    moves.append((nc, nr))

    elif low == 'b':  # 象/相 - elephant
        for dc, dr in [(2, 2), (2, -2), (-2, 2), (-2, -2)]:
            nc, nr = col + dc, row + dr
            ec, er = col + dc // 2, row + dr // 2  # eye position
            if in_board(nc, nr) and in_own_half(nr, side) and board[er][ec] is None:
                if not is_own(board[nr][nc], side):
                    moves.append((nc, nr))

    elif low == 'a':  # 士/仕 - advisor
        for dc, dr in [(1, 1), (1, -1), (-1, 1), (-1, -1)]:
            nc, nr = col + dc, row + dr
            if in_board(nc, nr) and in_palace(nc, nr, side):
                if not is_own(board[nr][nc], side):
                    moves.append((nc, nr))

    elif low == 'k':  # 將/帥 - king
        for dc, dr in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            nc, nr = col + dc, row + dr
            if in_board(nc, nr) and in_palace(nc, nr, side):
                if not is_own(board[nr][nc], side):
                    moves.append((nc, nr))
        # Flying general: face-to-face on same file with no screens
        enemy_k = find_king(board, RED if side == BLACK else BLACK)
        if enemy_k and enemy_k[0] == col:
            if _count_screens(board, col, row, enemy_k[0], enemy_k[1]) == 0:
                moves.append((enemy_k[0], enemy_k[1]))

    elif low == 'c':  # 砲/炮 - cannon
        for dc, dr in [(0, 1), (0, -1), (1, 0), (-1, 0)]:
            c, r = col + dc, row + dr
            jumped = False
            while in_board(c, r):
                if not jumped:
                    if board[r][c] is None:
                        moves.append((c, r))
                    else:
                        jumped = True
                else:
                    if board[r][c] is not None:
                        if is_enemy(board[r][c], side):
                            moves.append((c, r))
                        break
                c += dc
                r += dr

    elif low == 'p':  # 卒/兵 - pawn
        forward = 1 if side == BLACK else -1
        # Forward
        nc, nr = col, row + forward
        if in_board(nc, nr) and not is_own(board[nr][nc], side):
            moves.append((nc, nr))
        # Sideways after crossing river
        crossed = (side == BLACK and row >= 5) or (side == RED and row <= 4)
        if crossed:
            for dc in [-1, 1]:
                nc, nr = col + dc, row
                if in_board(nc, nr) and not is_own(board[nr][nc], side):
                    moves.append((nc, nr))

    return moves


def is_in_check(board, side):
    """Check if side's king is under attack."""
    king_pos = find_king(board, side)
    if king_pos is None:
        return True
    kc, kr = king_pos
    enemy = RED if side == BLACK else BLACK
    for r in range(ROWS):
        for c in range(COLS):
            if is_own(board[r][c], enemy):
                moves = raw_moves_for_piece(board, c, r)
                if (kc, kr) in moves:
                    return True
    return False


def is_legal_move(board, fc, fr, tc, tr, side):
    """Check if a move is legal (doesn't leave own king in check)."""
    piece = board[fr][fc]
    if piece is None or not is_own(piece, side):
        return False
    if (tc, tr) not in raw_moves_for_piece(board, fc, fr):
        return False
    # Simulate move
    new_board = clone(board)
    captured = new_board[tr][tc]
    new_board[tr][tc] = new_board[fr][fc]
    new_board[fr][fc] = None
    return not is_in_check(new_board, side)


def all_legal_moves(board, side):
    """Return all legal moves for side as list of ((fc,fr), (tc,tr))."""
    moves = []
    for r in range(ROWS):
        for c in range(COLS):
            if is_own(board[r][c], side):
                raw = raw_moves_for_piece(board, c, r)
                for tc, tr in raw:
                    if is_legal_move(board, c, r, tc, tr, side):
                        moves.append(((c, r), (tc, tr)))
    return moves
