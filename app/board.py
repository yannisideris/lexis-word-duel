"""Board geometry, tile values and the single move validator used for both players.

Board format (shared with the browser): list of 15 strings of 15 chars.
  "."  empty
  "A"  normal tile
  "a"  blank tile showing letter A (scores 0)
"""
from __future__ import annotations

from dataclasses import dataclass, field

SIZE = 15
CENTER = (7, 7)
COLS = "ABCDEFGHIJKLMNO"

LETTER_VALUES = {
    "A": 1, "B": 3, "C": 3, "D": 2, "E": 1, "F": 4, "G": 3, "H": 3, "I": 1,
    "J": 8, "K": 4, "L": 1, "M": 3, "N": 1, "O": 1, "P": 3, "Q": 10, "R": 1,
    "S": 1, "T": 1, "U": 2, "V": 5, "W": 4, "X": 8, "Y": 3, "Z": 10, "?": 0,
}

TILE_COUNTS = {
    "A": 9, "B": 2, "C": 2, "D": 4, "E": 11, "F": 2, "G": 3, "H": 3, "I": 8,
    "J": 1, "K": 1, "L": 4, "M": 2, "N": 6, "O": 8, "P": 2, "Q": 1, "R": 6,
    "S": 5, "T": 6, "U": 4, "V": 2, "W": 2, "X": 1, "Y": 2, "Z": 1, "?": 2,
}

# One quadrant (row, col <= 7); mirrored to the whole board. Symmetric on the diagonal.
_Q = {
    "TW": {(0, 0), (0, 5), (5, 0)},
    "DW": {(1, 1), (2, 6), (6, 2), (4, 4), (7, 1), (1, 7), (7, 7)},
    "TL": {(2, 4), (4, 2), (6, 6)},
    "DL": {(0, 3), (3, 0), (1, 4), (4, 1), (3, 6), (6, 3), (5, 5), (3, 7), (7, 3), (2, 2)},
}


def premium(r: int, c: int) -> str:
    q = (min(r, 14 - r), min(c, 14 - c))
    for kind, cells in _Q.items():
        if q in cells:
            return kind
    return ""


PREMIUMS = [[premium(r, c) for c in range(SIZE)] for r in range(SIZE)]


def cell_name(r: int, c: int) -> str:
    return f"{COLS[c]}{r + 1}"


def parse_cell(text: str) -> tuple[int, int] | None:
    text = str(text or "").strip().upper()
    if len(text) < 2 or text[0] not in COLS or not text[1:].isdigit():
        return None
    r, c = int(text[1:]) - 1, COLS.index(text[0])
    return (r, c) if 0 <= r < SIZE else None


def normalize_board(rows) -> list[list[str]]:
    if not isinstance(rows, list) or len(rows) != SIZE:
        raise ValueError("board must have 15 rows")
    grid = []
    for row in rows:
        row = str(row)
        if len(row) != SIZE or any(not (ch == "." or ch.isalpha() and ch.isascii()) for ch in row):
            raise ValueError("bad board row")
        grid.append(list(row))
    return grid


def is_empty_board(grid) -> bool:
    return all(ch == "." for row in grid for ch in row)


@dataclass
class Placement:
    r: int
    c: int
    letter: str          # uppercase letter shown
    blank: bool = False


@dataclass
class MoveResult:
    ok: bool
    error: str = ""
    score: int = 0
    words: list[dict] = field(default_factory=list)   # [{"word","score","start","dir"}]
    placements: list[Placement] = field(default_factory=list)


def _value(ch: str) -> int:
    return 0 if ch.islower() else LETTER_VALUES.get(ch, 0)


