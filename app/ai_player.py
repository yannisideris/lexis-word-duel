"""Gemma's turn: up to 3 model calls. Gemma invents every word; the engine only checks,
scores and (optionally) corrects the coordinates of Gemma's own words."""
from __future__ import annotations

import os
import time

from . import gemma
from .board import (COLS, PREMIUMS, SIZE, cell_name, find_legal_spots, parse_cell,
                    validate, word_to_placements)
from .lexicon import is_word

MAX_CALLS = int(os.environ.get("GEMMA_MAX_CALLS", "3"))
SNAP = os.environ.get("SNAP_PLACEMENT", "1") not in ("0", "false", "no")

SYSTEM = """You play an English crossword tile game against a human. Win on points.
Grid: columns A-O (left to right), rows 1-15 (top to bottom). Cell H8 = column H, row 8.
across = left to right along one row (row stays, column letter increases).
down = top to bottom along one column (column stays, row number increases).
Rules: all new tiles in one line; the whole line word (including board letters it runs through or touches) must be a real word; every perpendicular word you create must be real; the word must touch existing tiles (first word covers H8).
Only real English dictionary words: no names, abbreviations or made-up letter strings.
Words already on the board are not moves. Every letter of your word that is not already on the board must come from your rack.
Your rack's "?" is a blank: it can be any letter. Just write the word.
Board marks on empty cells: # triple word, + double word.
Placing: "through" = one board tile your word uses, written cell=letter; the position is computed from it. If your word only touches tiles side by side, give "start" = cell of the word's first letter instead."""

BRAINSTORM = """Your move. Go for points: longer words, # and + squares, high-value letters, all 7 tiles (+50) when possible.
Put your chosen move first, then up to 2 backups.
Reply JSON: {"moves":[{"new_tiles":"<rack letters you place>","word":"<WORD>","through":"<cell>=<letter on that cell>","dir":"<across|down>"}]}
Empty board: {"moves":[{"new_tiles":"<rack letters you place>","word":"<WORD>","start":"<first letter cell, row 8 or column H>","dir":"<across|down>"}]}
If your rack is hopeless and the bag has 7+ tiles you may instead reply {"moves":[],"exchange":"LETTERS"}."""


def render_board(grid) -> str:
    lines = ["    " + " ".join(COLS)]
    for r in range(SIZE):
        cells = []
        for c in range(SIZE):
            ch = grid[r][c]
            if ch != ".":
                cells.append(ch.upper())
            else:
                cells.append({"TW": "#", "DW": "+"}.get(PREMIUMS[r][c], "."))
        lines.append(f"{r + 1:>2}  " + " ".join(cells))
    return "\n".join(lines)


def tile_index(grid) -> str:
    """Exact coordinates of every tile, by row and by column (vertical reading is the hard part)."""
    rows, cols = [], []
    for r in range(SIZE):
        t = [f"{COLS[c]}={grid[r][c].upper()}" for c in range(SIZE) if grid[r][c] != "."]
        if t:
            rows.append(f"row {r + 1}: " + " ".join(t))
    for c in range(SIZE):
        t = [f"{r + 1}={grid[r][c].upper()}" for r in range(SIZE) if grid[r][c] != "."]
        if t:
            cols.append(f"col {COLS[c]}: " + " ".join(t))
    if not rows:
        return "Board is empty: your word must cover H8."
    words_line = ", ".join(f"{w} ({where})" for w, where in board_words(grid).items())
    return f"Words on board: {words_line}\nTiles by row:\n" + "\n".join(rows) + "\nTiles by column (top to bottom):\n" + "\n".join(cols)


def board_words(grid) -> dict[str, str]:
    """Words on the board -> 'H8 down'."""
    found: dict[str, str] = {}
    for across in (True, False):
        for a in range(SIZE):
            b = 0
            while b < SIZE:
                r, c = (a, b) if across else (b, a)
                if grid[r][c] == ".":
                    b += 1
                    continue
                start, letters = (r, c), ""
                while b < SIZE:
                    r, c = (a, b) if across else (b, a)
                    if grid[r][c] == ".":
                        break
                    letters += grid[r][c].upper()
                    b += 1
                if len(letters) >= 2:
                    found[letters] = f"{cell_name(*start)} {'across' if across else 'down'}"
    return found


def leave_of(rack: str, placements) -> str:
    pool = list(rack)
    for p in placements:
        pool.remove("?" if p.blank else p.letter)
    return "".join(sorted(pool))


