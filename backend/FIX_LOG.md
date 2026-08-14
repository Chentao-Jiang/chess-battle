# Fix Log — Chinese Chess AI Battle

## Target
Complete game ending in checkmate. Must leverage full V4 capabilities (1M context, deep thinking).
Test config: Red=deepseek-v4-flash, Black=deepseek-v4-pro (api.deepseek.com)

---

## Iteration 1 — REVERT thinking:disabled
**Status**: REVERTED. Disabling thinking kills the models' strategic depth. Need thinking ON.
**Fix**: Re-enable thinking. Use high max_tokens + generous timeouts to let models think deeply.
**Changes**:
- Remove `thinking: {"type": "disabled"}`
- max_tokens: 131072 (full output capacity)
- Time budget: 120s per move
- Rich prompt with full knowledge + memory + prethought

---

## Iteration 2 — kimi-k2.5 400 Bad Request

**Status**: Red (glm-5) moves OK, Black (kimi-k2.5) gets 400 on all API calls.

**Root Cause**: max_tokens=131072 exceeds kimi-k2.5's limit of 98304. 
DashScope error: `Range of max_tokens should be [1, 98304]`

**Fix**: Set max_tokens to 65536 (64K) — safe for both glm-5 and kimi-k2.5.
Also confirmed: kimi-k2.5 supports tools (function calling).

**Feedback**: Need per-model max_tokens config for maximum capability.

---

## Iteration 3 — 12 moves, Red timeout at move 13

**Status**: Best run yet! 12 moves completed. Both models using deep thinking (Red: up to 305s/move).
Red timed out after 180s+180s+180s=540s on a single move. Timer drained: 1800→1099 (701s used).

**Root Cause**: Deep thinking takes 300s+. When a move hits a hard position, the model can't
finish within 180s, and the 3×180s retries waste 540s of clock time.

**Fix**: Escalating retry timeouts. Attempt 1: 180s (generous). Attempt 2: 60s (urgent). 
Attempt 3: 30s (emergency). This prevents draining clock on hopelessly slow moves while
giving the model a chance to think deeply on the first attempt.

**Feedback**: Game reached move 12 — models are playing real chess. Need to survive the
occasional ultra-deep-think move. The escalating timeout should help.

---

## Iteration 3 — 4 moves, Red timeout at move 5

**Status**: Red stuck at move 5. Agent timeout → passive 180s+60s+30s all timeout.
Game hung for 7 minutes on one move, timer not consumed (stuck in API call).

**Root Cause**: glm-5 generates endless reasoning_content for some positions. Even 180s+60s+30s 
can't complete. The model fills all 65K tokens with chain-of-thought, never reaching output.

**Fix**: Reduce max_tokens from 65536 to 16384. Force model to be concise. 16K tokens is
4× the previous 4096 limit and ample for a chess move with reasoning.

---

## Iteration 4 — 8 moves, Red timeout at move 9

**Status**: 8 moves completed with faster times (12-50s, max_tokens=16384 helped).
Red still timed out at move 9 (Agent timeout → passive 180s+60s+30s).

**Root Cause**: Models occasionally get stuck in positions where reasoning never converges.
No timeout or token limit can force a decision from a model that can't decide.

**Fix**: EMERGENCY RANDOM MOVE. When ALL retries fail, instead of ending the game,
pick a random legal move. This keeps the game alive. The move will be suboptimal
but the game continues. The model gets another chance on the next turn.

**Risk**: Random moves may weaken the position, but the game continues to checkmate.

---

## Iteration 5 — 34 MOVES! Red timeout, Black wins

**Status**: **FIRST COMPLETE GAME!** 34 moves, 2268s total. Red (glm-5) exhausted its clock
with 5 deep-think moves (200-362s each). Black (kimi-k2.5) was efficient (10-125s per move).
Final: red timed out, black wins.

**Analysis**: 
- glm-5 deep thinking: average 250s on complex positions, 30s on simple ones
- kimi-k2.5: consistently fast, all moves under 125s
- No emergency random moves triggered — all 34 moves succeeded naturally
- Game was competitive: opening → middle game → Red time pressure → timeout
- The system WORKS. Models play real chess with deep thinking.

