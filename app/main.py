"""Stateless web server: the browser keeps the game; the server checks moves and runs Gemma's turn.
The server builds every prompt itself, so it cannot be used as a free general Gemma proxy."""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai_player, gemma
from .board import LETTER_VALUES, PREMIUMS, TILE_COUNTS, Placement, normalize_board, validate
from .lexicon import is_word, words

logging.basicConfig(level=logging.INFO)
STATIC = Path(__file__).resolve().parent.parent / "static"
AI_PER_MINUTE = int(os.environ.get("AI_MOVES_PER_MINUTE", "12"))

app = FastAPI(title="Lexis")
app.mount("/static", StaticFiles(directory=STATIC), name="static")
_hits: dict[str, deque] = defaultdict(deque)


@app.on_event("startup")
def _load() -> None:
    words()


class PlacementIn(BaseModel):
    r: int = Field(ge=0, le=14)
    c: int = Field(ge=0, le=14)
    letter: str = Field(min_length=1, max_length=1)
    blank: bool = False


class CheckIn(BaseModel):
    board: list[str]
    placements: list[PlacementIn] = Field(max_length=7)


class AiIn(BaseModel):
    board: list[str]
    rack: str = Field(max_length=7)
    scores: dict[str, int] = {}
    bag_left: int = Field(ge=0, le=100)
    last_human: str = Field(default="", max_length=120)
    thinking: bool = False


def _grid(rows):
    try:
        return normalize_board(rows)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def config():
    return {
        "premiums": PREMIUMS, "values": LETTER_VALUES, "counts": TILE_COUNTS,
        "providers": gemma.configured_providers(),
        "opponent": gemma.display_name(),
        "thinking_default": (os.environ.get("AI_THINKING") or os.environ.get("GEMMA_THINKING") or "0") in ("1", "true", "yes"),
    }


@app.post("/api/check")
def check(body: CheckIn):
    grid = _grid(body.board)
    pls = [Placement(p.r, p.c, p.letter.upper(), p.blank) for p in body.placements]
    if any(not p.letter.isalpha() for p in pls):
        raise HTTPException(400, "bad letter")
    res = validate(grid, pls, is_word)
    return {"ok": res.ok, "error": res.error, "score": res.score, "words": res.words}


@app.post("/api/ai-move")
async def ai_move(body: AiIn, request: Request):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    q, now = _hits[ip], time.monotonic()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= AI_PER_MINUTE:
        raise HTTPException(429, "Too many Gemma turns per minute, wait a moment.")
    q.append(now)
    rack = "".join(ch for ch in body.rack.upper() if ch.isalpha() or ch == "?")
    return await ai_player.play_turn(_grid(body.board), rack, body.scores, body.bag_left,
                                     body.last_human, body.thinking)
