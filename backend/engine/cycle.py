"""Chinese chess rule enforcement - cycle detection and forbidden moves.

Implements Asian Xiangqi Rules (亚规) for:
- 长将 (perpetual check) - 判负: one side checks continuously in a cycle
- 长捉 (perpetual chase) - 判负: one side chases/captures continuously
- 双方不变作和: both sides repeat allowed moves
"""

from engine.board import RED, BLACK
from engine.rules import is_in_check, raw_moves_for_piece, is_enemy


def is_check_move(board_after, side):
    """Check if the position after the move puts the opponent in check."""
    enemy = RED if side == BLACK else BLACK
    return is_in_check(board_after, enemy)


def is_capture_move(board_before, tr, tc):
    """Check if the move captured a piece."""
    return board_before[tr][tc] is not None


def is_threat_move(board_after, side, tc, tr):
    """Check if the moved piece creates a new threat to capture an unprotected enemy piece."""
    moves = raw_moves_for_piece(board_after, tc, tr)
    for tc2, tr2 in moves:
        piece_at_dest = board_after[tr2][tc2]
        if piece_at_dest and is_enemy(piece_at_dest, side):
            return True
    return False


def classify_move(board_before, board_after, fc, fr, tc, tr, side) -> list[str]:
    """Classify a move as: 'check', 'capture', 'threat', or 'quiet'."""
    result = []

    if is_check_move(board_after, side):
        result.append('check')

    if is_capture_move(board_before, tr, tc):
        result.append('capture')

    if is_threat_move(board_after, side, tc, tr):
        result.append('threat')

    if not result:
        result.append('quiet')

    return result


def detect_cycle(position_hash_history: list[str], last_moves: list[dict], side: str) -> dict | None:
    """Detect cycle using position hashes and classify the repeated moves.

    1. Find if current position hash appears earlier in history
    2. If repeated >= 2 times, analyze ONLY moves between first and last occurrence
    3. Apply 亚规: 长将/长捉 -> 判负, 双方不变 -> 判和
    """
    if len(position_hash_history) < 3:
        return None

    current_hash = position_hash_history[-1]

    repeats = []
    for i, h in enumerate(position_hash_history[:-1]):
        if h == current_hash:
            repeats.append(i)

    if len(repeats) < 2:
        return None

    # Use the first and last repeat indices to determine the cycle window
    first_idx = repeats[0]
    last_idx = repeats[-1]

    # Each position hash corresponds to one game loop iteration (one side's turn)
    # The number of turns in the cycle
    turns_in_cycle = last_idx - first_idx

    # Analyze ONLY moves within the cycle window
    # moves_in_cycle = total_moves - turns_in_cycle to total_moves
    cycle_moves = last_moves[-turns_in_cycle:] if len(last_moves) >= turns_in_cycle else last_moves

    side_moves = {}
    for m in cycle_moves:
        s = m['side']
        t = m['type']
        if s not in side_moves:
            side_moves[s] = []
        side_moves[s].extend(t if isinstance(t, list) else [t])

    if len(side_moves) < 2:
        return None

    red_moves = side_moves.get('red', [])
    black_moves = side_moves.get('black', [])

    # 长将: one side exclusively checks within the cycle
    if len(red_moves) >= 2 and all('check' in t for t in red_moves):
        return {'type': '长将', 'violator': 'red', 'result': '判负'}
    if len(black_moves) >= 2 and all('check' in t for t in black_moves):
        return {'type': '长将', 'violator': 'black', 'result': '判负'}

    # 长捉: one side exclusively captures or threatens within the cycle
    if len(red_moves) >= 2 and all('capture' in t or 'threat' in t for t in red_moves):
        return {'type': '长捉', 'violator': 'red', 'result': '判负'}
    if len(black_moves) >= 2 and all('capture' in t or 'threat' in t for t in black_moves):
        return {'type': '长捉', 'violator': 'black', 'result': '判负'}

    # 双方不变: both sides only making quiet moves within the cycle
    all_quiet = all('quiet' in t for t in red_moves + black_moves)
    if all_quiet and len(red_moves) >= 1 and len(black_moves) >= 1:
        return {'type': '双方不变', 'violator': None, 'result': '判和'}

    return None
