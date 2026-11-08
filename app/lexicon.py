"""ENABLE word list (public domain). Only used to CHECK words, never to suggest them."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

WORDLIST = Path(__file__).resolve().parent.parent / "data" / "enable1.txt"


@lru_cache(maxsize=1)
def words() -> frozenset[str]:
    with WORDLIST.open(encoding="utf-8") as fh:
        return frozenset(w.strip().upper() for w in fh if 2 <= len(w.strip()) <= 15)


def is_word(word: str) -> bool:
    return word.upper() in words()