def check_idea(grid, rack: str, idea: dict) -> dict:
    """Validate one of Gemma's ideas. Returns a record with ok/score/error and the final placement."""
    word = "".join(ch for ch in str(idea.get("word", "")).upper() if ch.isalpha())
    d = str(idea.get("dir", "")).lower()
    across = not d.startswith("d")
    dr, dc = (0, 1) if across else (1, 0)
    starts: list[tuple[int, int]] = []
    err = "Give \"through\" (board cell=letter) or a valid \"start\" cell."
    through = str(idea.get("through") or "")
    if "=" in through:
        cell_txt, letter = through.split("=", 1)
        cell, letter = parse_cell(cell_txt), letter.strip().upper()[:1]
        if cell and grid[cell[0]][cell[1]] == ".":
            err = f"{cell_name(*cell)} is empty, it has no tile to go through."
        elif cell and grid[cell[0]][cell[1]].upper() != letter:
            err = f"{cell_name(*cell)} has {grid[cell[0]][cell[1]].upper()}, not {letter}."
        elif cell and letter not in word:
            err = f"'{word}' does not contain {letter} from {cell_name(*cell)}."
        elif cell:
            starts = [(cell[0] - dr * i, cell[1] - dc * i) for i, ch in enumerate(word) if ch == letter]
    if not starts and parse_cell(idea.get("start", "")):
        starts = [parse_cell(idea.get("start", ""))]
    start = starts[0] if starts else None
    label = cell_name(*start) if start and 0 <= start[0] < SIZE and 0 <= start[1] < SIZE else str(idea.get("start") or through or "?")
    rec = {"word": word, "start": label, "dir": "across" if across else "down", "ok": False}
    if len(word) < 2:
        rec["error"] = "Not a word."
        return rec
    if not is_word(word):
        rec["error"] = f"'{word}' is not in the dictionary."
        return rec
    if word in board_words(grid) and not starts:
        rec["error"] = f"{word} is already on the board; add tiles from your rack."
        return rec
    for (sr, sc) in starts:
        pl, err = word_to_placements(grid, word, sr, sc, across, rack)
        if pl:
            res = validate(grid, pl, is_word)
            if res.ok:
                rec.update(ok=True, result=res, start=cell_name(sr, sc))
                return rec
            err = res.error
    if SNAP:
        spots = find_legal_spots(grid, word, rack, is_word)
        if spots:
            r0, c0 = start or (7, 7)
            r, c, ac, res = min(spots, key=lambda s: (abs(s[0] - r0) + abs(s[1] - c0) + (0 if s[2] == across else 2), -s[3].score))
            rec.update(ok=True, result=res, start=cell_name(r, c), dir="across" if ac else "down",
                       corrected=f"moved from {rec['start']} {rec['dir']} ({err})")
            return rec
    rec["error"] = err
    return rec


def describe(i: int, rec: dict, rack: str) -> str:
    head = f"{i}. {rec['word']} {rec['start']} {rec['dir']}"
    if not rec["ok"]:
        return f"{head} -> INVALID: {rec['error']}"
    res = rec["result"]
    extra = [w["word"] for w in res.words[1:]]
    s = f"{head} -> valid, {res.score} pts"
    if extra:
        s += ", also forms " + ", ".join(extra)
    if rec.get("corrected"):
        s += f" (position corrected: {rec['corrected']})"
    return s + f", keeps {leave_of(rack, res.placements) or '-'}"


async def play_turn(grid, rack: str, scores: dict, bag_left: int, last_human: str, thinking: bool) -> dict:
    tokens: list[int] = []   # per call: prompt + output (incl. thinking) = context actually used
    res = await _turn(grid, rack, scores, bag_left, last_human, thinking, tokens)
    res["tokens"] = tokens
    provs = res.get("providers") or []
    res["model"] = gemma.pretty_model(provs[-1]) if provs else ""
    return res


