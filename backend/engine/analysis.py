"""Static tactical analysis on top of the rules engine.

Pure functions — no side effects on the board. Used to inject concise
tactical hints into LLM prompts so the model starts from engine-verified
facts instead of re-deriving them (often wrongly) from the piece list.
"""
from engine.board import COLS, ROWS, CHINESE, is_own, is_enemy, clone, find_king, _side
from engine.rules import raw_moves_for_piece, is_in_check, is_legal_move
from engine.board import BLACK, RED

# Material values (centipawn-like, cannon valued for its influence)
PIECE_VALUE = {'k': 10000, 'r': 900, 'c': 450, 'n': 400, 'b': 200, 'a': 200, 'p': 100}


def _value(piece):
    return PIECE_VALUE.get(piece.lower(), 0) if piece else 0


def _attackers(board, tc, tr, by_side):
    """All pieces of by_side that can raw-move onto (tc, tr)."""
    result = []
    for r in range(ROWS):
        for c in range(COLS):
            p = board[r][c]
            if is_own(p, by_side):
                if (tc, tr) in raw_moves_for_piece(board, c, r):
                    result.append((c, r, p))
    return result


def _defenders(board, tc, tr, side):
    """Own pieces of `side` that can raw-move onto (tc, tr) (i.e. recapture there)."""
    return _attackers(board, tc, tr, side)


def hanging_pieces(board, side):
    """Pieces of `side` attacked by the enemy and not defended (or worth less than attacker)."""
    enemy = RED if side == BLACK else BLACK
    results = []
    for r in range(ROWS):
        for c in range(COLS):
            p = board[r][c]
            if not is_own(p, side):
                continue
            atk = _attackers(board, c, r, enemy)
            if not atk:
                continue
            dfd = _defenders(board, c, r, side)
            # Cheapest attacker vs cheapest defender exchange
            min_atk = min(_value(a[2]) for a in atk)
            min_dfd = min(_value(d[2]) for d in dfd) if dfd else 0
            if not dfd or _value(p) > min_atk or min_dfd > _value(p):
                tag = "无保护" if not dfd else "亏损交换"
                results.append({
                    'col': c, 'row': r, 'piece': p,
                    'name': CHINESE.get(p, p),
                    'value': _value(p),
                    'tag': tag,
                })
    return results


def check_moves(board, legal_moves, side):
    """Legal moves that give check to the enemy king."""
    enemy = RED if side == BLACK else BLACK
    results = []
    for (fc, fr), (tc, tr) in legal_moves:
        nb = clone(board)
        nb[tr][tc] = nb[fr][fc]
        nb[fr][fc] = None
        if is_in_check(nb, enemy):
            results.append(((fc, fr), (tc, tr)))
    return results


def capture_moves(board, legal_moves, side):
    """Legal moves that capture an enemy piece, with exchange annotation."""
    results = []
    for (fc, fr), (tc, tr) in legal_moves:
        target = board[tr][tc]
        if not target:
            continue
        enemy = RED if side == BLACK else BLACK
        nb = clone(board)
        nb[tr][tc] = nb[fr][fc]
        nb[fr][fc] = None
        # After capturing, can the enemy recapture onto (tc, tr)?
        recapture = _attackers(nb, tc, tr, enemy)
        mover = board[fr][fc]
        if recapture:
            min_rec = min(_value(a[2]) for a in recapture)
            # Net = captured value - (our mover if it gets recaptured)
            net = _value(target) - _value(mover)
            safety = f"吃{CHINESE.get(target, target)}(净{net:+d})但{CHINESE.get(recapture[0][2], '?')}可反吃" \
                if _value(mover) >= _value(target) else \
                f"吃{CHINESE.get(target, target)}净赚{_value(target) - _value(mover):+d}，被反吃仍有利"
        else:
            safety = f"白吃{CHINESE.get(target, target)}，无人可反吃"
        results.append({
            'move': ((fc, fr), (tc, tr)),
            'target': target,
            'net': _value(target) - (_value(mover) if recapture else 0),
            'note': safety,
        })
    return results


def build_tactical_hints(board, legal_moves, side, max_chars=900) -> str:
    """Concense tactical facts into a short prompt block. Empty string if nothing notable."""
    enemy = RED if side == BLACK else BLACK
    lines = []

    # Am I in check?
    if is_in_check(board, side) and legal_moves:
        lines.append(f"⚠️ 你正被将军！必须在{len(legal_moves)}个合法走法中选择能解将的走法。")

    # My hanging pieces
    hang = hanging_pieces(board, side)
    if hang:
        items = ", ".join(f"{h['name']}[{h['col']},{h['row']}]({h['tag']})" for h in hang[:6])
        lines.append(f"⚠️ 你方受威胁棋子: {items}")

    # Enemy hanging pieces (capture opportunities)
    opp_hang = hanging_pieces(board, enemy)
    if opp_hang:
        items = ", ".join(f"{h['name']}[{h['col']},{h['row']}]" for h in opp_hang[:6])
        lines.append(f"💡 对方可攻击/无保护子: {items}")

    # Capture moves worth knowing (top by net)
    caps = capture_moves(board, legal_moves, side)
    if caps:
        caps.sort(key=lambda x: -x['net'])
        top = caps[:5]
        items = []
        for cpt in top:
            (fc, fr), (tc, tr) = cpt['move']
            items.append(f"[{fc},{fr}]→[{tc},{tr}] {cpt['note']}")
        lines.append("💡 吃子走法评估: " + "; ".join(items))

    # Check moves
    checks = check_moves(board, legal_moves, side)
    if checks:
        items = ", ".join(f"[{fc},{fr}]→[{tc},{tr}]" for (fc, fr), (tc, tr) in checks[:5])
        lines.append(f"💡 可将军走法({len(checks)}): {items}")

    if not lines:
        return ""
    text = "【引擎战术提示】(由规则引擎计算，可信)\n" + "\n".join(lines)
    return text[:max_chars]


def best_fallback_move(board, legal_moves, side):
    """Engine-picked move of last resort: safest capture, else a quiet developing move.
    Used only when the LLM repeatedly fails to produce a legal move, to avoid forfeiting."""
    if not legal_moves:
        return None
    enemy = RED if side == BLACK else BLACK

    def score(m):
        (fc, fr), (tc, tr) = m
        mover = board[fr][fc]
        target = board[tr][tc]
        s = 0
        if target:
            s += 10 * _value(target)
        nb = clone(board)
        nb[tr][tc] = mover
        nb[fr][fc] = None
        if _attackers(nb, tc, tr, enemy):
            s -= _value(mover)
        if is_in_check(nb, enemy):
            s += 500
        if is_in_check(nb, side):
            s -= 10000
        return s

    return max(legal_moves, key=score)
