import socket
import time
import unittest

from scripts.cards import ACTION_SKIP, Card
from scripts.game_manager import GameSettings
from scripts.multiplayer import HostActionResult, MultiplayerHost


class MultiplayerSecurityTest(unittest.TestCase):
    def make_host(self, capacity: int = 4) -> MultiplayerHost:
        return MultiplayerHost(
            host_name="Host",
            room_name="Security Test",
            password="",
            capacity=capacity,
            host_address="127.0.0.1",
        )

    def test_host_match_uses_configured_extension_packs(self) -> None:
        host = MultiplayerHost(
            host_name="Host",
            room_name="Mixi Room",
            password="",
            capacity=4,
            host_address="127.0.0.1",
            settings=GameSettings(extension_packs=["mixi"]),
        )
        try:
            ok, _, _ = host.start_match()
            self.assertTrue(ok)
            match = host._state.match
            self.assertIsNotNone(match)
            assert match is not None
            self.assertIn("mixi", match.game.settings.extension_packs)
            total_cards = (
                len(match.game.draw_pile)
                + len(match.game.discard_pile)
                + sum(len(hand) for hand in match.game.player_hands)
            )
            self.assertEqual(total_cards, 236)
        finally:
            host.close()

    def test_room_state_does_not_expose_player_tokens(self) -> None:
        host = self.make_host()
        try:
            room = host.room_state

            self.assertNotIn("token", room["players"][0])
            self.assertNotIn(host.host_player_token, str(room))
        finally:
            host.close()

    def test_join_uses_host_issued_token_not_client_supplied_token(self) -> None:
        host = self.make_host()
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            response, token = host._handle_join(
                {
                    "type": "join",
                    "room_id": host.room_id,
                    "player_name": "Guest",
                    "password": "",
                    "token": "client-controlled-token",
                },
                conn,
                ("127.0.0.1", 50000),
            )

            self.assertEqual(response["type"], "join_ok")
            self.assertIsNotNone(token)
            self.assertNotEqual(token, "client-controlled-token")
            self.assertEqual(response["token"], token)
            self.assertNotIn("client-controlled-token", str(response["room"]))
        finally:
            host.close()
            conn.close()

    def test_submit_action_uses_host_receive_time_not_client_time(self) -> None:
        host = self.make_host()
        captured_now_ms: list[int] = []

        def capture_validate(player_token: str, payload: dict, now_ms: int) -> HostActionResult:
            captured_now_ms.append(now_ms)
            return HostActionResult(True, "captured")

        host.validate_human_action = capture_validate  # type: ignore[method-assign]
        before_ms = int(time.time() * 1000)
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            response, returned_token = host._handle_client_message(
                {"type": "submit_action", "action": {"action_type": "draw"}, "now_ms": 1},
                conn,
                ("127.0.0.1", 50000),
                "joined-token",
            )
        finally:
            host.close()
            conn.close()

        self.assertEqual(returned_token, "joined-token")
        self.assertEqual(response, {"type": "action_ack", "ok": True, "message": "captured"})
        self.assertEqual(len(captured_now_ms), 1)
        self.assertGreaterEqual(captured_now_ms[0], before_ms)


