from __future__ import annotations

import ipaddress
import json
import random
import socket
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from queue import Empty, Queue
from typing import Any, Optional

from scripts.ai import perform_simple_ai_turn
from scripts.cards import COLORS, Card
from scripts.game_manager import (
    GameSettings,
    PlayerAction,
    RULE_SEVEN_TARGET,
    RULE_ZERO_DIRECTION,
    UnoGameManager,
)

DISCOVERY_PORT = 43841
DEFAULT_GAME_PORT = 43842
DISCOVERY_INTERVAL_SEC = 1.0
ROOM_TTL_SEC = 3.2
HOST_AI_ACTION_DELAY_MS = 2500
MAX_PACKET_SIZE = 65536
MAX_TCP_LINE_SIZE = 65536
MAX_DISPLAY_NAME_LENGTH = 24
MAX_ROOM_NAME_LENGTH = 32
ROOM_QUERY_TYPE = "room_query"


@dataclass(frozen=True)
class LanInvite:
    room_id: str
    host_address: str
    host_port: int


@dataclass
class LobbyRoomInfo:
    room_id: str
    room_name: str
    host_name: str
    host_address: str
    host_port: int
    capacity: int
    human_count: int
    has_password: bool
    started: bool
    last_seen_ts: float

    @property
    def open_slots(self) -> int:
        return max(0, self.capacity - self.human_count)

    @property
    def is_joinable(self) -> bool:
        return not self.started and self.open_slots > 0


@dataclass
class HumanPlayer:
    token: str
    display_name: str
    is_host: bool = False


@dataclass
class MatchSummary:
    room_id: str
    room_name: str
    capacity: int
    humans: list[str]
    ai_added: int


@dataclass
class HostActionResult:
    ok: bool
    message: str
    events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _ClientSession:
    conn: socket.socket
    addr: tuple[str, int]
    token: str


@dataclass
class _HostedRoomState:
    room_id: str
    room_name: str
    password: str
    capacity: int
    host_name: str
    host_token: str
    settings: GameSettings = field(default_factory=GameSettings)
    humans: list[HumanPlayer] = field(default_factory=list)
    started: bool = False
    match: Optional["HostAuthoritativeMatch"] = None

    def __post_init__(self) -> None:
        self.humans.append(HumanPlayer(token=self.host_token, display_name=self.host_name, is_host=True))

    @property
    def human_count(self) -> int:
        return len(self.humans)

    @property
    def has_password(self) -> bool:
        return bool(self.password)

    @property
    def open_slots(self) -> int:
        return max(0, self.capacity - self.human_count)


