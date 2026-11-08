# Lexis: word duel vs an AI

An English crossword-tile duel against language models. Web app (FastAPI + plain JS), built for Render.
Only free models are used, and each AI turn is played by a randomly chosen one (see Providers).

## How the AI plays (up to 3 calls per turn)

The model chooses every word itself. The server never suggests words. It only:
- shows the board in a spatially explicit form: a grid, every tile listed by row **and** by column (vertical reading is what LLMs miss), and the words already on the board;
- checks the model's ideas against the rules and the ENABLE dictionary and scores them;
- if the word is legal but the coordinates are off, moves it to the nearest legal spot (`SNAP_PLACEMENT=1`, logged in the turn details). It tries the word at all 450 positions (15 x 15 x 2 directions) and keeps the closest legal one to what the model said.

Call 1: the model's move plus 2 backups. If its first choice is valid, it is played at once.
Calls 2-3 happen only when needed: the model gets the exact reason its idea failed (e.g. "I8 already has I") and fixes it, or plays one of its own playable backups. If it asks to exchange on call 1, it is asked once (with deeper reasoning) to look for a word first.
The model places a word by naming one board tile it goes through (`"through":"H9=M"`); the engine computes the cells.

## Providers (free)

| Name | Model | Notes |
|---|---|---|
| `groq` | GPT-OSS 120B | best spatial sense: places most words itself, ~2-4 s |
| `groq2` | GPT-OSS 20B | own Groq limits; good positions, more invented words |
| `google` | Gemini 3.5 Flash-Lite | fast (~1.7 s), good scores, positions mostly fixed by the engine |
| `gemma` | Gemma 4 26B (Google) | 2-9 s, good words, positions fixed by the engine; had 500/503 on some days |
| `openrouter` | Gemma 4 26B :free | only if `OPENROUTER_API_KEY` is set (~50 requests/day free) |

**Rotation** (`LLM_ROTATION=random`, default): each AI turn starts with a random provider (weights in `LLM_WEIGHTS`, e.g. `groq:3,groq2:1,google:2,gemma:2`); the rest are fallbacks. The same model is kept for all calls of a turn. The log shows which model played each move.

**Limits:** a per-minute limit with a short "try again in Xs" (up to 8 s) is waited out on the same model; longer ones (daily limits) rest that provider for the time it says, and the next one answers. Each cloud call gives up after `LLM_TIMEOUT` (30 s).

### Per-model profiles (`app/gemma.py`, `PROFILES`)

| Family | Fast (switch off) | Deep (switch on, or after an exchange request) | Why |
|---|---|---|---|
| GPT-OSS | reasoning `low`, max 2400 tokens, **no JSON mode** | `medium`, max 4500 | JSON mode made it answer "exchange" nearly every turn; Groq reserves prompt + max_tokens against 8000 tokens/min |
| Gemini | thinking `LOW`, JSON | `MEDIUM` | LOW 1.7 s; MEDIUM 8 s with better positions; HIGH 18-49 s (too slow) |
| Gemma | `MINIMAL`, max 400, JSON | plain-text self-check before the JSON | without a cap it rambled for 60-110 s |

Override any profile value with env vars named after the family, e.g. `GEMINI_FAST=MEDIUM`, `GPT_OSS_FAST_MAX=3000`, `GEMMA_TEMPERATURE=0.5`.

## Free-tier capacity
Groq per model: 1,000 requests/day, 8,000 tokens/min, 200,000 tokens/day. A GPT-OSS call uses ~2,500-3,500 tokens, so roughly 60-80 calls a day per Groq model. Gemini Flash-Lite and Gemma on Google have their own daily limits. With rotation the load is spread across all of them.

## Deploy on Render

1. Push this folder to a GitHub repo.
2. Render, New, Blueprint, pick the repo (`render.yaml` is included).
3. Set the secret env vars on Render: `GROQ_API_KEY`, `GEMINI_API_KEY` (and optionally `OPENROUTER_API_KEY`).

The browser holds the game state, so the server is stateless (free-plan restarts lose nothing). The server writes every prompt itself, so the endpoint cannot be used as a general LLM proxy. Each IP is limited to `AI_MOVES_PER_MINUTE` AI turns.

| Env var | Default | Meaning |
|---|---|---|
| `LLM_PROVIDERS` | `groq,groq2,google,gemma,openrouter,cloudflare` | providers and fallback order (unconfigured ones are skipped) |
| `LLM_ROTATION` | `random` | `off` = always start with the first provider |
| `LLM_WEIGHTS` | `groq:3,groq2:2,google:1,gemma:1,openrouter:1` | chance of each provider starting a turn |
| `GROQ_MODEL` / `GROQ_MODEL_2` | `openai/gpt-oss-120b` / `openai/gpt-oss-20b` | |
| `GOOGLE_MODEL` / `GEMMA_MODEL` | `gemini-3.5-flash-lite` / `gemma-4-26b-a4b-it` | |
| `OPENROUTER_MODEL` | `google/gemma-4-26b-a4b-it:free` | |
| `AI_NAME` | `AI` (or the model name with a single provider) | opponent name in the UI |
| `AI_THINKING` | 0 | default state of the thinking checkbox |
| `GEMMA_MAX_CALLS` | 3 | model calls per AI turn |
| `SNAP_PLACEMENT` | 1 | fix the coordinates of the model's legal words |
| `LLM_TIMEOUT` | 30 | seconds per cloud call (x2 when thinking) |

Tests: `python -m unittest discover -s tests -t .`
Dictionary: ENABLE (public domain), `data/enable1.txt`. No proper nouns; any one-word-per-line list can replace it.