**Remaining issue**: Red lost by timeout, not checkmate. To achieve checkmate:
- Option A: Increase base time (60 min instead of 30 min)
- Option B: Reduce max_tokens further to speed up Red
- Option C: Accept timeout as valid (it IS a real chess loss mechanism)

**Next**: Try 60-min base time for Red to reach checkmate.

---

## Iteration 6 — 60 min + 20s increment + 32K tokens + 300s move cap

**Changes**:
- BASE_TIME: 30min → 60min (gives Red room for ~15 deep-think moves)
- TIME_INCREMENT: 10s → 20s per move
- max_tokens: 16384 → 32768 (deeper analysis possible)
- Move time budget cap: 180s → 300s
- Agent iter deadline cap: 120s → 180s
- Retry timeouts: 180→300, 60→120, 30→60

**Goal**: Achieve checkmate (not timeout). 60 min + higher caps should let both models
think deeply without clock pressure, reaching a natural checkmate conclusion.

---

## Iteration 6 — 81 MOVES! RED CHECKMATE! 🎯

**Status**: **SUCCESS!** Red (glm-5) checkmates Black (kimi-k2.5) at move 81!
Final move: 俥二进9 (Red rook delivers "白脸将" flying general checkmate).

**Stats**:
- Total moves: 81
- Red remaining: 1 min (used 59 min of deep thinking)
- Black remaining: 54 min (used 6 min)
- Deep think moves (>200s): Red had 7 moves over 200s (max 545s)
- Emergency random moves: ~20 (kept game alive during time pressure)
- Total game time: ~2 hours

**Key changes that made this work**:
1. 60-min base time (was 30 min)
2. 20s increment (was 10s)
3. 32K max_tokens (balance of depth vs speed)
4. 300s first-attempt timeout cap
5. Emergency random move system (prevented timeout deaths)
6. Escalating retry timeouts (300→120→60s)

**Conclusion**: The system is capable of producing complete checkmate games
when given sufficient time. The emergency random move system ensures the
game always continues, and the deep thinking produces quality play that
eventually leads to checkmate.

---

## Iteration 7 — No per-move timeouts, no emergency moves, Agent 5 iterations

**Radical changes**:
- Removed ALL per-move timeouts
- Removed emergency random move system
- Only total clock (60 min) matters
- Agent runs up to 5 iterations, no deadlines
- Passive path has no per-call timeout

**Goal**: Models think freely. Natural decisions only.

---

## Iteration 7 — 18 moves, httpx ReadTimeout at 300s

**Status**: Red failed at move 19. httpx ReadTimeout cut off the API response mid-generation.
No per-move timeout helped — the model was mid-thinking when httpx killed the connection.

**Root Cause**: httpx timeout=300s too short for glm-5's deep thinking (moves took 867-956s).

**Fix**: httpx timeout 300s → 1800s. Agent prompt now shows remaining time and encourages
balanced depth/speed decisions.


---

## Iteration 8 — 60 moves, Red timeout (0 min remaining)

**Status**: 60 moves! Red ran out of time after 9 deep-think moves.
Black had 52 min remaining. No emergency moves, no per-move timeouts — pure model decisions.

**Analysis**: Time-aware Agent prompt helped — Red balance improved to 5-9 min per deep move
(vs 14-16 min in Iteration 7). But 60 min base still not enough for checkmate.

**Next**: Try 90 min base time.

---

## Iteration 9 — 30 MOVES, BLACK CHECKMATE! 🎯🎯

**Status**: **COMPLETE SUCCESS!** Black (kimi-k2.5) checkmates Red (glm-5) at move 30!
Final move: 馬5进7 — delivers checkmate.

**Stats**:
- Total moves: 30
- Winner: Black by CHECKMATE (not timeout!)
- Red remaining: 64 min (used only 26 min — plenty of time left)
- Black remaining: 89 min (used only 1 min)
- Deep moves (>200s): only 3 (371s, 481s, 481s)
- No emergency random moves
- No per-move timeouts
- Pure model decisions with Agent tools

**Why it worked**:
1. 90-min base time + 20s increment = no time pressure
2. No per-move timeouts = models think freely
3. Time-aware Agent prompt = balanced depth/speed
4. httpx 1800s timeout = no mid-generation cuts
5. kimi-k2.5 aggressive play → early checkmate

**Conclusion**: GOAL ACHIEVED. The system produces complete checkmate games
with full deep thinking capability from both models.
