"""Chinese chess knowledge base with smart position-aware selection."""
from engine.board import *
from engine.board import _side


# =============================================================================
# Knowledge Catalog
# =============================================================================

OPENING_PRINCIPLES = """## 开局四大原则
1. 尽快出动大子（车马炮），一子不多动
2. 左右均衡出子，避免单翼作战
3. 控制中路，炮马配合抢占要点
4. 开局10-12回合完成子力部署"""

BLACK_RESPONSE_GUIDE = """## 黑方应对中炮的常见选择
- 屏风马（马8进7+马2进3）：最主流，弹性最强
- 顺炮（炮8平5）：激烈对攻，以牙还牙
- 列炮（炮2平5）：不同侧中炮对攻
- 反宫马：防守稳固，后发制人"""

RED_OPENING_GUIDE = """## 红方（先手）开局选择
- 中炮（炮二平五）：最主流，主动进攻
- 仙人指路（兵三进一/兵七进一）：灵活多变
- 飞相局：稳健，先巩固后进攻"""

MIDDLEGAME_TACTICS = """## 十大中局战术
1. **捉双**：一子同时威胁对方两子，谋子主要手段
2. **牵制**：限制对方棋子活动，以少制多
3. **抽将**：借将军之机抽吃对方子力
4. **闪击**：闪开一子露出后方攻击，形成双重威胁
5. **顿挫**：借过渡着法调形抢先
6. **串打**：用炮串打对方多子（需炮架）
7. **围困**：压缩对方子力活动空间
8. **弃子**：主动弃子换取攻势或杀势
9. **兑子**：通过兑换抢占先机或简化局面
10. **先弃后取**：弃子后通过捉双等取回子力

## 中局决策要点
- 优先寻找捉双、抽将等得子机会
- 注意子力协调，避免孤军深入
- 车占要道（巡河、骑河、过河）
- 马跳卧槽、挂角等好位
- 炮架中路或底线配合"""

CHECKMATE_PATTERNS = """## 常见杀法
### 车类：双车错、白脸将（对面笑）、大刀剜心
### 马类：卧槽马、挂角马、钓鱼马
### 炮类：重炮杀、马后炮、闷宫杀、天地炮
### 组合：铁门闩（炮镇中+车/兵封门）、三子归边、二鬼拍门"""

ENDGAME_WON = """## 例胜残局
- 单马胜单士 | 马底兵胜双士 | 马炮胜士象全
- 炮单士胜双士 | 炮高兵仕相全胜士象全
- 单车胜马双士 | 三高兵胜士象全 | 车高兵胜单车"""

ENDGAME_DRAWN = """## 例和残局
- 单马和单象 | 单车和士象全 | 车兵和炮士象全"""

ENDGAME_PRINCIPLES = """## 残局要诀
1. 优势时简化局面，避免无谓兑子
2. 劣势时寻求兑子求和
3. 兵卒价值大幅上升，过河兵可当小车
4. 将帅要占中助攻"""

KNOWLEDGE_SECTIONS = {
    "opening": {"title": "开局原则", "content": OPENING_PRINCIPLES},
    "black_response": {"title": "黑方应对", "content": BLACK_RESPONSE_GUIDE},
    "red_opening": {"title": "红方开局", "content": RED_OPENING_GUIDE},
    "tactics": {"title": "中局战术", "content": MIDDLEGAME_TACTICS},
    "checkmate": {"title": "杀法大全", "content": CHECKMATE_PATTERNS},
    "endgame_won": {"title": "例胜残局", "content": ENDGAME_WON},
    "endgame_drawn": {"title": "例和残局", "content": ENDGAME_DRAWN},
    "endgame_principles": {"title": "残局要诀", "content": ENDGAME_PRINCIPLES},
}

KNOWLEDGE_MENU = "可用知识：开局原则 | 中局战术(捉双/牵制/抽将/闪击/弃子等) | 杀法(卧槽马/马后炮/重炮/铁门闩等) | 残局(例胜/例和/要诀)"


# =============================================================================
# Position Analysis
# =============================================================================

PIECE_VALUES = {
    'r': 9, 'R': 9, 'n': 4.5, 'N': 4.5, 'c': 4.5, 'C': 4.5,
    'b': 2, 'B': 2, 'a': 2, 'A': 2, 'p': 1, 'P': 1, 'k': 0, 'K': 0,
}


def _count_material(board, side):
    """Count total material value for a side."""
    total = 0
    for r in range(10):
        for c in range(9):
            p = board[r][c]
            if p and _side(p) == side:
                val = PIECE_VALUES.get(p.lower(), 0)
                if p.lower() == 'p':
                    crossed = (side == BLACK and r >= 5) or (side == RED and r <= 4)
                    if crossed:
                        val = 2
                total += val
    return total


def _count_non_royal(board):
    """Count pieces excluding kings and advisors."""
    return sum(1 for r in range(10) for c in range(9)
               if board[r][c] and board[r][c].lower() not in 'ka')


def analyze_position(board, side):
    """Return a feature dict for knowledge selection."""
    piece_count = _count_non_royal(board)
    our_mat = _count_material(board, side)
    opponent = RED if side == BLACK else BLACK
    opponent_mat = _count_material(board, opponent)

    if piece_count >= 22:
        phase = "opening"
    elif piece_count >= 12:
        phase = "middlegame"
    else:
        phase = "endgame"

    # King safety: count enemy pieces aligned with our king
    king_col, king_row = None, None
    for r in range(10):
        for c in range(9):
            p = board[r][c]
            if p and p.lower() == 'k' and _side(p) == side:
                king_col, king_row = c, r
                break

    king_safety = 5
    if king_col is not None:
        threats = 0
        for r in range(10):
            for c in range(9):
                p = board[r][c]
                if p and _side(p) == opponent:
                    if c == king_col or r == king_row:
                        threats += 1
                    if p.lower() in 'rc' and (c == king_col or r == king_row):
                        threats += 2
        king_safety = max(0, 10 - threats)

    return {
        "phase": phase,
        "piece_count": piece_count,
        "king_safety": king_safety,
        "material_balance": our_mat - opponent_mat,
    }


def select_knowledge(board, side, memory=None) -> str:
    """Select 1-3 most relevant knowledge sections based on position features."""
    feats = analyze_position(board, side)
    phase = feats["phase"]
    sections = []

    if phase == "opening":
        sections.append(("opening", OPENING_PRINCIPLES))
        if side == RED:
            sections.append(("red_opening", RED_OPENING_GUIDE))
        else:
            sections.append(("black_response", BLACK_RESPONSE_GUIDE))

    elif phase == "middlegame":
        sections.append(("tactics", MIDDLEGAME_TACTICS))

    else:  # endgame
        sections.append(("endgame_principles", ENDGAME_PRINCIPLES))
        if feats["material_balance"] > 3:
            sections.append(("endgame_won", ENDGAME_WON))
        elif feats["material_balance"] < -3:
            sections.append(("endgame_drawn", ENDGAME_DRAWN))
        else:
            sections.append(("endgame_won", ENDGAME_WON))

    # If ahead and not in check trouble, suggest checkmate patterns
    if phase != "opening" and feats["material_balance"] > 2 and feats["king_safety"] >= 3:
        sections.append(("checkmate", CHECKMATE_PATTERNS))

    return "\n".join(
        f"## {KNOWLEDGE_SECTIONS[key]['title']}\n{content}"
        for key, content in sections
    )


def get_knowledge(board, side, move_count: int) -> str:
    """Backward-compatible wrapper. Delegates to select_knowledge."""
    return select_knowledge(board, side)