def validate(grid, placements: list[Placement], is_word) -> MoveResult:
    """Full rules check + scoring. Error strings are short and concrete (they go to Gemma)."""
    if not placements:
        return MoveResult(False, "No tiles placed.")
    seen = set()
    for p in placements:
        if not (0 <= p.r < SIZE and 0 <= p.c < SIZE):
            return MoveResult(False, "A tile is off the board.")
        if (p.r, p.c) in seen:
            return MoveResult(False, f"Two tiles on {cell_name(p.r, p.c)}.")
        seen.add((p.r, p.c))
        if grid[p.r][p.c] != ".":
            return MoveResult(False, f"{cell_name(p.r, p.c)} is already occupied by {grid[p.r][p.c].upper()}.")

    rows = {p.r for p in placements}
    cols = {p.c for p in placements}
    if len(rows) > 1 and len(cols) > 1:
        return MoveResult(False, "Tiles must be in one row or one column.")
    if len(placements) == 1:
        p = placements[0]
        horiz_neighbor = any(0 <= p.c + d < SIZE and grid[p.r][p.c + d] != "." for d in (-1, 1))
        vert_neighbor = any(0 <= p.r + d < SIZE and grid[p.r + d][p.c] != "." for d in (-1, 1))
        across = horiz_neighbor or not vert_neighbor
    else:
        across = len(rows) == 1

    tmp = [row[:] for row in grid]
    for p in placements:
        tmp[p.r][p.c] = p.letter.lower() if p.blank else p.letter.upper()
    new_cells = {(p.r, p.c) for p in placements}

    # Contiguity along the main line.
    if across:
        r = placements[0].r
        lo, hi = min(cols), max(cols)
        if any(tmp[r][c] == "." for c in range(lo, hi + 1)):
            return MoveResult(False, "Gap in the word: tiles must be contiguous (existing tiles may fill gaps).")
    else:
        c = placements[0].c
        lo, hi = min(rows), max(rows)
        if any(tmp[r][c] == "." for r in range(lo, hi + 1)):
            return MoveResult(False, "Gap in the word: tiles must be contiguous (existing tiles may fill gaps).")

    first_move = is_empty_board(grid)
    if first_move:
        if CENTER not in new_cells:
            return MoveResult(False, "First word must cover the center square H8.")
        if len(placements) < 2:
            return MoveResult(False, "First word needs at least 2 letters.")
    else:
        touches = any(
            0 <= p.r + dr < SIZE and 0 <= p.c + dc < SIZE and grid[p.r + dr][p.c + dc] != "."
            for p in placements for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1))
        )
        if not touches:
            return MoveResult(False, "Word must connect to tiles already on the board.")

    def read_word(r, c, dr, dc):
        while 0 <= r - dr < SIZE and 0 <= c - dc < SIZE and tmp[r - dr][c - dc] != ".":
            r, c = r - dr, c - dc
        start = (r, c)
        cells = []
        while 0 <= r < SIZE and 0 <= c < SIZE and tmp[r][c] != ".":
            cells.append((r, c))
            r, c = r + dr, c + dc
        return start, cells

    def score_cells(cells):
        total, mult = 0, 1
        for (r, c) in cells:
            v = _value(tmp[r][c])
            if (r, c) in new_cells:
                kind = PREMIUMS[r][c]
                if kind == "DL":
                    v *= 2
                elif kind == "TL":
                    v *= 3
                elif kind == "DW":
                    mult *= 2
                elif kind == "TW":
                    mult *= 3
            total += v
        return total * mult

    words = []
    main_d = (0, 1) if across else (1, 0)
    p0 = placements[0]
    start, cells = read_word(p0.r, p0.c, *main_d)
    if len(cells) >= 2:
        words.append((start, main_d, cells))
    cross_d = (1, 0) if across else (0, 1)
    for p in placements:
        s, cs = read_word(p.r, p.c, *cross_d)
        if len(cs) >= 2:
            words.append((s, cross_d, cs))
    if not words:
        return MoveResult(False, "A word must be at least 2 letters.")

    out, total = [], 0
    for (s, d, cs) in words:
        w = "".join(tmp[r][c].upper() for r, c in cs)
        if not is_word(w):
            where = cell_name(*s) + (" across" if d == (0, 1) else " down")
            return MoveResult(False, f"'{w}' ({where}) is not in the dictionary.")
        sc = score_cells(cs)
        total += sc
        out.append({"word": w, "score": sc, "start": cell_name(*s), "dir": "across" if d == (0, 1) else "down"})
    if len(placements) == 7:
        total += 50
    return MoveResult(True, "", total, out, placements)


def word_to_placements(grid, word: str, r: int, c: int, across: bool, rack: str):
    """Lay Gemma's word on the line. Returns (placements, error). Uses blanks for missing letters."""
    word = word.upper()
    dr, dc = (0, 1) if across else (1, 0)
    er, ec = r + dr * (len(word) - 1), c + dc * (len(word) - 1)
    if not (0 <= r < SIZE and 0 <= c < SIZE and 0 <= er < SIZE and 0 <= ec < SIZE):
        return None, f"'{word}' from {cell_name(r, c)} {'across' if across else 'down'} runs off the board."
    pool = list(rack.upper())
    placements = []
    for i, ch in enumerate(word):
        rr, cc = r + dr * i, c + dc * i
        existing = grid[rr][cc]
        if existing != ".":
            if existing.upper() != ch:
                return None, f"{cell_name(rr, cc)} already has {existing.upper()}, but your word needs {ch} there."
            continue
        if ch in pool:
            pool.remove(ch)
            placements.append(Placement(rr, cc, ch, False))
        elif "?" in pool:
            pool.remove("?")
            placements.append(Placement(rr, cc, ch, True))
        else:
            return None, f"needs a {ch} at {cell_name(rr, cc)} but your rack ({' '.join(rack.upper())}) has no {ch} left."
    if not placements:
        return None, "Your word uses no tiles from your rack."
    # The tiles directly before/after the word would extend it.
    br, bc = r - dr, c - dc
    if 0 <= br < SIZE and 0 <= bc < SIZE and grid[br][bc] != ".":
        return None, f"{cell_name(br, bc)} (just before your word) has {grid[br][bc].upper()}, so the real word would be longer."
    ar, ac = er + dr, ec + dc
    if 0 <= ar < SIZE and 0 <= ac < SIZE and grid[ar][ac] != ".":
        return None, f"{cell_name(ar, ac)} (just after your word) has {grid[ar][ac].upper()}, so the real word would be longer."
    return placements, ""


def find_legal_spots(grid, word: str, rack: str, is_word) -> list[tuple[int, int, bool, MoveResult]]:
    """Every legal position for THIS word (used only to fix Gemma's coordinates, never to choose words)."""
    spots = []
    n = len(word)
    for across in (True, False):
        for r in range(SIZE):
            for c in range(SIZE):
                if (across and c + n > SIZE) or (not across and r + n > SIZE):
                    continue
                pl, err = word_to_placements(grid, word, r, c, across, rack)
                if err:
                    continue
                res = validate(grid, pl, is_word)
                if res.ok:
                    spots.append((r, c, across, res))
    return spots
