"""Battle controller with time control, rule enforcement, and conversation tracking."""
import asyncio
import json
import logging
import re
import time

from engine.board import *
from engine.board import _side, CHINESE, is_own
from engine.rules import *
from engine.notation import *
from engine.cycle import *
from game.state import *
from game.state import auto_max_tokens
from ai.client import AIClient
from ai.prompt import *
from ai.knowledge import get_knowledge
from ai.agent import ToolCallingAgent, AgentContext, AgentResult

logger = logging.getLogger(__name__)


def parse_move_response(text: str) -> dict | None:
    """Extract from/to coordinates from AI response. Skips template/example JSONs.

    Scans matches in reverse order so JSON appearing in chain-of-thought examples
    (early in the text) is ignored in favor of the final decision (later in the text).
    """
    # For reasoning models, prefer the text after the final-decision marker if present
    if "【最终决策】" in text:
        text = text.split("【最终决策】", 1)[1]
    patterns = [
        r'```json\s*\n?(.*?)```',
        r'```\s*\n?(\{.*?\})\n?```',
        r'\{[^{}]*"from"\s*:\s*\[[^\]]*\][^{}]*"to"\s*:\s*\[[^\]]*\][^{}]*\}',
    ]
    for pattern in patterns:
        for match in reversed(list(re.finditer(pattern, text, re.DOTALL | re.IGNORECASE))):
            try:
                raw = match.group(1) if match.lastindex and match.lastindex >= 1 else match.group(0)
                data = json.loads(raw)
                fc, fr = data['from']
                tc, tr = data['to']
                # Skip template JSONs like {"from":[col,row],...}
                if not (isinstance(fc, int) and isinstance(fr, int) and isinstance(tc, int) and isinstance(tr, int)):
                    continue
                return {
                    'fc': int(fc), 'fr': int(fr), 'tc': int(tc), 'tr': int(tr),
                    'reasoning': data.get('reasoning', ''),
                    'positional_assessment': data.get('positional_assessment', ''),
                    'strategic_plan': data.get('strategic_plan', ''),
                }
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    return None


