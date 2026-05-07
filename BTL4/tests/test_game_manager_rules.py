import unittest

from scripts.cards import (
    ACTION_DRAW_67,
    ACTION_FLASHBANG,
    ACTION_MOM_MAY_CRY,
    ACTION_SILENCE,
    ACTION_DRAW_TWO,
    ACTION_REVERSE,
    ACTION_SKIP,
    ACTION_WILD,
    ACTION_WILD_DRAW_FOUR,
    Card,
    sort_hand_cards,
)
from scripts.deck import build_deck_for_settings
from scripts.game_manager import (
    GameSettings,
    PlayerAction,
    RULE_REACTION,
    RULE_SEVEN_TARGET,
    RULE_ZERO_DIRECTION,
    UnoGameManager,
)


def number(color: str, value: int) -> Card:
    return Card(color=color, kind="number", number=value)


def action(color: str, kind: str) -> Card:
    return Card(color=color, kind=kind)


class UnoRuleSettingsTest(unittest.TestCase):
    def test_mixi_pack_deck_contains_flashbang_cards(self) -> None:
        deck = build_deck_for_settings(["mixi"])
        flashbang_count = sum(1 for card in deck if card.kind == ACTION_FLASHBANG)
        mom_may_cry_count = sum(1 for card in deck if card.kind == ACTION_MOM_MAY_CRY)

        self.assertEqual(flashbang_count, 4)
        self.assertEqual(mom_may_cry_count, 4)

    def make_game(self, settings: GameSettings) -> UnoGameManager:
        game = UnoGameManager(settings=settings, seed=1)
        game.draw_pile = [number("blue", value) for value in range(9)]
        game.discard_pile = [number("red", 5)]
        game.current_color = "red"
        game.current_player = 0
        game.turn_direction = 1
        game.pending_effect = None
        game.pending_effect_player = None
        game.pending_draw_penalty_count = 0
        game.pending_draw_penalty_kind = None
        game.pending_draw_decision_player = None
        game.pending_draw_decision_card = None
        game.winner = None
        return game

    def test_disabled_rule_0_plays_as_normal_number_card(self) -> None:
        game = self.make_game(GameSettings(num_players=3, rule_0_enabled=False))
        game.player_hands = [
            [number("red", 0), number("yellow", 1), number("green", 2)],
            [number("red", 2)],
            [number("blue", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0))

        self.assertTrue(result.ok)
        self.assertIsNone(game.pending_effect)
        self.assertEqual(game.current_player, 1)

    def test_disabled_rule_7_plays_as_normal_number_card(self) -> None:
        game = self.make_game(GameSettings(num_players=3, rule_7_enabled=False))
        game.player_hands = [
            [number("red", 7), number("yellow", 1), number("green", 2)],
            [number("red", 2)],
            [number("blue", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0))

        self.assertTrue(result.ok)
        self.assertIsNone(game.pending_effect)
        self.assertEqual(game.current_player, 1)

    def test_disabled_rule_8_plays_as_normal_number_card(self) -> None:
        game = self.make_game(GameSettings(num_players=3, rule_8_enabled=False))
        game.player_hands = [
            [number("red", 8), number("yellow", 1), number("green", 2)],
            [number("red", 2)],
            [number("blue", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0, timestamp_ms=100))

        self.assertTrue(result.ok)
        self.assertIsNone(game.pending_effect)
        self.assertIsNone(game.pending_reaction_due_ms)
        self.assertEqual(game.current_player, 1)

    def test_enabled_rules_still_create_pending_effects(self) -> None:
        cases = [
            (number("red", 0), RULE_ZERO_DIRECTION),
            (number("red", 7), RULE_SEVEN_TARGET),
            (number("red", 8), RULE_REACTION),
        ]
        for card, expected_effect in cases:
            with self.subTest(card=card.number):
                game = self.make_game(GameSettings(num_players=3))
                game.player_hands = [
                    [card, number("yellow", 1), number("green", 2)],
                    [number("red", 2)],
                    [number("blue", 3)],
                ]

                result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0, timestamp_ms=200))

                self.assertTrue(result.ok)
                self.assertEqual(game.pending_effect, expected_effect)
                self.assertEqual(game.pending_effect_player, 0)

    def test_two_player_reverse_skip_mode_gives_same_player_next_turn(self) -> None:
        game = self.make_game(GameSettings(num_players=2, two_player_reverse_behavior="skip"))
        game.player_hands = [
            [action("red", ACTION_REVERSE), number("yellow", 1), number("green", 2)],
            [number("blue", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0))

        self.assertTrue(result.ok)
        self.assertEqual(game.current_player, 0)
        self.assertEqual(game.turn_direction, 1)

    def test_two_player_reverse_mode_flips_direction_and_passes_turn(self) -> None:
        game = self.make_game(GameSettings(num_players=2, two_player_reverse_behavior="reverse"))
        game.player_hands = [
            [action("red", ACTION_REVERSE), number("yellow", 1), number("green", 2)],
            [number("blue", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0))

        self.assertTrue(result.ok)
        self.assertEqual(game.current_player, 1)
        self.assertEqual(game.turn_direction, -1)

    def test_action_card_cannot_be_winning_final_card(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.player_hands = [
            [action("red", ACTION_SKIP)],
            [number("blue", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0))

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "You cannot win with that action card.")
        self.assertIsNone(game.winner)

    def test_mixi_action_cards_cannot_be_winning_final_card(self) -> None:
        for mixi_kind in (ACTION_FLASHBANG, ACTION_MOM_MAY_CRY):
            with self.subTest(kind=mixi_kind):
                game = self.make_game(GameSettings(num_players=2))
                game.player_hands = [
                    [Card(color=None, kind=mixi_kind)],
                    [number("blue", 3)],
                ]

                result = game.submit_action(
                    PlayerAction(player_id=0, action_type="play", card_index=0, chosen_color="red")
                )

                self.assertFalse(result.ok)
                self.assertEqual(result.message, "You cannot win with that action card.")

    def test_draw_two_stack_allows_wild_draw_four(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.discard_pile = [action("red", ACTION_DRAW_TWO)]
        game.current_color = "red"
        game.pending_draw_penalty_count = 2
        game.pending_draw_penalty_kind = ACTION_DRAW_TWO
        game.player_hands = [
            [Card(color=None, kind=ACTION_WILD_DRAW_FOUR), number("yellow", 1)],
            [number("blue", 3)],
        ]

        result = game.submit_action(
            PlayerAction(player_id=0, action_type="play", card_index=0, chosen_color="blue")
        )

        self.assertTrue(result.ok)
        self.assertEqual(game.pending_draw_penalty_count, 6)
        self.assertEqual(game.pending_draw_penalty_kind, ACTION_WILD_DRAW_FOUR)

    def test_wild_draw_four_stack_rejects_draw_two(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.discard_pile = [Card(color=None, kind=ACTION_WILD_DRAW_FOUR, chosen_color="red")]
        game.current_color = "red"
        game.pending_draw_penalty_count = 4
        game.pending_draw_penalty_kind = ACTION_WILD_DRAW_FOUR
        game.player_hands = [
            [action("red", ACTION_DRAW_TWO), number("yellow", 1)],
            [number("blue", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0))

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Illegal card for current top card/color.")
        self.assertEqual(game.pending_draw_penalty_count, 4)

    def test_sort_hand_cards_groups_wilds_and_orders_colors(self) -> None:
        hand = [
            Card(color="green", kind=ACTION_SKIP),
            Card(color=None, kind=ACTION_WILD),
            number("blue", 9),
            number("red", 2),
            Card(color="yellow", kind=ACTION_REVERSE),
            number("yellow", 4),
            Card(color=None, kind=ACTION_WILD_DRAW_FOUR),
            number("blue", 1),
        ]

        sort_hand_cards(hand)

        self.assertEqual(
            [card.kind for card in hand[:2]],
            [ACTION_WILD, ACTION_WILD_DRAW_FOUR],
        )
        self.assertEqual(
            [(card.color, card.kind, card.number) for card in hand[2:]],
            [
                ("red", "number", 2),
                ("yellow", "number", 4),
                ("yellow", ACTION_REVERSE, None),
                ("blue", "number", 1),
                ("blue", "number", 9),
                ("green", ACTION_SKIP, None),
            ],
        )

    def test_sort_hand_action_sorts_current_players_hand_without_advancing_turn(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.player_hands = [
            [
                number("blue", 9),
                Card(color=None, kind=ACTION_WILD_DRAW_FOUR),
                number("red", 1),
            ],
            [number("yellow", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="sort_hand"))

        self.assertTrue(result.ok)
        self.assertEqual(result.message, "Hand sorted.")
        self.assertEqual(game.current_player, 0)
        self.assertEqual(
            [card.kind for card in game.player_hands[0]],
            [ACTION_WILD_DRAW_FOUR, "number", "number"],
        )

    def test_can_call_uno_requires_two_cards_with_a_legal_play(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.player_hands = [
            [number("red", 9), number("blue", 2)],
            [number("yellow", 3)],
        ]

        self.assertTrue(game.can_call_uno(0))
        result = game.call_uno(0)

        self.assertTrue(result.ok)
        self.assertEqual(result.uno_call_player, 0)
        self.assertIn(0, game.uno_called_players)

    def test_can_call_uno_rejects_two_cards_without_a_legal_play(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.player_hands = [
            [number("blue", 9), number("yellow", 2)],
            [number("yellow", 3)],
        ]

        self.assertFalse(game.can_call_uno(0))
        result = game.call_uno(0)

        self.assertFalse(result.ok)
        self.assertNotIn(0, game.uno_called_players)

    def test_can_call_uno_rejects_one_or_more_than_two_cards(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.player_hands = [
            [number("red", 9)],
            [number("yellow", 3), number("red", 1), number("red", 2)],
        ]

        self.assertFalse(game.can_call_uno(0))
        self.assertFalse(game.call_uno(0).ok)
        self.assertFalse(game.can_call_uno(1))
        self.assertFalse(game.call_uno(1).ok)

    def test_mom_may_cry_keeps_seven_random_cards_and_returns_rest_to_draw_pile(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.player_hands = [
            [Card(color=None, kind=ACTION_MOM_MAY_CRY)] + [number("red", value) for value in range(1, 9)],
            [number("blue", 3)],
        ]
        original_draw_len = len(game.draw_pile)

        result = game.submit_action(
            PlayerAction(player_id=0, action_type="play", card_index=0, chosen_color="red")
        )

        self.assertTrue(result.ok)
        self.assertEqual(len(game.player_hands[0]), 7)
        self.assertEqual(len(game.draw_pile), original_draw_len + 1)
        self.assertNotIn(ACTION_MOM_MAY_CRY, [card.kind for card in game.player_hands[0]])

    def test_flashbang_allows_drawing_even_with_legal_move(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.player_hands = [
            [number("red", 4), number("blue", 1)],
            [number("yellow", 3)],
        ]
        game.flashbang_remaining = {0: 1}
        game.active_flashbang_player = 0

        result = game.submit_action(PlayerAction(player_id=0, action_type="draw"))

        self.assertTrue(result.ok)
        self.assertIsNotNone(result.drew_card)

    def test_non_flashbanged_player_cannot_draw_with_legal_move(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.player_hands = [
            [number("red", 4), number("blue", 1)],
            [number("yellow", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="draw"))

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "You can play a card; draw only when you have no legal move.")

    def test_flashbang_marks_future_turns_for_all_players(self) -> None:
        game = self.make_game(GameSettings(num_players=3))
        game.player_hands = [
            [Card(color=None, kind=ACTION_FLASHBANG), number("red", 2)],
            [number("yellow", 3)],
            [number("blue", 4)],
        ]

        result = game.submit_action(
            PlayerAction(player_id=0, action_type="play", card_index=0, chosen_color="red")
        )

        self.assertTrue(result.ok)
        self.assertEqual(game.flashbang_remaining, {0: 1, 1: 2, 2: 2})
        self.assertEqual(game.current_player, 1)
        self.assertTrue(game.is_player_flashbanged(1))

        second_result = game.submit_action(PlayerAction(player_id=1, action_type="draw"))

        self.assertTrue(second_result.ok)
        self.assertEqual(game.flashbang_remaining, {0: 1, 1: 1, 2: 2})
        self.assertEqual(game.current_player, 2)
        self.assertTrue(game.is_player_flashbanged(2))

    def test_call_uno_rejects_player_outside_current_turn(self) -> None:
        game = self.make_game(GameSettings(num_players=2))
        game.current_player = 0
        game.player_hands = [
            [number("yellow", 3)],
            [number("red", 9), number("blue", 2)],
        ]

        self.assertTrue(game.can_call_uno(1))
        result = game.call_uno(1)

        self.assertFalse(result.ok)
        self.assertEqual(result.message, "Not this player's turn.")
        self.assertNotIn(1, game.uno_called_players)

    def test_mixi_none_type_card_can_be_played_while_draw_penalty_is_pending(self) -> None:
        game = self.make_game(GameSettings(num_players=2, extension_packs=["mixi"]))
        game.pending_draw_penalty_count = 2
        game.pending_draw_penalty_kind = ACTION_DRAW_TWO
        game.player_hands = [
            [Card(color=None, kind=ACTION_SILENCE), number("blue", 1)],
            [number("yellow", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0))

        self.assertTrue(result.ok)
        self.assertEqual(game.pending_draw_penalty_count, 2)
        self.assertEqual(game.pending_draw_penalty_kind, ACTION_DRAW_TWO)
        self.assertEqual(game.current_player, 1)

    def test_silenced_player_is_not_skipped_when_draw_penalty_is_pending(self) -> None:
        game = self.make_game(GameSettings(num_players=2, extension_packs=["mixi"]))
        game.current_player = 0
        game.silence_remaining = {1: 2}
        game.player_hands = [
            [action("red", ACTION_DRAW_TWO), number("red", 6)],
            [Card(color=None, kind=ACTION_DRAW_67), number("yellow", 3)],
        ]

        result = game.submit_action(PlayerAction(player_id=0, action_type="play", card_index=0))

        self.assertTrue(result.ok)
        self.assertEqual(game.current_player, 1)
        self.assertEqual(game.silence_remaining, {1: 2})


if __name__ == "__main__":
    unittest.main()
