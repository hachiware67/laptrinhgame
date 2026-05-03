import json
import socket
import time
import unittest

from scripts.multiplayer import (
    DEFAULT_GAME_PORT,
    DISCOVERY_PORT,
    LobbyBrowser,
    MultiplayerClient,
    MultiplayerHost,
    format_lan_invite,
    parse_lan_invite,
    _room_from_payload,
)


def _is_port_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", port))
        return True
    except OSError:
        return False
    finally:
        probe.close()


class LanInviteParsingTest(unittest.TestCase):
    def test_parse_valid_invite(self) -> None:
        invite = parse_lan_invite("ABC123@192.168.2.200:43842")

        self.assertEqual(invite.room_id, "ABC123")
        self.assertEqual(invite.host_address, "192.168.2.200")
        self.assertEqual(invite.host_port, 43842)

    def test_format_invite(self) -> None:
        self.assertEqual(format_lan_invite("ABC123", "192.168.2.200", 43842), "ABC123@192.168.2.200:43842")

    def test_parse_trims_whitespace(self) -> None:
        invite = parse_lan_invite("  ABC123@192.168.2.200:43842  ")

        self.assertEqual(invite.room_id, "ABC123")
        self.assertEqual(invite.host_address, "192.168.2.200")
        self.assertEqual(invite.host_port, 43842)

    def test_parse_rejects_missing_room_code(self) -> None:
        with self.assertRaises(ValueError):
            parse_lan_invite("@192.168.2.200:43842")

    def test_parse_rejects_missing_port(self) -> None:
        with self.assertRaises(ValueError):
            parse_lan_invite("ABC123@192.168.2.200")

    def test_parse_rejects_out_of_range_port(self) -> None:
        with self.assertRaises(ValueError):
            parse_lan_invite("ABC123@192.168.2.200:70000")


class HostBindingTest(unittest.TestCase):
    def test_host_uses_default_game_port_when_free(self) -> None:
        if not _is_port_free(DEFAULT_GAME_PORT):
            self.skipTest(f"Port {DEFAULT_GAME_PORT} is already occupied.")

        host = MultiplayerHost("Host", "Default Port", "", 4, host_address="127.0.0.1")
        try:
            self.assertEqual(host.host_port, DEFAULT_GAME_PORT)
            self.assertFalse(host.used_fallback_port)
        finally:
            host.close()

    def test_host_falls_back_to_random_port_when_default_is_busy(self) -> None:
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind(("127.0.0.1", DEFAULT_GAME_PORT))
            blocker.listen()
        except OSError:
            blocker.close()
            self.skipTest(f"Port {DEFAULT_GAME_PORT} could not be reserved for fallback test.")

        host = MultiplayerHost("Host", "Fallback Port", "", 4, host_address="127.0.0.1")
        try:
            self.assertNotEqual(host.host_port, DEFAULT_GAME_PORT)
            self.assertGreater(host.host_port, 0)
            self.assertTrue(host.used_fallback_port)
        finally:
            host.close()
            blocker.close()


class DiscoveryTimingTest(unittest.TestCase):
    def _payload(self, ts: float) -> dict:
        return {
            "room_id": "ABC123",
            "room_name": "Room",
            "host_name": "Host",
            "host_address": "192.168.2.200",
            "host_port": DEFAULT_GAME_PORT,
            "capacity": 4,
            "human_count": 1,
            "has_password": False,
            "started": False,
            "ts": ts,
        }

    def test_room_payload_uses_local_receive_time_for_ttl(self) -> None:
        received_ts = time.time()
        remote_ts = received_ts - 60

        room = _room_from_payload(self._payload(remote_ts), "192.168.2.200", received_ts=received_ts)

        self.assertIsNotNone(room)
        assert room is not None
        self.assertEqual(room.last_seen_ts, received_ts)

    def test_room_query_receives_unicast_advertisement(self) -> None:
        host = MultiplayerHost("Host", "Query Room", "", 4, host_address="127.0.0.1")
        listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        listener.bind(("127.0.0.1", 0))
        listener.settimeout(1.0)
        try:
            source_addr = ("127.0.0.1", listener.getsockname()[1])
            host._handle_discovery_packet({"type": "room_query"}, source_addr)

            packet, _ = listener.recvfrom(65536)
            payload = json.loads(packet.decode("utf-8"))

            self.assertEqual(payload["type"], "room_advertisement")
            self.assertEqual(payload["room"]["room_id"], host.room_id)
            self.assertEqual(payload["room"]["host_port"], host.host_port)
        finally:
            listener.close()
            host.close()


class DirectJoinIntegrationTest(unittest.TestCase):
    def test_client_can_join_from_parsed_lan_invite(self) -> None:
        host = MultiplayerHost("Host", "Direct Join", "", 4, host_address="127.0.0.1")
        client = MultiplayerClient("Guest")
        try:
            invite_text = format_lan_invite(host.room_id, "127.0.0.1", host.host_port)
            invite = parse_lan_invite(invite_text)

            ok, message, room = client.connect_and_join(
                invite.host_address,
                invite.host_port,
                invite.room_id,
                password="",
            )

            self.assertTrue(ok, message)
            self.assertIsNotNone(room)
            assert room is not None
            self.assertEqual(room["room_id"], host.room_id)
            self.assertEqual(room["human_count"], 2)
        finally:
            client.close()
            host.close()


class LobbyBrowserConstantTest(unittest.TestCase):
    def test_discovery_keeps_existing_udp_port(self) -> None:
        self.assertEqual(DISCOVERY_PORT, 43841)
        self.assertIs(LobbyBrowser is not None, True)