class HostAuthoritativeMatch:
    """Room host controls all validation and AI backfill for the match state."""

    def __init__(self, room: _HostedRoomState) -> None:
        self.room_id = room.room_id
        self.capacity = room.capacity
        self.human_tokens = [player.token for player in room.humans]
        self.human_names = [player.display_name for player in room.humans]

        settings = replace(room.settings, num_players=room.capacity)
        self.game = UnoGameManager(settings=settings)

        self.seat_token: dict[int, str] = {}
        self.token_seat: dict[str, int] = {}
        for idx, token in enumerate(self.human_tokens):
            self.seat_token[idx] = token
            self.token_seat[token] = idx

        self.ai_seats = set(range(len(self.human_tokens), room.capacity))
        self.ai_names: dict[int, str] = {
            seat: f"AI {seat - len(self.human_tokens) + 1}" for seat in sorted(self.ai_seats)
        }
        self.next_ai_action_time_ms = 0
        self._scheduled_ai_player: Optional[int] = None
        self.restart_votes: set[str] = set()
        self._initial_game_state = _serialize_game_state(self.game)

    @property
    def ai_count(self) -> int:
        return len(self.ai_seats)

    def summary(self, room: _HostedRoomState) -> MatchSummary:
        return MatchSummary(
            room_id=room.room_id,
            room_name=room.room_name,
            capacity=room.capacity,
            humans=[player.display_name for player in room.humans],
            ai_added=self.ai_count,
        )

    def restart_vote_totals(self) -> tuple[int, int]:
        return len(self.restart_votes), len(self.human_tokens)

    def cast_restart_vote(self, player_token: str) -> tuple[bool, str, bool]:
        if player_token not in self.human_tokens:
            return False, "Only connected human players can vote to restart.", False
        if player_token in self.restart_votes:
            voted, total = self.restart_vote_totals()
            return False, f"You already voted to restart ({voted}/{total}).", False
        self.restart_votes.add(player_token)
        voted, total = self.restart_vote_totals()
        approved = total > 0 and voted >= total
        if approved:
            return True, "Restart vote passed. Match restarted.", True
        return True, f"Restart vote: {voted}/{total}.", False

    def restart_from_initial_state(self, now_ms: int) -> None:
        self.game = deserialize_game_state(self._initial_game_state)
        self.next_ai_action_time_ms = now_ms + HOST_AI_ACTION_DELAY_MS
        self._scheduled_ai_player = self.game.current_player if self.game.current_player in self.ai_seats else None
        self.restart_votes.clear()

    def seat_for_token(self, token: str) -> Optional[int]:
        return self.token_seat.get(token)

    def display_name_by_seat(self) -> dict[int, str]:
        names = {
            seat: self.human_names[idx]
            for idx, (seat, _) in enumerate(sorted(self.seat_token.items(), key=lambda item: item[0]))
        }
        names.update(self.ai_names)
        return names

    def serialize_game_state(self) -> dict[str, Any]:
        return _serialize_game_state(self.game)

    def _action_from_payload(self, player_id: int, payload: dict[str, Any], now_ms: int) -> PlayerAction:
        return PlayerAction(
            player_id=player_id,
            action_type=str(payload.get("action_type", "")),
            card_index=payload.get("card_index"),
            chosen_color=payload.get("chosen_color"),
            chosen_direction=payload.get("chosen_direction"),
            target_player_id=payload.get("target_player_id"),
            timestamp_ms=now_ms,
        )

    def _apply_action_payload(self, player_id: int, payload: dict[str, Any], now_ms: int):
        action_type = str(payload.get("action_type", ""))
        if action_type == "draw_for_decision":
            return self.game.draw_for_decision(player_id)
        if action_type == "keep_drawn":
            return self.game.keep_pending_draw_decision(player_id)
        if action_type == "play_drawn":
            return self.game.play_pending_draw_decision(
                player_id,
                chosen_color=payload.get("chosen_color"),
                timestamp_ms=now_ms,
            )
        return self.game.submit_action(self._action_from_payload(player_id, payload, now_ms))

    def _event_from_action(self, player_id: int, payload: dict[str, Any], result) -> dict[str, Any]:
        action_type = str(payload.get("action_type", ""))
        action_name = {
            "play": "play",
            "draw": "draw",
            "draw_for_decision": "draw",
            "play_drawn": "draw_play",
            "keep_drawn": "draw_keep",
            "draw_played": "draw_play",
            "uno": "uno",
            "react": "react",
            "choose_zero_direction": "rule_0",
            "choose_seven_target": "rule_7",
        }.get(action_type, action_type or "unknown")
        return {
            "actor_id": player_id,
            "action": action_name,
            "ok": bool(result.ok),
            "message": str(result.message),
            "played_card": _serialize_card(result.played_card),
            "drew_card": _serialize_card(result.drew_card),
            "chosen_direction": payload.get("chosen_direction"),
            "target_player_id": payload.get("target_player_id"),
            "chosen_color": payload.get("chosen_color"),
            "uno_call_player": result.uno_call_player,
            "uno_caught_player": result.uno_caught_player,
            "uno_penalty_cards": [_serialize_card(card) for card in result.uno_penalty_cards],
        }

    def _auto_resolve_ai_pending(self, now_ms: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if self.game.winner is not None:
            return events

        current = self.game.current_player
        if current not in self.ai_seats:
            self._scheduled_ai_player = None
            return events

        if self.game.pending_effect is not None and self.game.pending_effect not in (RULE_ZERO_DIRECTION, RULE_SEVEN_TARGET):
            return events

        if self._scheduled_ai_player != current:
            self._scheduled_ai_player = current
            self.next_ai_action_time_ms = max(self.next_ai_action_time_ms, now_ms + HOST_AI_ACTION_DELAY_MS)

        if now_ms < self.next_ai_action_time_ms:
            return events

        if self.game.pending_effect == RULE_ZERO_DIRECTION:
            choice_payload = {
                "action_type": "choose_zero_direction",
                "chosen_direction": random.choice([1, -1]),
                "timestamp_ms": now_ms,
            }
            result = self._apply_action_payload(current, choice_payload, now_ms)
            events.append(self._event_from_action(current, choice_payload, result))
        elif self.game.pending_effect == RULE_SEVEN_TARGET:
            targets = [pid for pid in range(self.capacity) if pid != current]
            if not targets:
                return events
            choice_payload = {
                "action_type": "choose_seven_target",
                "target_player_id": random.choice(targets),
                "timestamp_ms": now_ms,
            }
            result = self._apply_action_payload(current, choice_payload, now_ms)
            events.append(self._event_from_action(current, choice_payload, result))
        elif self.game.pending_effect is not None:
            return events
        elif self.game.pending_draw_decision_card is not None:
            card = self.game.pending_draw_decision_card
            if card is None:
                return events
            if card.is_wild:
                chosen_color = self.game.choose_color_for_player(current)
                decision_payload = {
                    "action_type": "play_drawn",
                    "chosen_color": chosen_color,
                    "timestamp_ms": now_ms,
                }
            elif self.game.is_legal_play(card):
                decision_payload = {
                    "action_type": "play_drawn",
                    "timestamp_ms": now_ms,
                }
            else:
                decision_payload = {
                    "action_type": "keep_drawn",
                    "timestamp_ms": now_ms,
                }
            result = self._apply_action_payload(current, decision_payload, now_ms)
            events.append(self._event_from_action(current, decision_payload, result))
        else:
            outcome = perform_simple_ai_turn(self.game, now_ms=now_ms)
            if not outcome.result:
                return events
            payload = {
                "action_type": outcome.action_type,
                "timestamp_ms": now_ms,
            }
            if outcome.result.played_card is not None and outcome.result.played_card.is_wild:
                payload["chosen_color"] = outcome.result.played_card.chosen_color or self.game.current_color
            events.append(self._event_from_action(current, payload, outcome.result))

        if events:
            self.next_ai_action_time_ms = now_ms + HOST_AI_ACTION_DELAY_MS
            self._scheduled_ai_player = (
                self.game.current_player if self.game.current_player in self.ai_seats else None
            )
        return events

    def validate_and_apply(self, player_token: str, payload: dict[str, Any], now_ms: int) -> HostActionResult:
        player_id = self.token_seat.get(player_token)
        if player_id is None:
            return HostActionResult(False, "Player is not registered in this match.")

        result = self._apply_action_payload(player_id, payload, now_ms)
        if not result.ok:
            return HostActionResult(False, result.message, events=[])

        events = [self._event_from_action(player_id, payload, result)]
        events.extend(self._auto_resolve_ai_pending(now_ms))
        return HostActionResult(True, result.message, events=events)

    def advance_and_collect_events(self, now_ms: int) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        tick_message = self.game.tick(now_ms)
        if tick_message:
            events.append(
                {
                    "actor_id": self.game.current_player,
                    "action": "system",
                    "ok": True,
                    "message": str(tick_message),
                    "played_card": None,
                    "drew_card": None,
                    "chosen_direction": None,
                    "target_player_id": None,
                    "chosen_color": None,
                }
            )
        events.extend(self._auto_resolve_ai_pending(now_ms))
        return events


class MultiplayerClient:
    def __init__(self, display_name: str, token: Optional[str] = None) -> None:
        self.display_name = _sanitize_display_name(display_name, "Player")
        self.token = token or uuid.uuid4().hex
        self.seat_index: Optional[int] = None
        self._recv_queue: Queue[dict[str, Any]] = Queue()
        self._conn: Optional[socket.socket] = None
        self._recv_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def connect_and_join(
        self,
        host_address: str,
        host_port: int,
        room_id: str,
        password: str,
        timeout_sec: float = 3.0,
    ) -> tuple[bool, str, Optional[dict[str, Any]]]:
        try:
            conn = socket.create_connection((host_address, host_port), timeout=timeout_sec)
            conn.settimeout(0.4)
        except OSError as exc:
            return False, f"Could not connect to host: {exc}", None

        self._conn = conn
        join_message = {
            "type": "join",
            "room_id": room_id,
            "player_name": self.display_name,
            "password": password,
        }
        if not self._send_raw(join_message):
            self.close()
            return False, "Could not send join request.", None

        response = self._recv_one_blocking(timeout_sec)
        if response is None:
            self.close()
            return False, "Join request timed out.", None

        if response.get("type") == "join_ok":
            token = response.get("token")
            if token is not None:
                self.token = str(token)
            seat = response.get("seat")
            try:
                self.seat_index = int(seat) if seat is not None else None
            except (TypeError, ValueError):
                self.seat_index = None
            self._start_receiver_thread()
            return True, "Joined room.", response.get("room")

        self.close()
        return False, str(response.get("message", "Join rejected.")), None

    def send(self, payload: dict[str, Any]) -> bool:
        return self._send_raw(payload)

    def poll_messages(self) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        while True:
            try:
                messages.append(self._recv_queue.get_nowait())
            except Empty:
                break
        return messages

    def close(self) -> None:
        self._stop_event.set()
        conn = self._conn
        self._conn = None
        if conn is not None:
            try:
                conn.close()
            except OSError:
                pass

    def _send_raw(self, payload: dict[str, Any]) -> bool:
        if self._conn is None:
            return False
        try:
            raw = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
            self._conn.sendall(raw)
            return True
        except OSError:
            return False

    def _recv_one_blocking(self, timeout_sec: float) -> Optional[dict[str, Any]]:
        if self._conn is None:
            return None
        end_time = time.monotonic() + max(0.05, timeout_sec)
        buffer = b""
        while time.monotonic() < end_time:
            try:
                chunk = self._conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return None
            if not chunk:
                return None
            buffer += chunk
            if len(buffer) > MAX_TCP_LINE_SIZE:
                return None
            if b"\n" in buffer:
                line, _ = buffer.split(b"\n", 1)
                if len(line) > MAX_TCP_LINE_SIZE:
                    return None
                if not line:
                    continue
                try:
                    return json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    return None
        return None

    def _start_receiver_thread(self) -> None:
        self._stop_event.clear()

        def run() -> None:
            assert self._conn is not None
            buffer = b""
            while not self._stop_event.is_set():
                try:
                    chunk = self._conn.recv(4096)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > MAX_TCP_LINE_SIZE and b"\n" not in buffer:
                    break
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if len(line) > MAX_TCP_LINE_SIZE:
                        return
                    if not line:
                        continue
                    try:
                        payload = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    self._recv_queue.put(payload)
            self._recv_queue.put({"type": "disconnected", "message": "Lost connection to host."})

        self._recv_thread = threading.Thread(target=run, daemon=True)
        self._recv_thread.start()


class MultiplayerHost:
    def __init__(
        self,
        host_name: str,
        room_name: str,
        password: str,
        capacity: int,
        host_address: str = "0.0.0.0",
        preferred_port: int = DEFAULT_GAME_PORT,
        settings: Optional[GameSettings] = None,
    ) -> None:
        if capacity not in (2, 4):
            raise ValueError("Room capacity must be 2 or 4.")

        self.host_name = _sanitize_display_name(host_name, "Host")
        self.room_name = _sanitize_room_name(room_name)
        self.password = password
        self.capacity = capacity
        self.host_token = uuid.uuid4().hex
        self.room_id = _new_room_id()
        self.game_settings = replace(settings or GameSettings(), num_players=capacity)

        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._sessions: dict[str, _ClientSession] = {}
        self._state = _HostedRoomState(
            room_id=self.room_id,
            room_name=self.room_name,
            password=self.password,
            capacity=self.capacity,
            host_name=self.host_name,
            host_token=self.host_token,
            settings=self.game_settings,
        )

        self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.used_fallback_port = False
        self._bind_server_socket(host_address, preferred_port)
        self._server_socket.listen()
        self._server_socket.settimeout(0.5)

        bound_host, bound_port = self._server_socket.getsockname()
        self.host_port = int(bound_port)
        self.host_lan_addresses = likely_lan_ipv4_addresses()
        self.host_public_address = self.host_lan_addresses[0] if self.host_lan_addresses else "127.0.0.1"
        self._discovery_targets = _discovery_broadcast_targets(self.host_public_address)
        self._event_seq = 0
        self._last_match_sync_seq = -1
        self._last_match_sync_event: Optional[dict[str, Any]] = None

        self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._advertise_thread = threading.Thread(target=self._advertise_loop, daemon=True)
        self._discovery_thread = threading.Thread(target=self._discovery_listen_loop, daemon=True)
        self._accept_thread.start()
        self._advertise_thread.start()
        self._discovery_thread.start()

    def _bind_server_socket(self, host_address: str, preferred_port: int) -> None:
        try:
            self._server_socket.bind((host_address, preferred_port))
            return
        except OSError:
            if preferred_port == 0:
                raise

        self._server_socket.bind((host_address, 0))
        self.used_fallback_port = True

    @property
    def host_player_token(self) -> str:
        return self.host_token

    @property
    def room_state(self) -> dict[str, Any]:
        with self._lock:
            return self._serialize_room_state()

    def close(self) -> None:
        self._stop_event.set()
        self._broadcast_room_closed()
        try:
            self._server_socket.close()
        except OSError:
            pass
        with self._lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
            self._state.humans.clear()
        for session in sessions:
            try:
                session.conn.close()
            except OSError:
                pass

    def leave_host(self) -> None:
        # Host leaving destroys the room and drops every remote client.
        self.close()

    def start_match(self) -> tuple[bool, str, Optional[MatchSummary]]:
        with self._lock:
            if self._state.started:
                return False, "Match already started.", None
            self._last_match_sync_seq = -1
            self._last_match_sync_event = None
            self._state.started = True
            self._state.match = HostAuthoritativeMatch(self._state)
            summary = self._state.match.summary(self._state)
            payload = {
                "type": "match_started",
                "summary": {
                    "room_id": summary.room_id,
                    "room_name": summary.room_name,
                    "capacity": summary.capacity,
                    "humans": summary.humans,
                    "ai_added": summary.ai_added,
                },
            }
            self._broadcast(payload)
            self._broadcast({"type": "room_state", "room": self._serialize_room_state()})
            self._broadcast_match_sync_locked(
                {
                    "actor_id": self._state.match.seat_for_token(self.host_token),
                    "action": "match_start",
                    "ok": True,
                    "message": "Match started.",
                    "played_card": None,
                    "drew_card": None,
                    "chosen_direction": None,
                    "target_player_id": None,
                    "chosen_color": None,
                }
            )
            return True, f"Match started. Added {summary.ai_added} AI player(s).", summary

    def validate_human_action(self, player_token: str, payload: dict[str, Any], now_ms: int) -> HostActionResult:
        with self._lock:
            match = self._state.match
            if match is None:
                return HostActionResult(False, "Match has not started.")
            for event in match.advance_and_collect_events(now_ms):
                self._broadcast_match_sync_locked(event)
            ended_payload = self._end_match_if_finished_locked()
            if ended_payload is not None:
                self._broadcast(ended_payload)
                self._broadcast({"type": "room_state", "room": self._serialize_room_state()})
                return HostActionResult(False, str(ended_payload.get("message", "Match ended.")))
            result = match.validate_and_apply(player_token, payload, now_ms=now_ms)
            if result.ok:
                for event in result.events:
                    self._broadcast_match_sync_locked(event)
            else:
                self._broadcast_match_sync_locked(None)
            ended_payload = self._end_match_if_finished_locked()
            if ended_payload is not None:
                self._broadcast(ended_payload)
            self._broadcast({"type": "room_state", "room": self._serialize_room_state()})
            return result

    def apply_host_action(self, payload: dict[str, Any], now_ms: int) -> HostActionResult:
        return self.validate_human_action(self.host_token, payload, now_ms)

    def request_restart_vote(self, player_token: str, now_ms: int) -> tuple[bool, str, bool]:
        with self._lock:
            match = self._state.match
            if match is None:
                return False, "Match has not started.", False

            ok, message, approved = match.cast_restart_vote(player_token)
            if not ok:
                return False, message, False

            actor_id = match.seat_for_token(player_token)
            voted, total = match.restart_vote_totals()
            if approved:
                match.restart_from_initial_state(now_ms=now_ms)
                self._broadcast_match_sync_locked(
                    {
                        "actor_id": actor_id,
                        "action": "restart_approved",
                        "ok": True,
                        "message": message,
                        "played_card": None,
                        "drew_card": None,
                        "chosen_direction": None,
                        "target_player_id": None,
                        "chosen_color": None,
                        "restart_votes": voted,
                        "restart_total": total,
                    }
                )
                self._broadcast({"type": "room_state", "room": self._serialize_room_state()})
                return True, message, True

            self._broadcast_match_sync_locked(
                {
                    "actor_id": actor_id,
                    "action": "restart_vote",
                    "ok": True,
                    "message": message,
                    "played_card": None,
                    "drew_card": None,
                    "chosen_direction": None,
                    "target_player_id": None,
                    "chosen_color": None,
                    "restart_votes": voted,
                    "restart_total": total,
                }
            )
            return True, message, False

    def current_match_sync(self) -> Optional[dict[str, Any]]:
        with self._lock:
            match = self._state.match
            if match is None:
                return None
            now_ms = int(time.time() * 1000)
            for event in match.advance_and_collect_events(now_ms):
                self._broadcast_match_sync_locked(event)
            ended_payload = self._end_match_if_finished_locked()
            if ended_payload is not None:
                self._broadcast(ended_payload)
                self._broadcast({"type": "room_state", "room": self._serialize_room_state()})
                return None
            match = self._state.match
            if match is None:
                return None
            sync_seq = self._last_match_sync_seq if self._last_match_sync_seq >= 0 else self._event_seq
            return {
                "type": "match_sync",
                "seq": sync_seq,
                "room": self._serialize_room_state(),
                "game": match.serialize_game_state(),
                "seat_names": match.display_name_by_seat(),
                "event": self._last_match_sync_event,
            }

    def _end_match_if_finished_locked(self) -> Optional[dict[str, Any]]:
        match = self._state.match
        if match is None:
            return None
        winner = match.game.winner
        if winner is None:
            return None
        winner_id = int(winner)
        winner_name = match.display_name_by_seat().get(winner_id, f"Player {winner_id + 1}")
        self._state.started = False
        self._state.match = None
        return {
            "type": "match_ended",
            "winner": winner_id,
            "message": f"Match ended. Winner: {winner_name}.",
        }

    def _serialize_room_state(self) -> dict[str, Any]:
        match = self._state.match
        return {
            "room_id": self._state.room_id,
            "room_name": self._state.room_name,
            "host_name": self._state.host_name,
            "capacity": self._state.capacity,
            "human_count": self._state.human_count,
            "open_slots": self._state.open_slots,
            "has_password": self._state.has_password,
            "started": self._state.started,
            "players": [
                {
                    "seat": index,
                    "display_name": player.display_name,
                    "is_host": player.is_host,
                }
                for index, player in enumerate(self._state.humans)
            ],
            "ai_count": (match.ai_count if match is not None else 0),
            "settings": _serialize_game_settings(self._state.settings),
        }

    def _broadcast_match_sync_locked(self, event: Optional[dict[str, Any]]) -> None:
        match = self._state.match
        if match is None:
            return
        seq = self._event_seq
        payload = {
            "type": "match_sync",
            "seq": seq,
            "room": self._serialize_room_state(),
            "game": match.serialize_game_state(),
            "seat_names": match.display_name_by_seat(),
            "event": event,
        }
        self._event_seq += 1
        self._last_match_sync_seq = seq
        self._last_match_sync_event = event
        self._broadcast(payload)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        encoded = (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        stale_tokens: list[str] = []
        for token, session in list(self._sessions.items()):
            try:
                session.conn.sendall(encoded)
            except OSError:
                stale_tokens.append(token)

        for token in stale_tokens:
            self._remove_player(token)

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                conn, addr = self._server_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            conn.settimeout(0.5)
            threading.Thread(target=self._client_loop, args=(conn, addr), daemon=True).start()

    def _client_loop(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        buffer = b""
        player_token: Optional[str] = None

        try:
            while not self._stop_event.is_set():
                try:
                    chunk = conn.recv(4096)
                except socket.timeout:
                    continue
                if not chunk:
                    break

                buffer += chunk
                if len(buffer) > MAX_TCP_LINE_SIZE and b"\n" not in buffer:
                    break
                oversized_line = False
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    if len(line) > MAX_TCP_LINE_SIZE:
                        oversized_line = True
                        break
                    if not line:
                        continue
                    try:
                        payload = json.loads(line.decode("utf-8"))
                    except json.JSONDecodeError:
                        continue
                    response, new_token = self._handle_client_message(payload, conn, addr, player_token)
                    if new_token is not None:
                        player_token = new_token
                    if response is not None:
                        conn.sendall((json.dumps(response, separators=(",", ":")) + "\n").encode("utf-8"))
                        if payload.get("type") == "join" and response.get("type") == "join_ok":
                            with self._lock:
                                room_payload = self._serialize_room_state()
                            self._broadcast({"type": "room_state", "room": room_payload})
                if oversized_line:
                    break
        except OSError:
            pass
        finally:
            if player_token is not None:
                self._remove_player(player_token)
            try:
                conn.close()
            except OSError:
                pass

    def _handle_client_message(
        self,
        payload: dict[str, Any],
        conn: socket.socket,
        addr: tuple[str, int],
        current_token: Optional[str],
    ) -> tuple[Optional[dict[str, Any]], Optional[str]]:
        msg_type = payload.get("type")

        if msg_type == "join":
            return self._handle_join(payload, conn, addr)

        if current_token is None:
            return {"type": "error", "message": "Join required first."}, None

        if msg_type == "leave":
            self._remove_player(current_token)
            return {"type": "left", "message": "Left room."}, current_token

        if msg_type == "request_room_state":
            with self._lock:
                return {"type": "room_state", "room": self._serialize_room_state()}, current_token

        if msg_type == "submit_action":
            action_payload = payload.get("action") or {}
            if not isinstance(action_payload, dict):
                return {"type": "action_ack", "ok": False, "message": "Invalid action payload."}, current_token
            now_ms = int(time.time() * 1000)
            result = self.validate_human_action(current_token, action_payload, now_ms=now_ms)
            return {"type": "action_ack", "ok": result.ok, "message": result.message}, current_token

        if msg_type == "vote_restart":
            now_ms = int(time.time() * 1000)
            ok, message, approved = self.request_restart_vote(current_token, now_ms=now_ms)
            return {
                "type": "restart_vote_ack",
                "ok": ok,
                "message": message,
                "approved": approved,
            }, current_token

        return None, current_token

    def _handle_join(
        self,
        payload: dict[str, Any],
        conn: socket.socket,
        addr: tuple[str, int],
    ) -> tuple[dict[str, Any], Optional[str]]:
        room_id = str(payload.get("room_id", "")).strip()
        player_name = _sanitize_display_name(str(payload.get("player_name", "")), "Player")
        token = uuid.uuid4().hex
        password = str(payload.get("password", ""))

        with self._lock:
            if room_id != self._state.room_id:
                return {"type": "join_error", "message": "Room not found."}, None
            if self._state.started:
                return {"type": "join_error", "message": "Match already started."}, None
            if self._state.password and self._state.password != password:
                return {"type": "join_error", "message": "Incorrect room password."}, None
            if self._state.human_count >= self._state.capacity:
                return {"type": "join_error", "message": "Room is full."}, None
            while any(player.token == token for player in self._state.humans):
                token = uuid.uuid4().hex

            seat = self._state.human_count
            self._state.humans.append(HumanPlayer(token=token, display_name=player_name, is_host=False))
            self._sessions[token] = _ClientSession(conn=conn, addr=addr, token=token)
            room_payload = self._serialize_room_state()

        return {"type": "join_ok", "room": room_payload, "token": token, "seat": seat}, token

    def _remove_player(self, token: str) -> None:
        should_close_room = False
        with self._lock:
            self._sessions.pop(token, None)
            before = len(self._state.humans)
            self._state.humans = [player for player in self._state.humans if player.token != token]
            if len(self._state.humans) != before:
                if self._state.human_count <= 0:
                    should_close_room = True
                room_payload = self._serialize_room_state()
            else:
                room_payload = None

        if room_payload is not None:
            self._broadcast({"type": "room_state", "room": room_payload})

        if should_close_room:
            self.close()

    def _broadcast_room_closed(self) -> None:
        payload = {"type": "room_closed", "room_id": self.room_id}
        packet = json.dumps(payload).encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        for target in self._discovery_targets:
            try:
                sock.sendto(packet, (target, DISCOVERY_PORT))
            except OSError:
                continue
        try:
            sock.close()
        except OSError:
            pass

    def _room_advertisement_packet(self) -> bytes:
        with self._lock:
            room_payload = self._serialize_room_state()
            room_payload.update(
                {
                    "host_address": self.host_public_address,
                    "host_port": self.host_port,
                    "ts": time.time(),
                }
            )
        return json.dumps({"type": "room_advertisement", "room": room_payload}).encode("utf-8")

    def _handle_discovery_packet(self, payload: dict[str, Any], source_addr: tuple[str, int]) -> None:
        if payload.get("type") != ROOM_QUERY_TYPE:
            return

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.sendto(self._room_advertisement_packet(), source_addr)
        except OSError:
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def _discovery_listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(("", DISCOVERY_PORT))
            sock.settimeout(0.5)
        except OSError:
            try:
                sock.close()
            except OSError:
                pass
            return

        while not self._stop_event.is_set():
            try:
                packet, source_addr = sock.recvfrom(MAX_PACKET_SIZE)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(packet.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(payload, dict):
                self._handle_discovery_packet(payload, source_addr)

        try:
            sock.close()
        except OSError:
            pass

    def _advertise_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        while not self._stop_event.is_set():
            try:
                packet = self._room_advertisement_packet()
                for target in self._discovery_targets:
                    sock.sendto(packet, (target, DISCOVERY_PORT))
            except OSError:
                pass
            self._stop_event.wait(DISCOVERY_INTERVAL_SEC)
        try:
            sock.close()
        except OSError:
            pass


class LobbyBrowser:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._rooms: dict[str, LobbyRoomInfo] = {}
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def list_rooms(self, now_ts: Optional[float] = None) -> list[LobbyRoomInfo]:
        now = now_ts or time.time()
        with self._lock:
            stale = [room_id for room_id, room in self._rooms.items() if now - room.last_seen_ts > ROOM_TTL_SEC]
            for room_id in stale:
                self._rooms.pop(room_id, None)
            rooms = [room for room in self._rooms.values() if room.is_joinable]
        rooms.sort(key=lambda room: (room.room_name.lower(), room.room_id))
        return rooms

    def close(self) -> None:
        self._stop_event.set()

    def _listen_loop(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        try:
            sock.bind(("", DISCOVERY_PORT))
        except OSError:
            sock.bind(("127.0.0.1", DISCOVERY_PORT))
        sock.settimeout(0.5)
        query_targets = _discovery_broadcast_targets(_pick_public_ipv4() or "")
        next_query_ts = 0.0

        while not self._stop_event.is_set():
            now = time.time()
            if now >= next_query_ts:
                query = json.dumps({"type": ROOM_QUERY_TYPE}).encode("utf-8")
                for target in query_targets:
                    try:
                        sock.sendto(query, (target, DISCOVERY_PORT))
                    except OSError:
                        continue
                next_query_ts = now + DISCOVERY_INTERVAL_SEC
            try:
                packet, source_addr = sock.recvfrom(MAX_PACKET_SIZE)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(packet.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if payload.get("type") == "room_closed":
                room_id = str(payload.get("room_id", ""))
                if room_id:
                    with self._lock:
                        self._rooms.pop(room_id, None)
                continue
            if payload.get("type") != "room_advertisement":
                continue
            room_payload = payload.get("room")
            if not isinstance(room_payload, dict):
                continue
            room = _room_from_payload(room_payload, source_addr[0], received_ts=time.time())
            if room is None:
                continue
            with self._lock:
                self._rooms[room.room_id] = room

        try:
            sock.close()
        except OSError:
            pass


def parse_lan_invite(value: str) -> LanInvite:
    raw = str(value).strip()
    try:
        room_part, address_part = raw.split("@", 1)
        host_part, port_part = address_part.rsplit(":", 1)
    except ValueError as exc:
        raise ValueError("Use ROOMCODE@IP:PORT, for example ABC123@192.168.2.200:43842.") from exc

    room_id = room_part.strip().upper()
    host_address = host_part.strip()
    port_text = port_part.strip()

    if not room_id or len(room_id) > 16 or not room_id.isalnum():
        raise ValueError("Use ROOMCODE@IP:PORT, for example ABC123@192.168.2.200:43842.")
    if not _is_valid_invite_host(host_address):
        raise ValueError("Use ROOMCODE@IP:PORT, for example ABC123@192.168.2.200:43842.")
    try:
        host_port = int(port_text)
    except ValueError as exc:
        raise ValueError("Use ROOMCODE@IP:PORT, for example ABC123@192.168.2.200:43842.") from exc
    if not 1 <= host_port <= 65535:
        raise ValueError("Use ROOMCODE@IP:PORT, for example ABC123@192.168.2.200:43842.")

    return LanInvite(room_id=room_id, host_address=host_address, host_port=host_port)


def format_lan_invite(room_id: str, host_address: str, host_port: int) -> str:
    invite = parse_lan_invite(f"{room_id}@{host_address}:{host_port}")
    return f"{invite.room_id}@{invite.host_address}:{invite.host_port}"


def _is_valid_invite_host(host_address: str) -> bool:
    if not host_address or any(char.isspace() for char in host_address):
        return False
    if any(char in host_address for char in "/\\@"):
        return False
    try:
        ipaddress.ip_address(host_address)
        return True
    except ValueError:
        pass
    labels = host_address.split(".")
    return all(label and label.replace("-", "").isalnum() for label in labels)


def _room_from_payload(
    payload: dict[str, Any],
    source_host: str,
    received_ts: Optional[float] = None,
) -> Optional[LobbyRoomInfo]:
    try:
        room_id = str(payload["room_id"])
        room_name = _sanitize_room_name(str(payload["room_name"]))
        host_name = _sanitize_display_name(str(payload["host_name"]), "Host")
        advertised_host = str(payload["host_address"])
        host_port = int(payload["host_port"])
        capacity = int(payload["capacity"])
        human_count = int(payload["human_count"])
        has_password = bool(payload.get("has_password", False))
        started = bool(payload.get("started", False))
    except (KeyError, TypeError, ValueError):
        return None

    if capacity not in (2, 4):
        return None
    if not 1 <= host_port <= 65535:
        return None
    if not room_id or len(room_id) > 16:
        return None

    host_address = source_host or advertised_host
    if host_address == "0.0.0.0":
        host_address = advertised_host

    return LobbyRoomInfo(
        room_id=room_id,
        room_name=room_name,
        host_name=host_name,
        host_address=host_address,
        host_port=host_port,
        capacity=capacity,
        human_count=max(0, min(capacity, human_count)),
        has_password=has_password,
        started=started,
        last_seen_ts=received_ts if received_ts is not None else time.time(),
    )


def _sanitize_display_name(value: str, fallback: str) -> str:
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        cleaned = fallback
    return cleaned[:MAX_DISPLAY_NAME_LENGTH]


def _sanitize_room_name(value: str) -> str:
    cleaned = " ".join(str(value).strip().split())
    if not cleaned:
        cleaned = "UNO Room"
    return cleaned[:MAX_ROOM_NAME_LENGTH]


def _new_room_id() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(6))


def _pick_public_ipv4() -> Optional[str]:
    # UDP connect trick: no traffic sent, but exposes preferred outbound interface.
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("8.8.8.8", 80))
        return probe.getsockname()[0]
    except OSError:
        return None
    finally:
        try:
            probe.close()
        except OSError:
            pass


def likely_lan_ipv4_addresses(primary_ip: Optional[str] = None) -> tuple[str, ...]:
    primary = primary_ip if primary_ip is not None else _pick_public_ipv4()
    addresses: list[str] = []
    for ip_text in _candidate_local_ipv4s(primary or ""):
        try:
            ip_obj = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if not isinstance(ip_obj, ipaddress.IPv4Address):
            continue
        if ip_obj.is_loopback or ip_obj.is_multicast or ip_obj.is_unspecified or ip_obj.is_link_local:
            continue
        if ip_text not in addresses:
            addresses.append(ip_text)
    return tuple(addresses)


def _discovery_broadcast_targets(primary_ip: str) -> tuple[str, ...]:
    targets: list[str] = ["255.255.255.255"]
    seen: set[str] = set(targets)

    for ip_text in _candidate_local_ipv4s(primary_ip):
        try:
            ip_obj = ipaddress.ip_address(ip_text)
        except ValueError:
            continue
        if not isinstance(ip_obj, ipaddress.IPv4Address):
            continue
        if ip_obj.is_loopback or ip_obj.is_multicast or ip_obj.is_unspecified:
            continue
        # /24 is the most common LAN layout and works for typical home networks.
        directed = str(ipaddress.IPv4Network(f"{ip_text}/24", strict=False).broadcast_address)
        if directed not in seen:
            seen.add(directed)
            targets.append(directed)

    if "127.0.0.1" not in seen:
        targets.append("127.0.0.1")
    return tuple(targets)


def _candidate_local_ipv4s(primary_ip: str) -> list[str]:
    candidates: list[str] = []
    if primary_ip:
        candidates.append(primary_ip)

    try:
        _, _, host_ips = socket.gethostbyname_ex(socket.gethostname())
    except OSError:
        host_ips = []
    candidates.extend(host_ips)

    deduped: list[str] = []
    seen: set[str] = set()
    for ip_text in candidates:
        if not ip_text or ip_text in seen:
            continue
        seen.add(ip_text)
        deduped.append(ip_text)
    return deduped


def _serialize_card(card: Optional[Card]) -> Optional[dict[str, Any]]:
    if card is None:
        return None
    return {
        "color": card.color,
        "kind": card.kind,
        "number": card.number,
        "chosen_color": card.chosen_color,
    }


def _deserialize_card(payload: Optional[dict[str, Any]]) -> Optional[Card]:
    if not isinstance(payload, dict):
        return None
    card = Card(
        color=payload.get("color"),
        kind=str(payload.get("kind", "")),
        number=payload.get("number"),
    )
    chosen_color = payload.get("chosen_color")
    card.chosen_color = str(chosen_color) if chosen_color is not None else None
    return card


def _serialize_game_settings(settings: GameSettings) -> dict[str, Any]:
    return {
        "num_players": settings.num_players,
        "initial_cards": settings.initial_cards,
        "rule_0_enabled": settings.rule_0_enabled,
        "rule_7_enabled": settings.rule_7_enabled,
        "rule_8_enabled": settings.rule_8_enabled,
        "rule_8_reaction_timer_ms": settings.rule_8_reaction_timer_ms,
        "two_player_reverse_behavior": settings.two_player_reverse_behavior,
        "extension_packs": list(settings.extension_packs),
    }


def _deserialize_game_settings(payload: dict[str, Any]) -> GameSettings:
    return GameSettings(
        num_players=int(payload.get("num_players", 4)),
        initial_cards=int(payload.get("initial_cards", 7)),
        rule_0_enabled=bool(payload.get("rule_0_enabled", True)),
        rule_7_enabled=bool(payload.get("rule_7_enabled", True)),
        rule_8_enabled=bool(payload.get("rule_8_enabled", True)),
        rule_8_reaction_timer_ms=int(payload.get("rule_8_reaction_timer_ms", 3000)),
        two_player_reverse_behavior=str(payload.get("two_player_reverse_behavior", "reverse")),
        extension_packs=[str(item) for item in payload.get("extension_packs", []) if isinstance(item, str)],
    )


def _serialize_game_state(game: UnoGameManager) -> dict[str, Any]:
    return {
        "settings": _serialize_game_settings(game.settings),
        "draw_pile": [_serialize_card(card) for card in game.draw_pile],
        "discard_pile": [_serialize_card(card) for card in game.discard_pile],
        "player_hands": [[_serialize_card(card) for card in hand] for hand in game.player_hands],
        "current_player": game.current_player,
        "turn_direction": game.turn_direction,
        "hand_pass_direction": game.hand_pass_direction,
        "current_color": game.current_color,
        "winner": game.winner,
        "pending_effect": game.pending_effect,
        "pending_effect_player": game.pending_effect_player,
        "pending_reaction_started_at_ms": game.pending_reaction_started_at_ms,
        "pending_reaction_due_ms": game.pending_reaction_due_ms,
        "pending_reaction_players": sorted(game.pending_reaction_players),
        "pending_reaction_times": [[int(player), int(ts)] for player, ts in game.pending_reaction_times],
        "pending_draw_penalty_count": game.pending_draw_penalty_count,
        "pending_draw_penalty_kind": game.pending_draw_penalty_kind,
        "pending_draw_penalty_source": game.pending_draw_penalty_source,
        "pending_draw_decision_player": game.pending_draw_decision_player,
        "pending_draw_decision_card": _serialize_card(game.pending_draw_decision_card),
        "silence_remaining": [[int(player_id), int(count)] for player_id, count in sorted(game.silence_remaining.items())],
        "flashbang_remaining": [[int(player_id), int(count)] for player_id, count in sorted(game.flashbang_remaining.items())],
        "active_flashbang_player": game.active_flashbang_player,
        "uno_called_players": sorted(game.uno_called_players),
    }


def deserialize_game_state(payload: dict[str, Any]) -> UnoGameManager:
    settings_payload = payload.get("settings") or {}
    settings = _deserialize_game_settings(settings_payload if isinstance(settings_payload, dict) else {})
    game = UnoGameManager(settings=settings)
    game.draw_pile = [card for card in (_deserialize_card(item) for item in payload.get("draw_pile", [])) if card is not None]
    game.discard_pile = [card for card in (_deserialize_card(item) for item in payload.get("discard_pile", [])) if card is not None]
    game.player_hands = []
    for hand_payload in payload.get("player_hands", []):
        hand = [card for card in (_deserialize_card(item) for item in hand_payload) if card is not None]
        game.player_hands.append(hand)
    while len(game.player_hands) < game.num_players:
        game.player_hands.append([])
    game.current_player = int(payload.get("current_player", 0))
    game.turn_direction = int(payload.get("turn_direction", 1)) or 1
    game.hand_pass_direction = int(payload.get("hand_pass_direction", 1)) or 1
    game.current_color = payload.get("current_color")
    winner = payload.get("winner")
    game.winner = int(winner) if winner is not None else None
    game.pending_effect = payload.get("pending_effect")
    pending_effect_player = payload.get("pending_effect_player")
    game.pending_effect_player = int(pending_effect_player) if pending_effect_player is not None else None
    started_at = payload.get("pending_reaction_started_at_ms")
    due_ms = payload.get("pending_reaction_due_ms")
    game.pending_reaction_started_at_ms = int(started_at) if started_at is not None else None
    game.pending_reaction_due_ms = int(due_ms) if due_ms is not None else None
    game.pending_reaction_players = {int(pid) for pid in payload.get("pending_reaction_players", [])}
    game.pending_reaction_times = []
    for item in payload.get("pending_reaction_times", []):
        if not isinstance(item, list) or len(item) != 2:
            continue
        game.pending_reaction_times.append((int(item[0]), int(item[1])))
    game.pending_draw_penalty_count = int(payload.get("pending_draw_penalty_count", 0))
    game.pending_draw_penalty_kind = payload.get("pending_draw_penalty_kind")
    pending_source = payload.get("pending_draw_penalty_source")
    game.pending_draw_penalty_source = int(pending_source) if pending_source is not None else None
    pending_player = payload.get("pending_draw_decision_player")
    game.pending_draw_decision_player = int(pending_player) if pending_player is not None else None
    game.pending_draw_decision_card = _deserialize_card(payload.get("pending_draw_decision_card"))
    game.silence_remaining = {}
    silence_payload = payload.get("silence_remaining", [])
    if isinstance(silence_payload, dict):
        for player_id, count in silence_payload.items():
            game.silence_remaining[int(player_id)] = int(count)
    else:
        for item in silence_payload:
            if not isinstance(item, list) or len(item) != 2:
                continue
            game.silence_remaining[int(item[0])] = int(item[1])
    game.flashbang_remaining = {}
    for item in payload.get("flashbang_remaining", []):
        if not isinstance(item, list) or len(item) != 2:
            continue
        game.flashbang_remaining[int(item[0])] = int(item[1])
    active_flashbang = payload.get("active_flashbang_player")
    game.active_flashbang_player = int(active_flashbang) if active_flashbang is not None else None
    game.uno_called_players = {int(pid) for pid in payload.get("uno_called_players", [])}
    game.is_animating = False
    return game


def sanitize_wild_color(color: Optional[str]) -> Optional[str]:
    if color is None:
        return None
    value = str(color).strip().lower()
    return value if value in COLORS else None