async def _turn(grid, rack, scores, bag_left, last_human, thinking, tokens) -> dict:
    t0 = time.time()
    trace: list[str] = []
    providers: list[str] = []
    context = (
        f"{render_board(grid)}\n\n{tile_index(grid)}\n\n"
        f"Your rack: {' '.join(rack)}\n"
        f"Score: you {scores.get('ai', 0)}, human {scores.get('human', 0)}. Tiles left in bag: {bag_left}.\n"
        + (f"Human's last move: {last_human}\n" if last_human else "")
    )
    how = "Reply with the JSON only."  # with thinking on, gemma.py swaps this for a check-then-JSON note on models without built-in reasoning
    messages = [{"role": "user", "content": context + "\n" + BRAINSTORM + "\n" + how}]
    valid: list[dict] = []
    seen: set = set()
    calls = 0
    exchange = ""

    order = gemma.turn_order()   # random first pick per turn (LLM_ROTATION), the rest are fallbacks

    deeper = False   # set after an exchange request: the next call reasons more (reasoning models only)

    async def call():
        nonlocal calls, order
        calls += 1
        obj, prov = await gemma.ask_json(SYSTEM, messages, thinking=thinking or deeper, order=order)
        providers.append(prov)
        # Keep the same model for the rest of this turn so it rescues its own ideas.
        order = [prov] + [p for p in order if p != prov]
        tokens.append(sum(gemma.LAST_USAGE))
        messages.append({"role": "assistant", "content": _compact(obj)})
        return obj

    try:
        while calls < MAX_CALLS:
            obj = await call()
            ideas = obj.get("moves") if isinstance(obj.get("moves"), list) else []
            pick = obj.get("pick")
            if pick is not None and valid:
                chosen = None
                txt = "".join(ch for ch in str(pick).upper() if ch.isalnum())
                if txt.isdigit() and 1 <= int(txt) <= len(valid):
                    chosen = valid[int(txt) - 1]
                else:
                    chosen = next((v for v in valid if v["word"] == txt), None)
                if chosen:
                    trace.append(f"Picked {chosen['word']}")
                    return _play(chosen, rack, calls, providers, trace, t0)
                trace.append(f"Pick {pick!r} did not match.")
            exchange = str(obj.get("exchange") or "").upper()
            records = []
            for idea in ideas[:3]:
                if not isinstance(idea, dict):
                    continue
                rec = check_idea(grid, rack, idea)
                key = (rec["word"], rec["start"], rec["dir"])
                records.append(rec)
                if rec["ok"] and key not in seen:
                    valid.append(rec)
                seen.add(key)
            trace.append(f"Call {calls}: " + "; ".join(
                f"{r['word']} {r['start']} {r['dir']} " + ("ok" if r["ok"] else f"X ({r['error']})")
                for r in records) if records else f"Call {calls}: no move")
            # The model's own first choice is valid: play it as is.
            if records and records[0]["ok"] and ideas and records[0]["word"] == _clean(ideas[0]):
                return _play(records[0], rack, calls, providers, trace, t0)
            if not ideas and exchange:
                if calls == 1 and calls < MAX_CALLS and bag_left >= 7:
                    trace.append("Asked to exchange; asked once (with deeper reasoning) to look for a word first.")
                    deeper = gemma.native_reasoning(order[0])
                    messages.append({"role": "user", "content":
                        "An exchange scores 0 and costs your turn. If any word fits the board, send it; "
                        "otherwise send the exchange again. Same JSON format.\n" + how})
                    continue
                break
            if calls >= MAX_CALLS:
                break
            first_fail = next((r for r in records if not r["ok"]), None)
            listing = "\n".join(describe(i + 1, r, rack) for i, r in enumerate(valid))
            parts = []
            if first_fail:
                parts.append(f"Your choice {first_fail['word']} {first_fail['start']} {first_fail['dir']} cannot be played: {first_fail['error']}")
            others = [describe(0, r, rack)[3:] for r in records if not r["ok"] and r is not first_fail]
            if others:
                parts.append("Also rejected:\n" + "\n".join(others))
            if valid:
                parts.append(f"Your playable ideas:\n{listing}")
                parts.append('Fix your choice ({"moves":[...]}, first = your choice) or play one of your playable ideas ({"pick":N}).')
            else:
                parts.append("Re-read the tile coordinates and send your move again (first = your choice). Same JSON format.")
            messages.append({"role": "user", "content": "\n".join(parts) + "\n" + how})
    except gemma.GemmaError as exc:
        trace.append(f"Model error: {exc}")
        if not valid:
            return {"type": "error", "error": str(exc), "trace": trace, "calls": calls,
                    "providers": providers, "ms": int((time.time() - t0) * 1000)}

    if valid:
        trace.append("Out of calls: playing Gemma's earliest playable idea.")
        return _play(valid[0], rack, calls, providers, trace, t0)
    out = {"calls": calls, "providers": providers, "trace": trace, "ms": int((time.time() - t0) * 1000)}
    letters = [ch for ch in exchange if ch in rack]
    pool = list(rack)
    ok_ex = bag_left >= 7 and letters and all(pool.remove(ch) is None for ch in letters if ch in pool)
    if ok_ex:
        return {**out, "type": "exchange", "tiles": "".join(letters)}
    if bag_left >= 7:
        return {**out, "type": "exchange", "tiles": rack}
    return {**out, "type": "pass"}


def _clean(idea) -> str:
    return "".join(ch for ch in str(idea.get("word", "") if isinstance(idea, dict) else "").upper() if ch.isalpha())


def _compact(obj: dict) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _play(rec, rack, calls, providers, trace, t0) -> dict:
    res = rec["result"]
    if rec.get("corrected"):
        trace.append(f"Engine fixed position of {rec['word']}: {rec['corrected']}")
    return {
        "type": "play",
        "word": rec["word"], "start": rec["start"], "dir": rec["dir"],
        "score": res.score, "words": res.words,
        "placements": [{"r": p.r, "c": p.c, "letter": p.letter, "blank": p.blank} for p in res.placements],
        "corrected": bool(rec.get("corrected")),
        "calls": calls, "providers": providers, "trace": trace,
        "ms": int((time.time() - t0) * 1000),
    }