class BattleController:
    def __init__(self, state: GameState):
        self.state = state
        self._task: asyncio.Task | None = None
        self._running = False
        self._bg_tasks: dict[str, asyncio.Task] = {}
        self._human_move_event: asyncio.Event | None = None
        self._human_move_data: dict | None = None

    async def start(self):
        if self._task is not None and not self._task.done():
            logger.warning("Game loop already running")
            return
        if self.state.status in (GameStatus.WAITING, GameStatus.FINISHED):
            if self.state.status == GameStatus.FINISHED:
                self.state.reset()
            self.state.status = GameStatus.RUNNING
            self.state.last_thought = "比赛开始！红方先行。规则：每方30分钟包干制，每步+10秒。禁止着法：长将判负、长捉判负、双方不变作和。"
            self._running = True
            self._task = asyncio.create_task(self._game_loop())
            logger.info("Game started")

    async def stop(self):
        self._running = False
        for side, task in self._bg_tasks.items():
            if not task.done():
                task.cancel()
        self._bg_tasks.clear()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self.state.status = GameStatus.WAITING
        logger.info("Game stopped")

    def _update_memory_from_response(self, side, parsed: dict):
        """Update agent memory from parsed move response."""
        if not parsed:
            return
        memory = self.state.red_memory if side == RED else self.state.black_memory
        memory.update_from_response(parsed)

    def _start_background_analysis(self, side):
        """Launch background position analysis for the idle side."""
        if side in self._bg_tasks and not self._bg_tasks[side].done():
            self._bg_tasks[side].cancel()
        self._bg_tasks[side] = asyncio.create_task(self._background_analyze(side))

    async def _background_analyze(self, side):
        """Analyze the current position from side's perspective."""
        try:
            from ai.knowledge import select_knowledge
            from ai.prompt import build_json_pieces
            import json

            board_snapshot = clone(self.state.board)
            legal = all_legal_moves(board_snapshot, side)
            knowledge = select_knowledge(board_snapshot, side)

            system_msg = "你是中国象棋分析引擎。分析当前局面，不要建议具体走法。输出：威胁评估、战略建议、关键要点。200字以内。"
            pieces = build_json_pieces(board_snapshot)
            user_msg = f"""局面：
```json
{json.dumps(pieces, ensure_ascii=False)}
```
{knowledge}

分析："""

            side_name = "红方" if side == RED else "黑方"
            config = self.state.config
            base_url = config.red_base_url if side == RED else config.black_base_url
            api_key = config.red_api_key if side == RED else config.black_api_key
            model = config.red_model if side == RED else config.black_model

            if not base_url or not api_key or not model:
                return

            mt = self.state.config.red_max_tokens if side == RED else self.state.config.black_max_tokens
            if mt <= 0:
                mt = auto_max_tokens(model)
            client = AIClient(base_url, model, api_key, mt)
            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ]

            acc = []

            async def _pt_delta(kind, text):
                acc.append(text)
                if side == RED:
                    self.state.red_prethought = f"{side_name}预分析：{''.join(acc)[-300:]}"
                else:
                    self.state.black_prethought = f"{side_name}预分析：{''.join(acc)[-300:]}"

            response = await asyncio.wait_for(
                client.chat(messages, on_delta=_pt_delta), timeout=60)

            if side == RED:
                self.state.red_prethought = f"{side_name}预分析：{response[:300]}"
            else:
                self.state.black_prethought = f"{side_name}预分析：{response[:300]}"

        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning(f"Background analysis failed for {side}: {e}")

    def _collect_prethought(self, side) -> str:
        """Read and clear prethought for the current side."""
        if side == RED:
            pt = self.state.red_prethought
            self.state.red_prethought = ""
        else:
            pt = self.state.black_prethought
            self.state.black_prethought = ""
        return pt

    def submit_human_move(self, fc: int, fr: int, tc: int, tr: int) -> str:
        """Submit a human move. Returns 'ok' or error message."""
        side = self.state.current_side
        is_human = self.state.config.red_is_human if side == RED else self.state.config.black_is_human
        if not is_human:
            return "当前不是人类玩家回合"
        if self._human_move_event is None:
            return "游戏未在等待人类走棋"

        # Validate
        if not (0 <= fc < 9 and 0 <= fr < 10 and 0 <= tc < 9 and 0 <= tr < 10):
            return "坐标超出棋盘范围"

        piece = self.state.board[fr][fc]
        if piece is None:
            return "起始位置无棋子"
        if not is_own(piece, side):
            return "不能移动对方棋子"

        target = self.state.board[tr][tc]
        if target and is_own(target, side):
            return "目标位置有己方棋子"

        if target and target.lower() == 'k' and piece.lower() == 'k':
            pass  # flying general
        elif not is_legal_move(self.state.board, fc, fr, tc, tr, side):
            return "走法不符合规则"

        self._human_move_data = {'fc': fc, 'fr': fr, 'tc': tc, 'tr': tr}
        self._human_move_event.set()
        return "ok"

    async def _wait_for_human_move(self, side) -> tuple | None:
        """Block until human submits a move. Returns (fc,fr,tc,tr) or None."""
        self._human_move_event = asyncio.Event()
        self._human_move_data = None
        self.state.last_thought = f"等待{'红方' if side == RED else '黑方'}走棋..."
        await self._human_move_event.wait()
        data = self._human_move_data
        self._human_move_event = None
        if data:
            return (data['fc'], data['fr'], data['tc'], data['tr'])
        return None

    async def _game_loop(self):
        while self._running:
            try:
                side = self.state.current_side
                timer = self.state.red_timer if side == RED else self.state.black_timer
                is_human = self.state.config.red_is_human if side == RED else self.state.config.black_is_human

                # Check timeout
                if timer.is_timeout():
                    winner = RED if side == BLACK else BLACK
                    self.state.winner = winner
                    self.state.last_thought = f"{side} 超时！{'红方' if winner == RED else '黑方'} 获胜！"
                    self.state.status = GameStatus.FINISHED
                    logger.info(f"Timeout: {side} loses")
                    break

                legal = all_legal_moves(self.state.board, side)
                if not legal:
                    if is_in_check(self.state.board, side):
                        self.state.winner = RED if side == BLACK else BLACK
                        self.state.last_thought = f"将杀！{'红方' if self.state.winner == RED else '黑方'} 获胜！"
                    else:
                        self.state.winner = RED if side == BLACK else BLACK
                        self.state.last_thought = f"困毙！{'红方' if self.state.winner == RED else '黑方'} 获胜！"
                    self.state.status = GameStatus.FINISHED
                    break

                # Record position for cycle detection
                self.state.record_position()

                # Check if this side is human
                if is_human:
                    # Human move: wait for frontend input
                    move_start = time.time()
                    human_move = await self._wait_for_human_move(side)
                    move_duration = time.time() - move_start

                    if human_move is None:
                        continue  # wait again
                    fc, fr, tc, tr = human_move
                    conv = AIConversation(
                        messages=[], response=f"人类走棋 [{fc},{fr}]→[{tc},{tr}]",
                        move_result=f"({fc},{fr})→({tc},{tr})", timestamp=time.time(),
                    )
                    parsed = None
                else:
                    # AI move
                    base_url = self.state.config.red_base_url if side == RED else self.state.config.black_base_url
                    api_key = self.state.config.red_api_key if side == RED else self.state.config.black_api_key
                    model = self.state.config.red_model if side == RED else self.state.config.black_model

                    if not base_url or not api_key or not model:
                        self.state.last_thought = f"请先在配置面板填写{'红方' if side == RED else '黑方'}的 Base URL、模型和 API Key"
                        self.state.status = GameStatus.WAITING
                        await asyncio.sleep(2)
                        continue

                    client = self._side_client(side, timer)
                    move_start = time.time()

                    # Low clock: skip prethought injection and use fast single-shot mode
                    fast_mode = timer.remaining < 45
                    prethought = "" if timer.remaining < 120 else self._collect_prethought(side)
                    fc, fr, tc, tr, conv, parsed = await self._get_ai_move(
                        client, side, prethought=prethought, fast_mode=fast_mode,
                    )
                    move_duration = time.time() - move_start

                # I3: Check timeout again after API call
                if timer.is_timeout():
                    winner = RED if side == BLACK else BLACK
                    self.state.winner = winner
                    self.state.last_thought = f"{side} 超时！{'红方' if winner == RED else '黑方'} 获胜！"
                    self.state.status = GameStatus.FINISHED
                    logger.info(f'Timeout after API call: {side} loses')
                    break

                if fc is None:
                    self.state.add_conversation(side, conv)
                    self.state.last_thought = f"{'红方' if side == RED else '黑方'} 无法返回合法走法"
                    self.state.status = GameStatus.FINISHED
                    break

                self.state.add_conversation(side, conv)

                if parsed:
                    self._update_memory_from_response(side, parsed)
                self.state.last_thought = conv.response[:200] if conv.response else self.state.last_thought

                # Execute move
                piece = self.state.board[fr][fc]
                # Use piece's own side for notation (defensive: ensures correct numbering even if current_side is stale)
                piece_side = _side(piece)
                chinese = to_chinese(self.state.board, fc, fr, tc, tr, piece_side)

                board_before = clone(self.state.board)
                self.state.board[tr][tc] = piece
                self.state.board[fr][fc] = None
                board_after = clone(self.state.board)

                self.state.move_count += 1
                timer.use_time(move_duration)

                # Classify and check rules
                move_type = classify_move(board_before, board_after, fc, fr, tc, tr, side)

                last_moves = []
                for m in self.state.move_history:
                    last_moves.append({'side': m.side, 'type': getattr(m, 'move_type', ['quiet'])})
                last_moves.append({'side': side, 'type': move_type})

                cycle_result = detect_cycle(self.state.position_hash_history, last_moves, side)
                if cycle_result:
                    if cycle_result['result'] == '判负':
                        violator = cycle_result['violator']
                        winner = RED if violator == BLACK else BLACK
                        self.state.winner = winner
                        self.state.cycle_detected = True
                        self.state.cycle_violator = violator
                        self.state.last_thought = f"{cycle_result['type']}！{'红方' if violator == RED else '黑方'} 必须变着，不变判负！{'红方' if winner == RED else '黑方'} 获胜！"
                        self.state.status = GameStatus.FINISHED
                        break
                    elif cycle_result['result'] == '判和':
                        self.state.last_thought = f"双方不变作和。{cycle_result['type']}"
                        self.state.winner = 'draw'
                        self.state.status = GameStatus.FINISHED
                        break

                record = MoveRecord(
                    move_num=self.state.move_count,
                    side=side,
                    from_pos=(fc, fr),
                    to_pos=(tc, tr),
                    chinese=chinese,
                    timestamp=time.time(),
                    time_used=round(move_duration, 1),
                )
                record.move_type = move_type
                self.state.move_history.append(record)

                logger.info(f"Move {self.state.move_count}: {side} {chinese} ({move_duration:.1f}s)")

                # Switch sides
                self.state.current_side = RED if side == BLACK else BLACK

                # Launch background analysis for the idle side (skip when its clock is low)
                idle_timer = self.state.black_timer if side == RED else self.state.red_timer
                if idle_timer.remaining >= 120:
                    self._start_background_analysis(side)

                # Wait for configured delay
                await asyncio.sleep(self.state.config.move_delay)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Game loop error: {e}", exc_info=True)
                self.state.last_thought = f"错误: {str(e)}"
                self.state.status = GameStatus.WAITING
                self._running = False
                break

    def _make_delta_reporter(self, side):
        """Create an on_delta callback that streams model output into state.last_thought.

        The existing 0.5s full-state WebSocket broadcast picks this up, so the
        frontend sees thinking/content live without a new message protocol.
        """
        state = self.state
        side_name = '红方' if side == RED else '黑方'
        buf = {"reasoning": [], "content": []}
        last_emit = 0.0

        async def report(kind: str, text: str):
            nonlocal last_emit
            buf.setdefault(kind, []).append(text)
            now = time.time()
            if now - last_emit < 0.4:
                return
            last_emit = now
            parts = []
            if buf["reasoning"]:
                parts.append("🤔 " + "".join(buf["reasoning"])[-350:])
            if buf["content"]:
                parts.append("💬 " + "".join(buf["content"])[-150:])
            state.last_thought = f"[{side_name}实时] " + " | ".join(parts)

        return report

    def _side_client(self, side, timer) -> AIClient:
        """Build AIClient with per-side thinking/effort settings and low-time downgrades."""
        config = self.state.config
        base_url = config.red_base_url if side == RED else config.black_base_url
        api_key = config.red_api_key if side == RED else config.black_api_key
        model = config.red_model if side == RED else config.black_model
        mt = config.red_max_tokens if side == RED else config.black_max_tokens
        if mt <= 0:
            mt = auto_max_tokens(model)
        temp = config.red_temperature if side == RED else config.black_temperature
        thinking = config.red_thinking if side == RED else config.black_thinking
        effort = config.red_effort if side == RED else config.black_effort
        # Low clock: cut reasoning depth to avoid losing on time
        if timer.remaining < 120:
            effort = "low"
        return AIClient(base_url, model, api_key, mt, temperature=temp,
                        thinking=thinking, effort=effort)

    async def _get_ai_move(self, client: AIClient, side, max_retries=3, prethought: str = "",
                           fast_mode: bool = False):
        """Get AI move. NO per-move timeouts — only total clock matters.
        fast_mode (very low clock) skips the multi-round agent phase entirely.
        Returns (fc,fr,tc,tr,conv,parsed)."""
        timer = self.state.red_timer if side == RED else self.state.black_timer
        memory = self.state.red_memory if side == RED else self.state.black_memory
        legal_moves = all_legal_moves(self.state.board, side)
        last_error = ""
        on_delta = self._make_delta_reporter(side)

        move_history_data = [
            {'move_num': m.move_num, 'side': m.side, 'chinese': m.chinese,
             'from': list(m.from_pos), 'to': list(m.to_pos)}
            for m in self.state.move_history
        ]

        # === Phase 1: Agent with tools (primary; skipped in fast_mode) ===
        model = self.state.config.red_model if side == RED else self.state.config.black_model
        if fast_mode:
            last_error = "剩余时间过少，跳过Agent阶段直接轻量出招"
        else:
            try:
                agent_ctx = AgentContext(
                    board=self.state.board, side=side, move_count=self.state.move_count,
                    memory=memory, prethought=prethought, legal_moves=legal_moves,
                    move_history=move_history_data, remaining_seconds=timer.remaining,
                    model=model,
                )
                # Keep at least ~90s on the clock for the agent phase
                budget = max(60.0, timer.remaining - 90)
                agent = ToolCallingAgent(client=client, max_iterations=5, time_budget=budget)
                agent_result = await agent.run(agent_ctx, on_delta=on_delta)

                if agent_result.mode == "agent" and agent_result.fc is not None:
                    fc, fr, tc, tr = agent_result.fc, agent_result.fr, agent_result.tc, agent_result.tr
                    if 0 <= fc < 9 and 0 <= fr < 10 and 0 <= tc < 9 and 0 <= tr < 10:
                        conv = AIConversation(
                            messages=agent_result.messages,
                            response=f"[Agent] {agent_result.parsed}",
                            move_result=f"Agent: ({fc},{fr})→({tc},{tr})",
                            timestamp=time.time(),
                        )
                        return (fc, fr, tc, tr, conv, agent_result.parsed)
                    last_error = "Agent坐标越界"
                elif agent_result.fallback_text:
                    parsed = parse_move_response(agent_result.fallback_text)
                    if parsed:
                        fc, fr, tc, tr = parsed['fc'], parsed['fr'], parsed['tc'], parsed['tr']
                        if 0 <= fc < 9 and 0 <= fr < 10 and 0 <= tc < 9 and 0 <= tr < 10:
                            conv = AIConversation(
                                messages=agent_result.messages,
                                response=agent_result.fallback_text,
                                move_result=f"({fc},{fr})→({tc},{tr})",
                                timestamp=time.time(),
                            )
                            return (fc, fr, tc, tr, conv, parsed)
                    last_error = "Agent无法解析"
                else:
                    last_error = f"Agent: {agent_result.error or 'failed'}"
            except Exception as e:
                logger.warning(f"Agent failed: {e}")
                last_error = f"Agent异常: {str(e)[:80]}"

        # === Phase 2: Passive fallback (no tools) ===
        memory_context = memory.to_context()
        for attempt in range(1, max_retries + 1):
            messages = []
            try:
                knowledge = get_knowledge(self.state.board, side, self.state.move_count)
                messages = build_prompt(
                    self.state.board, side, move_history_data, attempt,
                    timer.remaining, last_error, legal_moves, knowledge,
                    memory_context=memory_context, prethought=prethought, model=model,
                )
                response = await client.chat(messages, on_delta=on_delta)
                last_conv = AIConversation(
                    messages=messages, response=response, timestamp=time.time(),
                )
                parsed = parse_move_response(response)
                if parsed:
                    fc, fr, tc, tr = parsed['fc'], parsed['fr'], parsed['tc'], parsed['tr']

                    # Validate bounds
                    if not (0 <= fc < 9 and 0 <= fr < 10 and 0 <= tc < 9 and 0 <= tr < 10):
                        last_error = "坐标超出棋盘范围"
                        last_conv.move_result = "坐标越界"
                        logger.warning(f"AI returned out-of-bounds move, attempt {attempt}")
                        continue

                    # Check if there's a piece at the from position
                    piece = self.state.board[fr][fc]
                    if piece is None:
                        last_error = "起始位置没有棋子"
                        last_conv.move_result = "起始位置无子"
                        logger.warning(f"AI tried to move from empty square, attempt {attempt}")
                        continue

                    # Check if piece belongs to current side
                    if not is_own(piece, side):
                        last_error = f"试图移动对方的{'红方' if side == BLACK else '黑方'}棋子"
                        last_conv.move_result = "移动对方棋子"
                        logger.warning(f"AI tried to move opponent's piece, attempt {attempt}")
                        continue

                    # Check if destination has own piece
                    target = self.state.board[tr][tc]
                    if target and is_own(target, side):
                        last_error = f"目标位置有己方棋子({CHINESE.get(target, target)})"
                        last_conv.move_result = "目标位置有己方子"
                        logger.warning(f"AI tried to capture own piece, attempt {attempt}")
                        continue

                    # Check piece-specific movement rules
                    piece_lower = piece.lower()
                    piece_name = CHINESE.get(piece, piece)
                    raw_dests = raw_moves_for_piece(self.state.board, fc, fr)
                    if (tc, tr) not in raw_dests:
                        if piece_lower == 'r':
                            blocker = None
                            if fc == tc:
                                step = 1 if tr > fr else -1
                                for rr in range(fr + step, tr, step):
                                    if self.state.board[rr][fc]:
                                        blocker = f"{CHINESE.get(self.state.board[rr][fc], '?')}在[{fc},{rr}]"
                                        break
                            else:
                                step = 1 if tc > fc else -1
                                for cc in range(fc + step, tc, step):
                                    if self.state.board[fr][cc]:
                                        blocker = f"{CHINESE.get(self.state.board[fr][cc], '?')}在[{cc},{fr}]"
                                        break
                            last_error = f"{piece_name}直线被{blocker}阻挡" if blocker else f"{piece_name}必须沿直线行走且不可越子"
                        elif piece_lower == 'n':
                            last_error = f"{piece_name}走'日'字，可能蹩马腿了"
                        elif piece_lower == 'c':
                            last_error = f"{piece_name}走棋同車（直线不可越子），吃子必须且仅跳过一个棋子"
                        elif piece_lower == 'b':
                            crossed = (side == BLACK and fr <= 4) or (side == RED and fr >= 5)
                            last_error = f"{piece_name}走'田'字，{'不能过河' if not crossed else '可能塞象眼了'}"
                        elif piece_lower == 'a':
                            last_error = f"{piece_name}只能在九宫内斜走一格"
                        elif piece_lower == 'k':
                            last_error = f"{piece_name}只能在九宫内直走一格，或尝试飞将但中间有遮挡"
                        elif piece_lower == 'p':
                            crossed = (side == BLACK and fr >= 5) or (side == RED and fr <= 4)
                            last_error = f"{piece_name}{'只能前进' if not crossed else '不能后退'}"
                        else:
                            last_error = "不符合棋子走法规则"
                        last_conv.move_result = f"违规: {last_error}"
                        logger.warning(f"AI returned illegal move for {piece_name}, attempt {attempt}")
                        continue

                    # Check if move leaves own king in check (but allow flying-general king capture)
                    if target and target.lower() == 'k' and piece.lower() == 'k':
                        # King capturing enemy king via flying general is a winning move
                        pass  # skip self-check validation
                    elif not is_legal_move(self.state.board, fc, fr, tc, tr, side):
                        last_error = "此走法会使己方将/帥处于被将军状态"
                        last_conv.move_result = "送将（自杀）"
                        logger.warning(f"AI returned move that leaves own king in check, attempt {attempt}")
                        continue

                    # Move is valid
                    last_conv.move_result = f"({fc},{fr})→({tc},{tr})"
                    return (fc, fr, tc, tr, last_conv, parsed)
                else:
                    last_error = "返回格式不是JSON"
                    last_conv.move_result = "无法解析走法"
                logger.warning(f"AI returned unparsable response (attempt {attempt}), raw: {response[:300]}")
            except Exception as e:
                logger.error(f"AI API error (attempt {attempt}): {e}", exc_info=True)
                self.state.last_thought = f"API错误: {str(e)}"
                last_error = f"API调用失败: {str(e)[:80]}"
                # Record error conversation for debug panel
                last_conv = AIConversation(
                    messages=messages,
                    response="",
                    move_result=f"API错误: {str(e)[:80]}",
                    timestamp=time.time(),
                )

        # === Phase 3: Engine-picked move of last resort (avoid forfeiting on format failures) ===
        from engine.analysis import best_fallback_move
        best = best_fallback_move(self.state.board, legal_moves, side)
        if best:
            (fc, fr), (tc, tr) = best
            piece = self.state.board[fr][fc]
            conv = AIConversation(
                messages=[],
                response=f"[引擎兜底] LLM多次未能返回合法走法，由引擎按战术评分选择: ({fc},{fr})→({tc},{tr}) {CHINESE.get(piece, '')}",
                move_result=f"引擎兜底: ({fc},{fr})→({tc},{tr})",
                timestamp=time.time(),
            )
            logger.warning(f"LLM failed {max_retries} attempts; engine fallback move ({fc},{fr})->({tc},{tr})")
            return (fc, fr, tc, tr, conv, None)
        return (None, None, None, None, last_conv, None)
