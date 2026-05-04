import unittest

from scripts.multiplayer import deserialize_game_state
from scripts.screens import _card_signature_sort_key


class MultiplayerVisualSyncTest(unittest.TestCase):
    def test_card_signature_sort_key_handles_none_values(self) -> None:
        signatures = [
            (None, "wild", None, "red"),
            ("red", "number", 7, None),
            ("blue", "number", 0, None),
            (None, "wild_draw_four", None, None),
        ]

        ordered = sorted(signatures, key=_card_signature_sort_key)

        self.assertEqual(len(ordered), 4)
        self.assertIn((None, "wild", None, "red"), ordered)
        self.assertIn(("red", "number", 7, None), ordered)

    def test_deserialize_game_state_restores_flashbang_state(self) -> None:
        game = deserialize_game_state(
            {
                "settings": {"num_players": 3},
                "draw_pile": [],
                "discard_pile": [{"color": "red", "kind": "number", "number": 5, "chosen_color": None}],
                "player_hands": [[], [], []],
                "current_player": 1,
                "turn_direction": 1,
                "hand_pass_direction": 1,
                "current_color": "red",
                "winner": None,
                "pending_effect": None,
                "pending_effect_player": None,
                "pending_reaction_started_at_ms": None,
                "pending_reaction_due_ms": None,
                "pending_reaction_players": [],
                "pending_reaction_times": [],
                "pending_draw_penalty_count": 0,
                "pending_draw_penalty_kind": None,
                "pending_draw_decision_player": None,
                "pending_draw_decision_card": None,
                "flashbang_remaining": [[1, 2], [2, 1]],
                "active_flashbang_player": 1,
                "uno_called_players": [],
            }
        )

        self.assertEqual(game.flashbang_remaining, {1: 2, 2: 1})
        self.assertEqual(game.active_flashbang_player, 1)
        self.assertTrue(game.is_player_flashbanged(1))


if __name__ == "__main__":
    unittest.main()
