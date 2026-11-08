import unittest

from app.ai_player import check_idea, render_board, tile_index
from app.board import Placement, validate
from app.lexicon import is_word

EMPTY = [["."] * 15 for _ in range(15)]


def board_with(word, r, c, across=True):
    g = [row[:] for row in EMPTY]
    for i, ch in enumerate(word):
        g[r + (0 if across else i)][c + (i if across else 0)] = ch
    return g


class EngineTests(unittest.TestCase):
    def test_first_move_center_and_score(self):
        pl = [Placement(7, 6 + i, ch) for i, ch in enumerate("CAT")]
        res = validate(EMPTY, pl, is_word)
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.score, (3 + 1 + 1) * 2)  # center is double word

    def test_first_move_must_cover_center(self):
        res = validate(EMPTY, [Placement(0, 0, "A"), Placement(0, 1, "T")], is_word)
        self.assertFalse(res.ok)

    def test_cross_word_and_hook(self):
        g = board_with("CAT", 7, 6)
        res = validate(g, [Placement(7, 9, "S")], is_word)  # CATS
        self.assertTrue(res.ok, res.error)
        self.assertEqual(res.words[0]["word"], "CATS")

    def test_invalid_cross_word_reported(self):
        g = board_with("CAT", 7, 6)
        res = validate(g, [Placement(6, 7, "Q"), Placement(6, 8, "I")], is_word)
        self.assertFalse(res.ok)

    def test_gemma_idea_through_board_letter(self):
        g = board_with("CAT", 7, 6)  # C=G8 A=H8 T=I8
        rec = check_idea(g, "RTEXYZQ", {"word": "RATE", "start": "H7", "dir": "down"})
        self.assertTrue(rec["ok"] and not rec.get("corrected"), rec.get("error"))
        rec = check_idea(g, "RTEXYZQ", {"word": "TEXT", "start": "I8", "dir": "down"})
        self.assertTrue(rec["ok"], rec.get("error"))
        self.assertFalse(rec.get("corrected"))

    def test_snap_wrong_coordinate(self):
        g = board_with("CAT", 7, 6)
        # RAT down through the A at H8 should start at H7; Gemma says H6.
        rec = check_idea(g, "RTEXYZQ", {"word": "RAT", "start": "H6", "dir": "down"})
        self.assertTrue(rec["ok"])
        self.assertEqual(rec["start"], "H7")
        self.assertTrue(rec.get("corrected"))

    def test_not_a_word(self):
        rec = check_idea(EMPTY, "ABCDEFG", {"word": "BAGDEF", "start": "H8", "dir": "across"})
        self.assertFalse(rec["ok"])

    def test_prompt_views(self):
        g = board_with("CAT", 7, 6)
        self.assertIn("col H: 8=A", tile_index(g))
        self.assertIn(" 8  ", render_board(g))


if __name__ == "__main__":
    unittest.main()