class HostAIPacingTest(unittest.TestCase):
    def make_host(self, capacity: int = 2) -> MultiplayerHost:
        return MultiplayerHost(
            host_name="Host",
            room_name="AI Pacing Test",
            password="",
            capacity=capacity,
            host_address="127.0.0.1",
        )

    def test_auto_resolve_processes_only_one_ai_action_per_call(self) -> None:
        host = self.make_host(capacity=2)
        try:
            ok, _, _ = host.start_match()
            self.assertTrue(ok)
            match = host._state.match
            self.assertIsNotNone(match)
            assert match is not None

            match.game.current_player = 1
            match.game.pending_effect = None
            match.game.pending_effect_player = None
            match.game.pending_draw_decision_card = None
            match.game.pending_draw_decision_player = None
            match.game.pending_draw_penalty_count = 0
            match.game.pending_draw_penalty_kind = None
            match.game.current_color = "red"
            match.game.discard_pile = [Card(color="red", kind="number", number=3)]
            match.game.player_hands[1] = [
                Card(color="red", kind=ACTION_SKIP),
                Card(color="green", kind="number", number=5),
            ]
            match.game.player_hands[0] = [Card(color="yellow", kind="number", number=1)]

            now_ms = int(time.time() * 1000)
            events = match._auto_resolve_ai_pending(now_ms)

            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].get("actor_id"), 1)
            self.assertEqual(match.next_ai_action_time_ms, now_ms + 1500)
            self.assertEqual(match.game.current_player, 1)
        finally:
            host.close()

    def test_auto_resolve_respects_ai_cooldown_gate(self) -> None:
        host = self.make_host(capacity=2)
        try:
            ok, _, _ = host.start_match()
            self.assertTrue(ok)
            match = host._state.match
            self.assertIsNotNone(match)
            assert match is not None

            match.game.current_player = 1
            match.next_ai_action_time_ms = 10_000
            match.game.pending_effect = None
            match.game.pending_draw_decision_card = None
            match.game.current_color = "red"
            match.game.discard_pile = [Card(color="red", kind="number", number=9)]
            match.game.player_hands[1] = [Card(color="red", kind=ACTION_SKIP)]

            events = match._auto_resolve_ai_pending(9_500)
            self.assertEqual(events, [])
            self.assertEqual(match.game.current_player, 1)
        finally:
            host.close()

    def test_human_uno_event_includes_uno_call_player(self) -> None:
        host = self.make_host(capacity=2)
        try:
            ok, _, _ = host.start_match()
            self.assertTrue(ok)
            match = host._state.match
            self.assertIsNotNone(match)
            assert match is not None

            match.game.current_player = 0
            match.game.pending_effect = None
            match.game.current_color = "red"
            match.game.discard_pile = [Card(color="red", kind="number", number=3)]
            match.game.player_hands[0] = [
                Card(color="red", kind="number", number=5),
                Card(color="blue", kind="number", number=8),
            ]

            result = match.validate_and_apply(
                host.host_player_token,
                {"action_type": "uno"},
                now_ms=int(time.time() * 1000),
            )

            self.assertTrue(result.ok)
            self.assertEqual(len(result.events), 1)
            self.assertEqual(result.events[0]["action"], "uno")
            self.assertEqual(result.events[0]["uno_call_player"], 0)

        finally:
            host.close()

    def test_human_uno_action_is_rejected_outside_current_turn(self) -> None:
        host = self.make_host(capacity=2)
        try:
            ok, _, _ = host.start_match()
            self.assertTrue(ok)
            match = host._state.match
            self.assertIsNotNone(match)
            assert match is not None

            match.game.current_player = 1
            match.game.pending_effect = None
            match.game.current_color = "red"
            match.game.discard_pile = [Card(color="red", kind="number", number=3)]
            match.game.player_hands[0] = [
                Card(color="red", kind="number", number=5),
                Card(color="blue", kind="number", number=8),
            ]

            result = match.validate_and_apply(
                host.host_player_token,
                {"action_type": "uno"},
                now_ms=int(time.time() * 1000),
            )

            self.assertFalse(result.ok)
            self.assertEqual(result.message, "Not this player's turn.")
            self.assertEqual(result.events, [])
            self.assertNotIn(0, match.game.uno_called_players)

        finally:
            host.close()

    def test_human_penalty_event_includes_uno_caught_player(self) -> None:
        host = self.make_host(capacity=2)
        try:
            ok, _, _ = host.start_match()
            self.assertTrue(ok)
            match = host._state.match
            self.assertIsNotNone(match)
            assert match is not None

            match.game.current_player = 0
            match.game.pending_effect = None
            match.game.pending_draw_penalty_count = 0
            match.game.pending_draw_penalty_kind = None
            match.game.current_color = "red"
            match.game.discard_pile = [Card(color="red", kind="number", number=3)]
            match.game.player_hands[0] = [
                Card(color="red", kind="number", number=5),
                Card(color="blue", kind="number", number=8),
            ]

            result = match.validate_and_apply(
                host.host_player_token,
                {"action_type": "play", "card_index": 0},
                now_ms=int(time.time() * 1000),
            )

            self.assertTrue(result.ok)
            penalty_events = [event for event in result.events if event.get("actor_id") == 0]
            self.assertEqual(len(penalty_events), 1)
            self.assertEqual(penalty_events[0]["uno_caught_player"], 0)
            self.assertEqual(len(penalty_events[0]["uno_penalty_cards"]), 2)

        finally:
            host.close()


if __name__ == "__main__":
    unittest.main()
