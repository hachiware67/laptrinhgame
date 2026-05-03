import random
import os
import time
from dataclasses import dataclass
from typing import Optional, Any

import pygame

from scripts.animation import ActiveCard, lerp, lerp_point, smooth_factor, transform_card_surface
from scripts.assets import asset_path
from scripts.ai import AITurnOutcome, perform_simple_ai_turn
from scripts.cards import ACTION_WILD_DRAW_FOUR, ACTION_COUNTER, ACTION_DRAW_67, ACTION_SILENCE, Card, sort_hand_cards
from scripts.game_manager import (
    ActionResult,
    GameSettings,
    PlayerAction,
    PASS_CLOCKWISE,
    PASS_COUNTER_CLOCKWISE,
    RULE_REACTION,
    RULE_SEVEN_TARGET,
    RULE_ZERO_DIRECTION,
    UnoGameManager,
)
from scripts.multiplayer import (
    LobbyBrowser,
    LobbyRoomInfo,
    MultiplayerClient,
    MultiplayerHost,
    deserialize_game_state,
)
from scripts.sprites import CardSpriteAtlas
from scripts.ui import (
    card_rect_for_hand,
    draw_theme_background,
    draw_theme_button,
    draw_theme_panel,
    get_card_rect_from_pos,
    get_draw_decision_button_rects,
    get_draw_pile_rect,
    get_end_screen_button_rects,
    get_hovered_hand_index,
    get_discard_pile_rect,
    get_player_anchor_point,
    get_player_card_rotation,
    get_player_hand_rotation,
    get_player_hand_card_rects,
    get_sort_hand_button_rect,
    get_reaction_button_rect,
    get_rule_seven_target_rects,
    get_rule_zero_choice_rects,
    get_title_screen_button_rects,
    get_uno_button_rect,
    get_wild_color_at_pos,
    render_end_screen,
    render_title_screen,
    render_ui,
    theme_font,
)

STATE_TITLE = "title"
STATE_PLAYING = "playing"
STATE_END = "end"
HAND_TRANSFER_ANIMATION = "Hand_Transfer_Animation"
SETTINGS_BG_COLOR = (10, 18, 28)
SETTINGS_ACTIVE_FILL = (65, 175, 95)
SETTINGS_ACTIVE_BORDER = (164, 235, 178)
SETTINGS_IDLE_FILL = (84, 94, 110)
SETTINGS_IDLE_BORDER = (146, 158, 174)
SETTINGS_DANGER_FILL = (225, 55, 55)
SETTINGS_DANGER_BORDER = (246, 166, 166)
SETTINGS_LABEL_X_OFFSET = 430
SETTINGS_SLIDER_WIDTH = 400


def _canonical_to_view_player(canonical_id: int, local_canonical_id: int, num_players: int) -> int:
    if num_players <= 0:
        return canonical_id
    return (canonical_id - local_canonical_id) % num_players


def _view_to_canonical_player(view_id: int, local_canonical_id: int, num_players: int) -> int:
    if num_players <= 0:
        return view_id
    return (view_id + local_canonical_id) % num_players


def _remap_game_payload_to_local_view(
    game_payload: dict[str, Any],
    local_canonical_player_id: int,
) -> dict[str, Any]:
    remapped = dict(game_payload)
    hands = game_payload.get("player_hands", [])
    num_players = len(hands) if isinstance(hands, list) else int(game_payload.get("settings", {}).get("num_players", 4))
    if num_players <= 0:
        return remapped

    remapped_hands: list[list[dict[str, Any]]] = [[] for _ in range(num_players)]
    if isinstance(hands, list):
        for canonical_id, hand_payload in enumerate(hands):
            view_id = _canonical_to_view_player(canonical_id, local_canonical_player_id, num_players)
            remapped_hands[view_id] = hand_payload if isinstance(hand_payload, list) else []
    remapped["player_hands"] = remapped_hands

    def remap_player_value(value: Any) -> Any:
        if value is None:
            return None
        return _canonical_to_view_player(int(value), local_canonical_player_id, num_players)

    remapped["current_player"] = remap_player_value(game_payload.get("current_player"))
    remapped["winner"] = remap_player_value(game_payload.get("winner"))
    remapped["pending_effect_player"] = remap_player_value(game_payload.get("pending_effect_player"))
    remapped["pending_draw_decision_player"] = remap_player_value(game_payload.get("pending_draw_decision_player"))
    remapped["pending_reaction_players"] = [
        _canonical_to_view_player(int(pid), local_canonical_player_id, num_players)
        for pid in game_payload.get("pending_reaction_players", [])
    ]
    remapped["pending_reaction_times"] = [
        [_canonical_to_view_player(int(item[0]), local_canonical_player_id, num_players), int(item[1])]
        for item in game_payload.get("pending_reaction_times", [])
        if isinstance(item, list) and len(item) == 2
    ]
    remapped["uno_called_players"] = [
        _canonical_to_view_player(int(pid), local_canonical_player_id, num_players)
        for pid in game_payload.get("uno_called_players", [])
    ]
    return remapped


def _card_signature(card: Card) -> tuple[Optional[str], str, Optional[int], Optional[str]]:
    return (card.color, card.kind, card.number, card.chosen_color)


def _card_signature_sort_key(
    signature: tuple[Optional[str], str, Optional[int], Optional[str]],
) -> tuple[str, str, int, str]:
    color, kind, number, chosen_color = signature
    return (
        color or "",
        kind,
        number if number is not None else -1,
        chosen_color or "",
    )


@dataclass
class AudioSettings:
    master_volume: float = 1.0
    music_volume: float = 0.18
    sfx_volume: float = 1.0

    def music_mix(self) -> float:
        return max(0.0, min(1.0, self.master_volume * self.music_volume))

    def sfx_mix(self, base_volume: float = 1.0) -> float:
        return max(0.0, min(1.0, base_volume * self.master_volume * self.sfx_volume))


@dataclass
class ScreenResult:
    next_screen: Optional["BaseScreen"] = None
    running: bool = True
    display_mode: Optional[str] = None


@dataclass
class MultiplayerHostSetup:
    player_name: str
    room_name: str
    password: str
    capacity: int


@dataclass
class HandTransferAnimation:
    choice_action: PlayerAction
    phase: int
    cards: list[ActiveCard]
    target_owner_by_card_id: dict[int, int]


class BaseScreen:
    state_name = ""

    def handle_events(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
        now_ms: int,
    ) -> ScreenResult:
        return ScreenResult()

    def update(self, screen: pygame.Surface, now_ms: int) -> Optional["BaseScreen"]:
        return None

    def draw(self, screen: pygame.Surface, now_ms: int) -> None:
        raise NotImplementedError

    @property
    def wants_bgm(self) -> bool:
        return False


class TitleScreen(BaseScreen):
    state_name = STATE_TITLE

    def __init__(self, atlas: CardSpriteAtlas, audio_settings: AudioSettings) -> None:
        self.atlas = atlas
        self.audio_settings = audio_settings

    def handle_events(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
        now_ms: int,
    ) -> ScreenResult:
        for event in events:
            if event.type == pygame.QUIT:
                return ScreenResult(running=False)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_pos = event.pos
                for button_name, rect in get_title_screen_button_rects(screen.get_rect()).items():
                    if rect.collidepoint(mouse_pos):
                        if button_name == "start_local":
                            return ScreenResult(
                                next_screen=GameSettingsScreen(self.atlas, self.audio_settings)
                            )
                        if button_name == "settings":
                            return ScreenResult(
                                next_screen=MainSettingsScreen(self.atlas, self.audio_settings)
                            )
                        if button_name == "multiplayer":
                            return ScreenResult(
                                next_screen=MultiplayerScreen(self.atlas, self.audio_settings)
                            )
                        if button_name == "quit":
                            return ScreenResult(running=False)
                        break

        return ScreenResult()

    def draw(self, screen: pygame.Surface, now_ms: int) -> None:
        render_title_screen(screen)


class MultiplayerScreen(BaseScreen):
    state_name = "multiplayer"
    MODE_LOBBY = "lobby"
    MODE_CREATE = "create"
    MODE_ROOM = "room"

    def __init__(self, atlas: CardSpriteAtlas, audio_settings: AudioSettings) -> None:
        self.atlas = atlas
        self.audio_settings = audio_settings
        self.mode = self.MODE_LOBBY
        default_name = (os.getenv("USERNAME") or os.getenv("USER") or "Player").strip()
        self.player_name = default_name[:20] or "Player"
        self.join_password = ""
        self.create_room_name = "UNO Room"
        self.create_password = ""
        self.create_capacity = 4
        self.focus_field = "player_name"
        self.selected_room_id: Optional[str] = None
        self.lobby_rooms: list[LobbyRoomInfo] = []
        self.room_state: Optional[dict[str, Any]] = None
        self.host: Optional[MultiplayerHost] = None
        self.client: Optional[MultiplayerClient] = None
        self.is_host = False
        self.message = "Create a room or join one from the lobby."
        self._pending_next_screen: Optional[BaseScreen] = None

    def handle_events(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
        now_ms: int,
    ) -> ScreenResult:
        self._refresh_lobby_cache()
        for event in events:
            if event.type == pygame.QUIT:
                self._close_network()
                return ScreenResult(running=False)

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if self.mode == self.MODE_CREATE:
                        self.mode = self.MODE_LOBBY
                        self.focus_field = "player_name"
                    elif self.mode == self.MODE_ROOM:
                        self._leave_room()
                    else:
                        self._close_network()
                        return ScreenResult(next_screen=TitleScreen(self.atlas, self.audio_settings))
                    continue
                self._handle_text_input(event)
                continue

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if self.mode == self.MODE_LOBBY:
                    outcome = self._handle_lobby_click(event.pos, screen.get_rect())
                elif self.mode == self.MODE_CREATE:
                    outcome = self._handle_create_click(event.pos, screen.get_rect())
                else:
                    outcome = self._handle_room_click(event.pos, screen.get_rect(), now_ms)
                if outcome is not None:
                    return outcome

        if self._pending_next_screen is not None:
            target = self._pending_next_screen
            self._pending_next_screen = None
            return ScreenResult(next_screen=target)

        return ScreenResult()

    def update(self, screen: pygame.Surface, now_ms: int) -> Optional["BaseScreen"]:
        if self.host is not None:
            self.room_state = self.host.room_state
        if self.client is not None:
            for packet in self.client.poll_messages():
                packet_type = packet.get("type")
                if packet_type == "room_state":
                    self.room_state = packet.get("room")
                elif packet_type == "match_started":
                    summary = packet.get("summary") or {}
                    ai_added = int(summary.get("ai_added", 0))
                    self.message = f"Host started match. AI backfill: {ai_added}."
                elif packet_type == "match_sync":
                    self._maybe_enter_network_match(now_ms, packet)
                elif packet_type == "disconnected":
                    self.message = str(packet.get("message", "Disconnected from host."))
                    self._leave_room(keep_message=True)
        self._refresh_lobby_cache()
        return None

    def draw(self, screen: pygame.Surface, now_ms: int) -> None:
        draw_theme_background(screen)
        screen_rect = screen.get_rect()
        title_font = theme_font(screen_rect.width, screen_rect.height, 72, bold=True)
        section_font = theme_font(screen_rect.width, screen_rect.height, 32, bold=True)
        body_font = theme_font(screen_rect.width, screen_rect.height, 24)
        small_font = theme_font(screen_rect.width, screen_rect.height, 20)

        title = title_font.render("MULTIPLAYER", True, (245, 245, 245))
        screen.blit(title, title.get_rect(midtop=(screen_rect.centerx, 24)))

        panel = pygame.Rect(0, 0, min(1180, screen_rect.width - 96), screen_rect.height - 170)
        panel.center = (screen_rect.centerx, screen_rect.centery + 38)
        draw_theme_panel(screen, panel, alpha=146)

        if self.mode == self.MODE_LOBBY:
            self._draw_lobby(screen, panel, section_font, body_font, small_font)
        elif self.mode == self.MODE_CREATE:
            self._draw_create_form(screen, panel, section_font, body_font, small_font)
        else:
            self._draw_room_view(screen, panel, section_font, body_font, small_font)

    def _draw_lobby(
        self,
        screen: pygame.Surface,
        panel: pygame.Rect,
        section_font: pygame.font.Font,
        body_font: pygame.font.Font,
        small_font: pygame.font.Font,
    ) -> None:
        screen_rect = screen.get_rect()
        label = section_font.render("Active Rooms", True, (238, 242, 246))
        screen.blit(label, (panel.x + 28, panel.y + 18))

        name_rect = self._player_name_input_rect(panel)
        pwd_rect = self._join_password_input_rect(panel)
        self._draw_input_box(screen, name_rect, f"Name: {self.player_name}", self.focus_field == "player_name")
        join_password_text = "*" * len(self.join_password) if self.join_password else "(none)"
        self._draw_input_box(
            screen,
            pwd_rect,
            f"Join Password: {join_password_text}",
            self.focus_field == "join_password",
        )

        rows = self._room_row_rects(panel, len(self.lobby_rooms))
        if not rows:
            empty = body_font.render("No joinable rooms found on LAN.", True, (188, 200, 212))
            screen.blit(empty, (panel.x + 36, panel.y + 170))
        else:
            for idx, room in enumerate(self.lobby_rooms):
                row_rect = rows[idx]
                selected = room.room_id == self.selected_room_id
                fill = SETTINGS_ACTIVE_FILL if selected else SETTINGS_IDLE_FILL
                border = SETTINGS_ACTIVE_BORDER if selected else SETTINGS_IDLE_BORDER
                pygame.draw.rect(screen, fill, row_rect, border_radius=10)
                pygame.draw.rect(screen, border, row_rect, width=2, border_radius=10)

                pwd_mark = " (Locked)" if room.has_password else ""
                details = (
                    f"{room.room_name}{pwd_mark}  |  Host: {room.host_name}  |  "
                    f"Players: {room.human_count}/{room.capacity}"
                )
                text = small_font.render(details, True, (244, 246, 248))
                screen.blit(text, text.get_rect(midleft=(row_rect.x + 14, row_rect.centery)))

        buttons = self._lobby_button_rects(panel)
        draw_theme_button(
            screen,
            buttons["create"],
            "Create Room",
            SETTINGS_ACTIVE_FILL,
            SETTINGS_ACTIVE_BORDER,
        )
        draw_theme_button(
            screen,
            buttons["join"],
            "Join Selected",
            (70, 130, 225),
            (158, 194, 246),
        )
        draw_theme_button(
            screen,
            buttons["back"],
            "Back",
            SETTINGS_DANGER_FILL,
            SETTINGS_DANGER_BORDER,
        )

        footer = small_font.render(self.message, True, (234, 213, 145))
        screen.blit(footer, footer.get_rect(midbottom=(screen_rect.centerx, screen_rect.bottom - 16)))

    def _draw_create_form(
        self,
        screen: pygame.Surface,
        panel: pygame.Rect,
        section_font: pygame.font.Font,
        body_font: pygame.font.Font,
        small_font: pygame.font.Font,
    ) -> None:
        header_font = theme_font(screen.get_width(), screen.get_height(), 40, bold=True)
        header = header_font.render("Create Room", True, (238, 242, 246))
        screen.blit(header, header.get_rect(midtop=(panel.centerx, panel.y + 26)))

        room_name_rect = pygame.Rect(panel.centerx - 310, panel.y + 136, 620, 64)
        room_pwd_rect = pygame.Rect(panel.centerx - 310, panel.y + 224, 620, 64)
        self._draw_input_box(
            screen,
            room_name_rect,
            f"Room Name: {self.create_room_name}",
            self.focus_field == "create_room_name",
            font_size=30,
        )
        create_pwd_text = "*" * len(self.create_password) if self.create_password else "(none)"
        self._draw_input_box(
            screen,
            room_pwd_rect,
            f"Password: {create_pwd_text}",
            self.focus_field == "create_password",
            font_size=30,
        )

        buttons = self._create_button_rects(panel)
        draw_theme_button(
            screen,
            buttons["confirm"],
            "Host Room",
            SETTINGS_ACTIVE_FILL,
            SETTINGS_ACTIVE_BORDER,
            font_size=30,
        )
        draw_theme_button(
            screen,
            buttons["cancel"],
            "Cancel",
            SETTINGS_DANGER_FILL,
            SETTINGS_DANGER_BORDER,
            font_size=30,
        )
        msg = small_font.render(self.message, True, (234, 213, 145))
        screen.blit(msg, msg.get_rect(midbottom=(panel.centerx, panel.bottom - 16)))

    def _draw_room_view(
        self,
        screen: pygame.Surface,
        panel: pygame.Rect,
        section_font: pygame.font.Font,
        body_font: pygame.font.Font,
        small_font: pygame.font.Font,
    ) -> None:
        room = self.room_state or {}
        room_name = str(room.get("room_name", "Room"))
        room_id = str(room.get("room_id", "------"))
        capacity = int(room.get("capacity", 0) or 0)
        human_count = int(room.get("human_count", 0) or 0)
        ai_count = int(room.get("ai_count", 0) or 0)
        started = bool(room.get("started", False))

        title = section_font.render(f"{room_name} [{room_id}]", True, (238, 242, 246))
        screen.blit(title, title.get_rect(midtop=(panel.centerx, panel.y + 22)))

        detail = body_font.render(
            f"Humans: {human_count}/{capacity}  |  Open Slots: {max(0, capacity - human_count)}  |  AI Added: {ai_count}",
            True,
            (222, 230, 238),
        )
        screen.blit(detail, detail.get_rect(midtop=(panel.centerx, panel.y + 76)))

        player_panel = pygame.Rect(panel.x + 36, panel.y + 130, panel.width - 72, panel.height - 280)
        draw_theme_panel(screen, player_panel, alpha=120)
        player_title = body_font.render("Players in Room", True, (242, 246, 250))
        screen.blit(player_title, player_title.get_rect(midtop=(player_panel.centerx, player_panel.y + 16)))

        players = room.get("players", []) if isinstance(room.get("players"), list) else []
        for idx, player in enumerate(players):
            name = str(player.get("display_name", f"Player {idx + 1}"))
            mark = " (Host)" if player.get("is_host") else ""
            line = small_font.render(f"{idx + 1}. {name}{mark}", True, (232, 236, 242))
            screen.blit(line, line.get_rect(midleft=(player_panel.x + 28, player_panel.y + 58 + idx * 34)))

        if started:
            start_note = small_font.render(
                "Match started. Opening synchronized gameplay...",
                True,
                (245, 213, 150),
            )
            screen.blit(start_note, start_note.get_rect(midtop=(panel.centerx, player_panel.bottom + 18)))

        buttons = self._room_button_rects(panel, started=started)
        if self.is_host and not started:
            draw_theme_button(
                screen,
                buttons["start"],
                "Start Match",
                SETTINGS_ACTIVE_FILL,
                SETTINGS_ACTIVE_BORDER,
            )
        draw_theme_button(
            screen,
            buttons["leave"],
            "Leave Room",
            SETTINGS_DANGER_FILL,
            SETTINGS_DANGER_BORDER,
        )
        status = small_font.render(self.message, True, (234, 213, 145))
        screen.blit(status, status.get_rect(midbottom=(panel.centerx, panel.bottom - 16)))

    def _refresh_lobby_cache(self) -> None:
        browser = getattr(self, "_browser", None)
        if browser is None:
            browser = LobbyBrowser()
            self._browser = browser
        self.lobby_rooms = browser.list_rooms()
        if self.selected_room_id and all(room.room_id != self.selected_room_id for room in self.lobby_rooms):
            self.selected_room_id = None

    def _close_network(self) -> None:
        if self.client is not None:
            self.client.close()
            self.client = None
        if self.host is not None:
            self.host.close()
            self.host = None
        browser = getattr(self, "_browser", None)
        if browser is not None:
            browser.close()
            self._browser = None
        self.room_state = None
        self.is_host = False

    def _leave_room(self, keep_message: bool = False) -> None:
        if self.client is not None:
            self.client.send({"type": "leave"})
            self.client.close()
            self.client = None
        if self.host is not None:
            self.host.leave_host()
            self.host = None
        self.room_state = None
        self.is_host = False
        self.mode = self.MODE_LOBBY
        self.focus_field = "player_name"
        if not keep_message:
            self.message = "Returned to lobby."

    def _handle_text_input(self, event: pygame.event.Event) -> None:
        if event.key == pygame.K_TAB:
            if self.mode == self.MODE_LOBBY:
                self.focus_field = "join_password" if self.focus_field == "player_name" else "player_name"
            elif self.mode == self.MODE_CREATE:
                self.focus_field = "create_password" if self.focus_field == "create_room_name" else "create_room_name"
            return

        if event.key == pygame.K_BACKSPACE:
            self._delete_last_char()
            return

        char = event.unicode
        if not char or char == "\r":
            return
        if ord(char) < 32 or ord(char) > 126:
            return
        self._append_char(char)

    def _append_char(self, char: str) -> None:
        if self.focus_field == "player_name":
            self.player_name = (self.player_name + char)[:20]
        elif self.focus_field == "join_password":
            self.join_password = (self.join_password + char)[:24]
        elif self.focus_field == "create_room_name":
            self.create_room_name = (self.create_room_name + char)[:28]
        elif self.focus_field == "create_password":
            self.create_password = (self.create_password + char)[:24]

    def _delete_last_char(self) -> None:
        if self.focus_field == "player_name":
            self.player_name = self.player_name[:-1]
        elif self.focus_field == "join_password":
            self.join_password = self.join_password[:-1]
        elif self.focus_field == "create_room_name":
            self.create_room_name = self.create_room_name[:-1]
        elif self.focus_field == "create_password":
            self.create_password = self.create_password[:-1]

    def _handle_lobby_click(self, mouse_pos: tuple[int, int], screen_rect: pygame.Rect) -> Optional[ScreenResult]:
        panel = self._main_panel_rect(screen_rect)
        name_rect = self._player_name_input_rect(panel)
        pwd_rect = self._join_password_input_rect(panel)
        if name_rect.collidepoint(mouse_pos):
            self.focus_field = "player_name"
            return None
        if pwd_rect.collidepoint(mouse_pos):
            self.focus_field = "join_password"
            return None

        rows = self._room_row_rects(panel, len(self.lobby_rooms))
        for idx, rect in enumerate(rows):
            if rect.collidepoint(mouse_pos):
                self.selected_room_id = self.lobby_rooms[idx].room_id
                room = self.lobby_rooms[idx]
                self.message = f"Selected room {room.room_name} ({room.human_count}/{room.capacity})."
                return None

        buttons = self._lobby_button_rects(panel)
        if buttons["back"].collidepoint(mouse_pos):
            self._close_network()
            return ScreenResult(next_screen=TitleScreen(self.atlas, self.audio_settings))
        if buttons["create"].collidepoint(mouse_pos):
            self.mode = self.MODE_CREATE
            self.focus_field = "create_room_name"
            self.message = "Choose room parameters and host."
            return None
        if buttons["join"].collidepoint(mouse_pos):
            self._join_selected_room()
            return None
        return None

    def _join_selected_room(self) -> None:
        selected = next((room for room in self.lobby_rooms if room.room_id == self.selected_room_id), None)
        if selected is None:
            self.message = "Select a room first."
            return

        if self.host is not None:
            self.host.close()
            self.host = None
        if self.client is not None:
            self.client.close()

        client = MultiplayerClient(self.player_name)
        ok, message, room_state = client.connect_and_join(
            host_address=selected.host_address,
            host_port=selected.host_port,
            room_id=selected.room_id,
            password=self.join_password,
        )
        if not ok or room_state is None:
            client.close()
            self.client = None
            self.message = message
            return
        self.client = client
        self.room_state = room_state
        self.mode = self.MODE_ROOM
        self.is_host = False
        self.message = message

    def _handle_create_click(self, mouse_pos: tuple[int, int], screen_rect: pygame.Rect) -> Optional[ScreenResult]:
        panel = self._main_panel_rect(screen_rect)
        room_name_rect = pygame.Rect(panel.centerx - 290, panel.y + 130, 580, 58)
        room_pwd_rect = pygame.Rect(panel.centerx - 290, panel.y + 214, 580, 58)
        if room_name_rect.collidepoint(mouse_pos):
            self.focus_field = "create_room_name"
            return None
        if room_pwd_rect.collidepoint(mouse_pos):
            self.focus_field = "create_password"
            return None

        cap_rects = self._capacity_button_rects(panel)
        for capacity, rect in cap_rects.items():
            if rect.collidepoint(mouse_pos):
                self.create_capacity = capacity
                self.message = f"Capacity set to {capacity}."
                return None

        buttons = self._create_button_rects(panel)
        if buttons["cancel"].collidepoint(mouse_pos):
            self.mode = self.MODE_LOBBY
            self.focus_field = "player_name"
            self.message = "Room creation canceled."
            return None
        if buttons["confirm"].collidepoint(mouse_pos):
            host_setup = MultiplayerHostSetup(
                player_name=self.player_name.strip() or "Host",
                room_name=self.create_room_name.strip() or "UNO Room",
                password=self.create_password,
                capacity=self.create_capacity,
            )
            settings_screen = GameSettingsScreen(
                self.atlas,
                self.audio_settings,
                multiplayer_host_setup=host_setup,
            )
            settings_screen.message = "Configure the match, then start hosting."
            return ScreenResult(next_screen=settings_screen)
        return None

    def _handle_room_click(
        self,
        mouse_pos: tuple[int, int],
        screen_rect: pygame.Rect,
        now_ms: int,
    ) -> Optional[ScreenResult]:
        panel = self._main_panel_rect(screen_rect)
        room = self.room_state or {}
        started = bool(room.get("started", False))
        buttons = self._room_button_rects(panel, started=started)

        if buttons["leave"].collidepoint(mouse_pos):
            self._leave_room()
            return None

        if self.is_host and not started and buttons["start"].collidepoint(mouse_pos):
            self._start_host_match(now_ms)
            return None
        return None

    def _start_host_match(self, now_ms: int) -> None:
        if self.host is None:
            self.message = "Host room not available."
            return
        ok, message, summary = self.host.start_match()
        self.message = message
        self.room_state = self.host.room_state
        if not ok or summary is None:
            return
        sync_packet = self.host.current_match_sync()
        if sync_packet is not None:
            self._maybe_enter_network_match(now_ms, sync_packet)

    def _maybe_enter_network_match(self, now_ms: int, sync_packet: dict[str, Any]) -> None:
        game_payload = sync_packet.get("game")
        if not isinstance(game_payload, dict):
            return
        room_payload = sync_packet.get("room")
        if isinstance(room_payload, dict):
            self.room_state = room_payload
        started = bool((self.room_state or {}).get("started", False))
        if not started:
            return

        local_canonical_player_id = 0
        if self.is_host and self.host is not None:
            local_canonical_player_id = 0
        elif self.client is not None and self.client.seat_index is not None:
            local_canonical_player_id = self.client.seat_index

        remapped_payload = _remap_game_payload_to_local_view(game_payload, local_canonical_player_id)
        game = deserialize_game_state(remapped_payload)
        seat_names_payload = sync_packet.get("seat_names", {})
        seat_names: dict[int, str] = {}
        if isinstance(seat_names_payload, dict):
            for key, value in seat_names_payload.items():
                try:
                    canonical_id = int(key)
                except (TypeError, ValueError):
                    continue
                view_id = _canonical_to_view_player(canonical_id, local_canonical_player_id, game.num_players)
                seat_names[view_id] = str(value)

        next_screen = MultiplayerPlayingScreen(
            atlas=self.atlas,
            game=game,
            audio_settings=self.audio_settings,
            is_host=self.is_host,
            host=self.host,
            client=self.client,
            local_canonical_player_id=local_canonical_player_id,
            room_state=self.room_state or {},
            seat_names=seat_names,
            initial_seq=int(sync_packet.get("seq", 0) or 0),
            next_ai_time=now_ms + PlayingScreen.AI_TURN_DELAY_MS,
        )
        self.host = None
        self.client = None
        self._pending_next_screen = next_screen

    @staticmethod
    def _main_panel_rect(screen_rect: pygame.Rect) -> pygame.Rect:
        panel = pygame.Rect(0, 0, min(1180, screen_rect.width - 96), screen_rect.height - 170)
        panel.center = (screen_rect.centerx, screen_rect.centery + 38)
        return panel

    @staticmethod
    def _player_name_input_rect(panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.x + 28, panel.y + 72, 420, 56)

    @staticmethod
    def _join_password_input_rect(panel: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(panel.x + 464, panel.y + 72, 420, 56)

    @staticmethod
    def _room_row_rects(panel: pygame.Rect, count: int) -> list[pygame.Rect]:
        row_h = 62
        start_y = panel.y + 150
        max_rows = max(0, min(7, count))
        return [pygame.Rect(panel.x + 28, start_y + idx * (row_h + 12), panel.width - 56, row_h) for idx in range(max_rows)]

    @staticmethod
    def _lobby_button_rects(panel: pygame.Rect) -> dict[str, pygame.Rect]:
        button_h = 62
        button_w = 228
        y = panel.bottom - button_h - 26
        gap = 18
        return {
            "create": pygame.Rect(panel.x + 28, y, button_w, button_h),
            "join": pygame.Rect(panel.x + 28 + button_w + gap, y, button_w, button_h),
            "back": pygame.Rect(panel.right - button_w - 28, y, button_w, button_h),
        }

    @staticmethod
    def _create_button_rects(panel: pygame.Rect) -> dict[str, pygame.Rect]:
        button_w = 240
        button_h = 64
        gap = 24
        y = panel.bottom - 114
        return {
            "confirm": pygame.Rect(panel.centerx - button_w - gap // 2, y, button_w, button_h),
            "cancel": pygame.Rect(panel.centerx + gap // 2, y, button_w, button_h),
        }

    @staticmethod
    def _capacity_button_rects(panel: pygame.Rect) -> dict[int, pygame.Rect]:
        button_w = 100
        button_h = 56
        y = panel.y + 296
        return {
            2: pygame.Rect(panel.centerx - 130, y, button_w, button_h),
            4: pygame.Rect(panel.centerx - 10, y, button_w, button_h),
        }

    @staticmethod
    def _room_button_rects(panel: pygame.Rect, started: bool) -> dict[str, pygame.Rect]:
        button_h = 62
        y = panel.bottom - 102
        leave_rect = pygame.Rect(panel.right - 280, y, 240, button_h)
        start_rect = pygame.Rect(panel.x + 40, y, 240, button_h)
        return {"start": start_rect, "leave": leave_rect}

    @staticmethod
    def _draw_input_box(
        screen: pygame.Surface,
        rect: pygame.Rect,
        text: str,
        focused: bool,
        font_size: int = 26,
    ) -> None:
        fill = (42, 52, 66) if not focused else (55, 76, 92)
        border = SETTINGS_ACTIVE_BORDER if focused else SETTINGS_IDLE_BORDER
        draw_theme_button(
            screen,
            rect,
            text,
            fill,
            border,
            text_color=(236, 241, 246),
            selected=focused,
            font_size=font_size,
        )


class EndScreen(BaseScreen):
    state_name = STATE_END

    def __init__(self, atlas: CardSpriteAtlas, game: UnoGameManager, audio_settings: AudioSettings) -> None:
        self.atlas = atlas
        self.game = game
        self.audio_settings = audio_settings

    def handle_events(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
        now_ms: int,
    ) -> ScreenResult:
        for event in events:
            if event.type == pygame.QUIT:
                return ScreenResult(running=False)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_pos = event.pos
                for button_name, rect in get_end_screen_button_rects(screen.get_rect()).items():
                    if rect.collidepoint(mouse_pos):
                        if button_name == "return_title":
                            return ScreenResult(next_screen=TitleScreen(self.atlas, self.audio_settings))
                        break

        return ScreenResult()

    def draw(self, screen: pygame.Surface, now_ms: int) -> None:
        render_end_screen(screen, self.game)


class GameSettingsScreen(BaseScreen):
    """Screen for configuring game settings before starting."""
    state_name = "settings"

    def __init__(
        self,
        atlas: CardSpriteAtlas,
        audio_settings: AudioSettings,
        multiplayer_host_setup: Optional[MultiplayerHostSetup] = None,
        settings: Optional[GameSettings] = None,
    ) -> None:
        self.atlas = atlas
        self.audio_settings = audio_settings
        self.settings = settings if settings is not None else GameSettings()
        self.multiplayer_host_setup = multiplayer_host_setup
        if self.multiplayer_host_setup is not None:
            self.settings.num_players = self.multiplayer_host_setup.capacity
        self.message = ""
        self.dragging_initial_cards = False
        self.dragging_rule_8_timer = False

    @staticmethod
    def _draw_button(
        screen: pygame.Surface,
        rect: pygame.Rect,
        label: str,
        fill: tuple[int, int, int],
        border: tuple[int, int, int],
    ) -> None:
        draw_theme_button(screen, rect, label, fill, border)

    @staticmethod
    def _section_x(screen_rect: pygame.Rect) -> int:
        return screen_rect.centerx - SETTINGS_LABEL_X_OFFSET

    @staticmethod
    def _get_bottom_button_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
        button_w = 240
        button_h = 64
        spacing = 40
        y = screen_rect.height - button_h - 50
        total_width = button_w * 2 + spacing
        left_x = screen_rect.centerx - total_width // 2
        return {
            "start_game": pygame.Rect(left_x, y, button_w, button_h),
            "back": pygame.Rect(left_x + button_w + spacing, y, button_w, button_h),
        }

    @staticmethod
    def _get_player_count_rects(screen_rect: pygame.Rect) -> dict[int, pygame.Rect]:
        start_y = max(150, int(screen_rect.height * 0.19))
        button_w = 86
        button_h = 70
        center_x = screen_rect.centerx
        return {
            2: pygame.Rect(center_x - button_w - 32, start_y, button_w, button_h),
            4: pygame.Rect(center_x + 32, start_y, button_w, button_h),
        }

    @staticmethod
    def _get_initial_cards_slider_rect(screen_rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(screen_rect.centerx - SETTINGS_SLIDER_WIDTH // 2, int(screen_rect.height * 0.34), SETTINGS_SLIDER_WIDTH, 30)

    @staticmethod
    def _get_rule_toggle_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
        start_y = int(screen_rect.height * 0.51)
        button_w = 120
        button_h = 60
        spacing = 26
        center_x = screen_rect.centerx
        return {
            "rule_0": pygame.Rect(center_x - button_w - spacing - button_w // 2, start_y, button_w, button_h),
            "rule_7": pygame.Rect(center_x - button_w // 2, start_y, button_w, button_h),
            "rule_8": pygame.Rect(center_x + button_w // 2 + spacing, start_y, button_w, button_h),
        }

    @staticmethod
    def _get_rule_8_timer_slider_rect(screen_rect: pygame.Rect) -> pygame.Rect:
        return pygame.Rect(screen_rect.centerx - SETTINGS_SLIDER_WIDTH // 2, int(screen_rect.height * 0.66), SETTINGS_SLIDER_WIDTH, 30)

    @staticmethod
    def _get_two_player_behavior_rects(screen_rect: pygame.Rect, show_rule_8_timer: bool) -> dict[str, pygame.Rect]:
        button_width = 180
        button_height = 60
        spacing = 30
        preferred_y = int(screen_rect.height * (0.79 if show_rule_8_timer else 0.66))
        bottom_rects = GameSettingsScreen._get_bottom_button_rects(screen_rect)
        bottom_top = min(r.top for r in bottom_rects.values())
        start_y = min(preferred_y, bottom_top - button_height - 12)
        block_x = screen_rect.centerx - (button_width * 2 + spacing) // 2
        return {
            "skip": pygame.Rect(block_x, start_y, button_width, button_height),
            "reverse": pygame.Rect(block_x + button_width + spacing, start_y, button_width, button_height),
        }

    @classmethod
    def _draw_slider(cls, screen: pygame.Surface, rect: pygame.Rect, value_ratio: float) -> None:
        pygame.draw.rect(screen, (31, 40, 52), rect, border_radius=8)
        pygame.draw.rect(screen, SETTINGS_IDLE_BORDER, rect, width=2, border_radius=8)
        knob_x = rect.x + value_ratio * rect.width
        pygame.draw.circle(screen, (0, 0, 0), (int(knob_x), rect.centery + 2), 17)
        pygame.draw.circle(screen, SETTINGS_ACTIVE_FILL, (int(knob_x), rect.centery), 15)
        pygame.draw.circle(screen, SETTINGS_ACTIVE_BORDER, (int(knob_x), rect.centery), 15, width=2)

    def handle_events(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
        now_ms: int,
    ) -> ScreenResult:
        for event in events:
            if event.type == pygame.QUIT:
                return ScreenResult(running=False)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_pos = event.pos
                
                # Player count buttons
                for count, rect in self._get_player_count_rects(screen.get_rect()).items():
                    if rect.collidepoint(mouse_pos):
                        self.settings.num_players = count
                        break
                
                # Initial cards slider
                slider_rect = self._get_initial_cards_slider_rect(screen.get_rect())
                if slider_rect.collidepoint(mouse_pos):
                    self.dragging_initial_cards = True
                
                # Rule toggles
                for rule, rect in self._get_rule_toggle_rects(screen.get_rect()).items():
                    if rect.collidepoint(mouse_pos):
                        if rule == "rule_0":
                            self.settings.rule_0_enabled = not self.settings.rule_0_enabled
                        elif rule == "rule_7":
                            self.settings.rule_7_enabled = not self.settings.rule_7_enabled
                        elif rule == "rule_8":
                            self.settings.rule_8_enabled = not self.settings.rule_8_enabled
                        break
                
                # Rule 8 timer slider
                timer_rect = self._get_rule_8_timer_slider_rect(screen.get_rect())
                if timer_rect.collidepoint(mouse_pos) and self.settings.rule_8_enabled:
                    self.dragging_rule_8_timer = True
                
                # 2-player behavior buttons
                if self.settings.num_players == 2:
                    for behavior, rect in self._get_two_player_behavior_rects(
                        screen.get_rect(),
                        show_rule_8_timer=self.settings.rule_8_enabled,
                    ).items():
                        if rect.collidepoint(mouse_pos):
                            self.settings.two_player_reverse_behavior = behavior
                            break
                
                # Back button
                back_rect = self._get_bottom_button_rects(screen.get_rect())["back"]
                if back_rect.collidepoint(mouse_pos):
                    if self.multiplayer_host_setup is not None:
                        lobby_screen = MultiplayerScreen(self.atlas, self.audio_settings)
                        lobby_screen.mode = MultiplayerScreen.MODE_CREATE
                        lobby_screen.player_name = self.multiplayer_host_setup.player_name
                        lobby_screen.create_room_name = self.multiplayer_host_setup.room_name
                        lobby_screen.create_password = self.multiplayer_host_setup.password
                        lobby_screen.create_capacity = self.multiplayer_host_setup.capacity
                        lobby_screen.message = "Choose room parameters and host."
                        return ScreenResult(next_screen=lobby_screen)
                    return ScreenResult(next_screen=TitleScreen(self.atlas, self.audio_settings))
                
                # Next button — proceed to extension pack selection
                start_rect = self._get_bottom_button_rects(screen.get_rect())["start_game"]
                if start_rect.collidepoint(mouse_pos):
                    return ScreenResult(next_screen=ExtensionPackScreen(
                        self.atlas, self.audio_settings, self.settings, self.multiplayer_host_setup
                    ))

            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.dragging_initial_cards = False
                self.dragging_rule_8_timer = False

            elif event.type == pygame.MOUSEMOTION:
                if self.dragging_initial_cards:
                    slider_rect = self._get_initial_cards_slider_rect(screen.get_rect())
                    relative_x = max(0, min(1.0, (event.pos[0] - slider_rect.x) / slider_rect.width))
                    self.settings.initial_cards = int(2 + relative_x * 13)
                
                if self.dragging_rule_8_timer and self.settings.rule_8_enabled:
                    timer_rect = self._get_rule_8_timer_slider_rect(screen.get_rect())
                    relative_x = max(0, min(1.0, (event.pos[0] - timer_rect.x) / timer_rect.width))
                    raw_timer_ms = 1000 + relative_x * 4000
                    snapped_timer_ms = round((raw_timer_ms - 1000) / 250) * 250 + 1000
                    self.settings.rule_8_reaction_timer_ms = max(1000, min(5000, int(snapped_timer_ms)))

        return ScreenResult()

    def draw(self, screen: pygame.Surface, now_ms: int) -> None:
        draw_theme_background(screen)
        screen_rect = screen.get_rect()
        section_x = self._section_x(screen_rect)

        font_title = theme_font(screen_rect.width, screen_rect.height, 72, bold=True)
        font_section = theme_font(screen_rect.width, screen_rect.height, 34, bold=True)
        font_label = theme_font(screen_rect.width, screen_rect.height, 28)

        panel_rect = pygame.Rect(0, 0, min(1180, screen_rect.width - 96), screen_rect.height - 170)
        panel_rect.center = (screen_rect.centerx, screen_rect.centery + 38)
        draw_theme_panel(screen, panel_rect, alpha=146)

        title = font_title.render("GAME SETTINGS", True, (255, 255, 255))
        screen.blit(title, title.get_rect(midtop=(screen_rect.centerx, max(18, int(screen_rect.height * 0.032)))))

        label = font_section.render("Players:", True, (255, 255, 255))
        player_rects = self._get_player_count_rects(screen_rect)
        screen.blit(label, (section_x, player_rects[2].y - 46))
        for count, rect in player_rects.items():
            fill = SETTINGS_ACTIVE_FILL if count == self.settings.num_players else SETTINGS_IDLE_FILL
            border = SETTINGS_ACTIVE_BORDER if count == self.settings.num_players else SETTINGS_IDLE_BORDER
            self._draw_button(screen, rect, str(count), fill, border)

        label = font_section.render("Initial Cards:", True, (255, 255, 255))
        slider_rect = self._get_initial_cards_slider_rect(screen_rect)
        screen.blit(label, (section_x, slider_rect.y - 54))
        self._draw_slider(screen, slider_rect, (self.settings.initial_cards - 2) / 13)
        cards_text = font_label.render(f"{self.settings.initial_cards}", True, (255, 255, 255))
        screen.blit(cards_text, (slider_rect.right + 40, slider_rect.centery - 14))

        label = font_section.render("Rules:", True, (255, 255, 255))
        rule_rects = self._get_rule_toggle_rects(screen_rect)
        screen.blit(label, (section_x, rule_rects["rule_0"].y - 54))
        rule_labels = {"rule_0": "Rule 0", "rule_7": "Rule 7", "rule_8": "Rule 8"}
        rule_states = {
            "rule_0": self.settings.rule_0_enabled,
            "rule_7": self.settings.rule_7_enabled,
            "rule_8": self.settings.rule_8_enabled,
        }
        for key, rect in rule_rects.items():
            enabled = rule_states[key]
            fill = SETTINGS_ACTIVE_FILL if enabled else SETTINGS_IDLE_FILL
            border = SETTINGS_ACTIVE_BORDER if enabled else SETTINGS_IDLE_BORDER
            self._draw_button(screen, rect, rule_labels[key], fill, border)

        if self.settings.rule_8_enabled:
            timer_rect = self._get_rule_8_timer_slider_rect(screen_rect)
            label = font_section.render("Rule 8 Timer (ms):", True, (255, 255, 255))
            screen.blit(label, (section_x, timer_rect.y - 54))
            self._draw_slider(screen, timer_rect, (self.settings.rule_8_reaction_timer_ms - 1000) / 4000)
            timer_text = font_label.render(f"{self.settings.rule_8_reaction_timer_ms}ms", True, (255, 255, 255))
            screen.blit(timer_text, (timer_rect.right + 40, timer_rect.centery - 14))

        if self.settings.num_players == 2:
            behavior_rects = self._get_two_player_behavior_rects(
                screen_rect,
                show_rule_8_timer=self.settings.rule_8_enabled,
            )
            label = font_section.render("2-Player Reverse Rule:", True, (255, 255, 255))
            screen.blit(label, (section_x, behavior_rects["skip"].y - 54))
            for behavior, rect in behavior_rects.items():
                selected = behavior == self.settings.two_player_reverse_behavior
                fill = SETTINGS_ACTIVE_FILL if selected else SETTINGS_IDLE_FILL
                border = SETTINGS_ACTIVE_BORDER if selected else SETTINGS_IDLE_BORDER
                self._draw_button(screen, rect, behavior.capitalize(), fill, border)

        button_rects = self._get_bottom_button_rects(screen_rect)
        self._draw_button(screen, button_rects["back"], "BACK", SETTINGS_DANGER_FILL, SETTINGS_DANGER_BORDER)
        self._draw_button(screen, button_rects["start_game"], "NEXT", SETTINGS_ACTIVE_FILL, SETTINGS_ACTIVE_BORDER)
        if self.message:
            footer = font_label.render(self.message, True, (234, 213, 145))
            footer_y = button_rects["back"].top - 18
            screen.blit(footer, footer.get_rect(midbottom=(screen_rect.centerx, footer_y)))


class ExtensionPackScreen(BaseScreen):
    """Screen for selecting extension packs, shown between GameSettingsScreen and game start."""
    state_name = "extension_packs"

    def __init__(
        self,
        atlas: CardSpriteAtlas,
        audio_settings: AudioSettings,
        settings: GameSettings,
        multiplayer_host_setup: Optional[MultiplayerHostSetup] = None,
    ) -> None:
        self.atlas = atlas
        self.audio_settings = audio_settings
        self.settings = settings
        self.multiplayer_host_setup = multiplayer_host_setup
        self.message = ""

    @staticmethod
    def _get_bottom_button_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
        button_w = 240
        button_h = 64
        spacing = 40
        y = screen_rect.height - button_h - 50
        total_width = button_w * 2 + spacing
        left_x = screen_rect.centerx - total_width // 2
        return {
            "start_game": pygame.Rect(left_x, y, button_w, button_h),
            "back": pygame.Rect(left_x + button_w + spacing, y, button_w, button_h),
        }

    @staticmethod
    def _get_mixi_toggle_rect(screen_rect: pygame.Rect) -> pygame.Rect:
        bottom_rects = ExtensionPackScreen._get_bottom_button_rects(screen_rect)
        bottom_top = min(r.top for r in bottom_rects.values())
        button_w = 280
        button_h = 54
        return pygame.Rect(screen_rect.centerx - button_w // 2, bottom_top - button_h - 80, button_w, button_h)

    def handle_events(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
        now_ms: int,
    ) -> ScreenResult:
        for event in events:
            if event.type == pygame.QUIT:
                return ScreenResult(running=False)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_pos = event.pos

                # Calculate mixi rect the same way as in draw()
                screen_rect = screen.get_rect()
                panel_rect = pygame.Rect(0, 0, min(900, screen_rect.width - 96), screen_rect.height - 170)
                panel_rect.center = (screen_rect.centerx, screen_rect.centery + 38)
                
                desc_y = panel_rect.top + 40
                mixi_label_y = desc_y + 80
                mixi_button_y = mixi_label_y + 50
                
                button_w = 280
                button_h = 54
                mixi_rect = pygame.Rect(screen_rect.centerx - button_w // 2, mixi_button_y, button_w, button_h)
                
                if mixi_rect.collidepoint(mouse_pos):
                    if "mixi" in self.settings.extension_packs:
                        self.settings.extension_packs.remove("mixi")
                    else:
                        self.settings.extension_packs.append("mixi")

                back_rect = self._get_bottom_button_rects(screen.get_rect())["back"]
                if back_rect.collidepoint(mouse_pos):
                    return ScreenResult(next_screen=GameSettingsScreen(
                        self.atlas,
                        self.audio_settings,
                        multiplayer_host_setup=self.multiplayer_host_setup,
                        settings=self.settings,
                    ))

                start_rect = self._get_bottom_button_rects(screen.get_rect())["start_game"]
                if start_rect.collidepoint(mouse_pos):
                    if self.multiplayer_host_setup is not None:
                        try:
                            host = MultiplayerHost(
                                host_name=self.multiplayer_host_setup.player_name,
                                room_name=self.multiplayer_host_setup.room_name,
                                password=self.multiplayer_host_setup.password,
                                capacity=self.settings.num_players,
                            )
                        except OSError as exc:
                            self.message = f"Could not host room: {exc}"
                            return ScreenResult()
                        except ValueError as exc:
                            self.message = str(exc)
                            return ScreenResult()

                        multiplayer_screen = MultiplayerScreen(self.atlas, self.audio_settings)
                        multiplayer_screen.mode = MultiplayerScreen.MODE_ROOM
                        multiplayer_screen.is_host = True
                        multiplayer_screen.host = host
                        multiplayer_screen.room_state = host.room_state
                        multiplayer_screen.message = f"Room created. Share code: {host.room_id}"
                        return ScreenResult(next_screen=multiplayer_screen)

                    game = UnoGameManager(settings=self.settings)
                    return ScreenResult(
                        next_screen=PlayingScreen(
                            atlas=self.atlas,
                            game=game,
                            audio_settings=self.audio_settings,
                            last_message="Player 1 starts.",
                            next_ai_time=now_ms + PlayingScreen.AI_TURN_DELAY_MS,
                        )
                    )

        return ScreenResult()

    def draw(self, screen: pygame.Surface, now_ms: int) -> None:
        draw_theme_background(screen)
        screen_rect = screen.get_rect()
        section_x = GameSettingsScreen._section_x(screen_rect)

        font_title = theme_font(screen_rect.width, screen_rect.height, 72, bold=True)
        font_section = theme_font(screen_rect.width, screen_rect.height, 34, bold=True)
        font_label = theme_font(screen_rect.width, screen_rect.height, 28)

        # Title at the very top, like other config screens
        title = font_title.render("EXTENSION PACKS", True, (255, 255, 255))
        title_y = max(18, int(screen_rect.height * 0.032))
        screen.blit(title, title.get_rect(midtop=(screen_rect.centerx, title_y)))

        # Panel containing the controls
        panel_rect = pygame.Rect(0, 0, min(900, screen_rect.width - 96), screen_rect.height - 170)
        panel_rect.center = (screen_rect.centerx, screen_rect.centery + 38)
        draw_theme_panel(screen, panel_rect, alpha=146)

        # Description label inside the panel
        desc_label = font_section.render("Choose which expansion packs to enable:", True, (255, 255, 255))
        desc_y = panel_rect.top + 40
        screen.blit(desc_label, (panel_rect.left + 40, desc_y))

        # Mixi Pack label and button below the description text
        mixi_label_y = desc_y + 80
        label = font_section.render("Mixi Pack:", True, (255, 255, 255))
        screen.blit(label, (panel_rect.left + 40, mixi_label_y))

        mixi_button_y = mixi_label_y + 50
        button_w = 280
        button_h = 54
        mixi_rect = pygame.Rect(screen_rect.centerx - button_w // 2, mixi_button_y, button_w, button_h)
        mixi_enabled = "mixi" in self.settings.extension_packs
        mixi_fill = SETTINGS_ACTIVE_FILL if mixi_enabled else SETTINGS_IDLE_FILL
        mixi_border = SETTINGS_ACTIVE_BORDER if mixi_enabled else SETTINGS_IDLE_BORDER
        GameSettingsScreen._draw_button(screen, mixi_rect, f"Mixi Pack {'ON' if mixi_enabled else 'OFF'}", mixi_fill, mixi_border)

        button_rects = self._get_bottom_button_rects(screen_rect)
        GameSettingsScreen._draw_button(screen, button_rects["back"], "BACK", SETTINGS_DANGER_FILL, SETTINGS_DANGER_BORDER)
        GameSettingsScreen._draw_button(screen, button_rects["start_game"], "START GAME", SETTINGS_ACTIVE_FILL, SETTINGS_ACTIVE_BORDER)
        if self.message:
            footer = font_label.render(self.message, True, (234, 213, 145))
            footer_y = button_rects["back"].top - 18
            screen.blit(footer, footer.get_rect(midbottom=(screen_rect.centerx, footer_y)))


class MainSettingsScreen(BaseScreen):
    state_name = "main_settings"
    DISPLAY_WINDOWED = "windowed"
    DISPLAY_FULLSCREEN = "fullscreen"

    def __init__(self, atlas: CardSpriteAtlas, audio_settings: AudioSettings) -> None:
        self.atlas = atlas
        self.audio_settings = audio_settings
        self.dragging_slider: str | None = None

    @staticmethod
    def _draw_button(
        screen: pygame.Surface,
        rect: pygame.Rect,
        label: str,
        fill: tuple[int, int, int],
        border: tuple[int, int, int],
    ) -> None:
        GameSettingsScreen._draw_button(screen, rect, label, fill, border)

    @staticmethod
    def _slider_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
        start_y = int(screen_rect.height * 0.28)
        row_gap = int(screen_rect.height * 0.14)
        return {
            "master": pygame.Rect(screen_rect.centerx - SETTINGS_SLIDER_WIDTH // 2, start_y, SETTINGS_SLIDER_WIDTH, 30),
            "music": pygame.Rect(screen_rect.centerx - SETTINGS_SLIDER_WIDTH // 2, start_y + row_gap, SETTINGS_SLIDER_WIDTH, 30),
            "sfx": pygame.Rect(screen_rect.centerx - SETTINGS_SLIDER_WIDTH // 2, start_y + row_gap * 2, SETTINGS_SLIDER_WIDTH, 30),
        }

    @staticmethod
    def _display_mode_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
        button_w = 220
        button_h = 60
        spacing = 32
        bottom_buttons = MainSettingsScreen._button_rects(screen_rect)
        slider_rects = MainSettingsScreen._slider_rects(screen_rect)
        preferred_y = slider_rects["sfx"].bottom + max(68, int(screen_rect.height * 0.095))
        y = min(preferred_y, bottom_buttons["back"].y - button_h - 42)
        left_x = screen_rect.centerx - (button_w * 2 + spacing) // 2
        return {
            MainSettingsScreen.DISPLAY_WINDOWED: pygame.Rect(left_x, y, button_w, button_h),
            MainSettingsScreen.DISPLAY_FULLSCREEN: pygame.Rect(left_x + button_w + spacing, y, button_w, button_h),
        }

    @staticmethod
    def _button_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
        button_w = 240
        button_h = 64
        y = screen_rect.height - button_h - 50
        return {
            "back": pygame.Rect(screen_rect.centerx - button_w // 2, y, button_w, button_h),
        }

    def _set_slider_value(self, slider_name: str, rect: pygame.Rect, mouse_x: int) -> None:
        relative_x = max(0.0, min(1.0, (mouse_x - rect.x) / rect.width))
        stepped_value = round(relative_x * 100) / 100
        if slider_name == "master":
            self.audio_settings.master_volume = stepped_value
        elif slider_name == "music":
            self.audio_settings.music_volume = stepped_value
        elif slider_name == "sfx":
            self.audio_settings.sfx_volume = stepped_value

    @staticmethod
    def _current_display_mode() -> str:
        surface = pygame.display.get_surface()
        if surface is not None and surface.get_flags() & pygame.FULLSCREEN:
            return MainSettingsScreen.DISPLAY_FULLSCREEN
        return MainSettingsScreen.DISPLAY_WINDOWED

    def handle_events(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
        now_ms: int,
    ) -> ScreenResult:
        slider_rects = self._slider_rects(screen.get_rect())
        for event in events:
            if event.type == pygame.QUIT:
                return ScreenResult(running=False)

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                mouse_pos = event.pos
                for slider_name, rect in slider_rects.items():
                    if rect.collidepoint(mouse_pos):
                        self.dragging_slider = slider_name
                        self._set_slider_value(slider_name, rect, mouse_pos[0])
                        break

                for display_mode, rect in self._display_mode_rects(screen.get_rect()).items():
                    if rect.collidepoint(mouse_pos):
                        return ScreenResult(display_mode=display_mode)

                back_rect = self._button_rects(screen.get_rect())["back"]
                if back_rect.collidepoint(mouse_pos):
                    return ScreenResult(next_screen=TitleScreen(self.atlas, self.audio_settings))

            elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                self.dragging_slider = None

            elif event.type == pygame.MOUSEMOTION and self.dragging_slider is not None:
                self._set_slider_value(self.dragging_slider, slider_rects[self.dragging_slider], event.pos[0])

        return ScreenResult()

    def draw(self, screen: pygame.Surface, now_ms: int) -> None:
        draw_theme_background(screen)
        screen_rect = screen.get_rect()
        section_x = GameSettingsScreen._section_x(screen_rect)
        font_title = theme_font(screen_rect.width, screen_rect.height, 72, bold=True)
        font_section = theme_font(screen_rect.width, screen_rect.height, 34, bold=True)
        font_label = theme_font(screen_rect.width, screen_rect.height, 28)

        panel_top = max(92, int(screen_rect.height * 0.13))
        panel_bottom = screen_rect.height - 36
        panel_rect = pygame.Rect(0, panel_top, min(900, screen_rect.width - 96), panel_bottom - panel_top)
        panel_rect.centerx = screen_rect.centerx
        draw_theme_panel(screen, panel_rect, alpha=142)

        title = font_title.render("MAIN SETTINGS", True, (255, 255, 255))
        screen.blit(title, title.get_rect(midtop=(screen_rect.centerx, max(18, int(screen_rect.height * 0.032)))))

        slider_rects = self._slider_rects(screen_rect)
        slider_rows = (
            ("master", "Master Volume:", self.audio_settings.master_volume),
            ("music", "Music Volume:", self.audio_settings.music_volume),
            ("sfx", "SFX Volume:", self.audio_settings.sfx_volume),
        )
        for key, label_text, value in slider_rows:
            rect = slider_rects[key]
            label = font_section.render(label_text, True, (255, 255, 255))
            screen.blit(label, (section_x, rect.y - 54))
            GameSettingsScreen._draw_slider(screen, rect, value)
            value_text = font_label.render(f"{int(round(value * 100))}%", True, (255, 255, 255))
            screen.blit(value_text, (rect.right + 40, rect.centery - 14))

        display_rects = self._display_mode_rects(screen_rect)
        display_label = font_section.render("Display:", True, (255, 255, 255))
        screen.blit(display_label, (section_x, display_rects[self.DISPLAY_WINDOWED].y - 54))
        active_mode = self._current_display_mode()
        display_labels = {
            self.DISPLAY_WINDOWED: "WINDOWED",
            self.DISPLAY_FULLSCREEN: "FULLSCREEN",
        }
        for display_mode, rect in display_rects.items():
            selected = display_mode == active_mode
            fill = SETTINGS_ACTIVE_FILL if selected else SETTINGS_IDLE_FILL
            border = SETTINGS_ACTIVE_BORDER if selected else SETTINGS_IDLE_BORDER
            self._draw_button(screen, rect, display_labels[display_mode], fill, border)

        back_rect = self._button_rects(screen_rect)["back"]
        self._draw_button(screen, back_rect, "BACK", SETTINGS_DANGER_FILL, SETTINGS_DANGER_BORDER)


class PlayingScreen(BaseScreen):
    state_name = STATE_PLAYING
    AI_TURN_DELAY_MS = 1000
    DIRECTION_ARROW_BASE_SPEED = 90.0
    DIRECTION_ARROW_ACCEL = 4.5
    DIRECTION_ARROW_DECEL = 2.5
    SHAKE_DURATION_MS = 260
    SHAKE_MAX_OFFSET = 10
    UNO_FLASH_DURATION_MS = 950
    PAUSE_MENU_OPTIONS = ("resume", "return_title")

    def __init__(
        self,
        atlas: CardSpriteAtlas,
        game: UnoGameManager,
        audio_settings: AudioSettings,
        last_message: str = "",
        selected_index: int = 0,
        pending_wild_card_index: Optional[int] = None,
        next_ai_time: int = 0,
    ) -> None:
        self.atlas = atlas
        self.game = game
        self.audio_settings = audio_settings
        self.ai_rng = random.Random()
        self.selected_index = selected_index
        self.pending_wild_card_index = pending_wild_card_index
        self.last_message = last_message
        self.next_ai_time = next_ai_time
        self.reaction_ai_due_times: dict[int, int] = {}
        self.hovered_index: int | None = None
        self._last_update_ms: int | None = None
        self._hand_layout_initialized = False
        self.active_cards: list[ActiveCard] = []
        self.hidden_hand_card_ids: set[int] = set()
        self.display_top_card = game.top_discard
        self.direction_arrow_angle = 0.0
        self.direction_arrow_speed = self.DIRECTION_ARROW_BASE_SPEED * self.game.turn_direction
        self.visual_state = ""
        self.hand_transfer_animation: HandTransferAnimation | None = None
        self.wild_hovered_color: str | None = None
        self.pending_draw_decision_card: Card | None = None
        self.pending_draw_decision_choosing_color = False
        self.uno_catch_sound = self._load_uno_catch_sound()
        self.counter_card_sound = self._load_card_sound("counter.mp3")
        self.draw67_card_sound = self._load_card_sound("draw67.mp3")
        self.silence_card_sound = self._load_card_sound("silence.mp3")
        self.pause_menu_open = False
        self.pause_selected_index = 0
        self.pause_hovered_button: str | None = None
        self.screen_shake_remaining_ms = 0
        self.screen_shake_offset: tuple[int, int] = (0, 0)
        self.uno_flash_text = ""
        self.uno_flash_color: tuple[int, int, int] = (65, 175, 95)
        self.uno_flash_remaining_ms = 0
        self._shadow_cache: dict[tuple[int, int, int], pygame.Surface] = {}
        self._compact_hidden_ids: set[int] = set()
        self._compact_back_virtual_size: int = 0

    @property
    def wants_bgm(self) -> bool:
        return True

    def handle_events(
        self,
        events: list[pygame.event.Event],
        screen: pygame.Surface,
        now_ms: int,
    ) -> ScreenResult:
        for event in events:
            if event.type == pygame.QUIT:
                return ScreenResult(running=False)

            if event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                if self.pause_menu_open:
                    self.pause_menu_open = False
                    self.pause_hovered_button = None
                    self.last_message = "Game resumed."
                    continue

                if self.game.winner is None and not self._has_modal_input():
                    self.pause_menu_open = True
                    self.pause_selected_index = 0
                    self.pause_hovered_button = None
                    self.last_message = "Game paused."
                    continue

            if self.pause_menu_open:
                pause_result = self._handle_pause_menu_event(event, screen)
                if pause_result is not None:
                    return pause_result
                continue

            if self.hand_transfer_animation is not None:
                continue

            if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1 and self.game.winner is None:
                self._handle_mouse_down(event.pos, screen, now_ms)

            if (
                event.type == pygame.KEYDOWN
                and self.game.winner is None
                and self.game.current_player == 0
            ):
                self._handle_key_down(event, screen, now_ms)

        return ScreenResult()

    def update(self, screen: pygame.Surface, now_ms: int) -> Optional[BaseScreen]:
        dt = 0.0 if self._last_update_ms is None else max(0.0, (now_ms - self._last_update_ms) / 1000.0)
        self._last_update_ms = now_ms
        self._update_screen_shake(dt)
        self._update_uno_flash(dt)

        if self.game.winner is not None:
            return EndScreen(self.atlas, self.game, self.audio_settings)

        if self.pause_menu_open:
            return None

        self._schedule_reaction_ai(now_ms)
        self._submit_ai_reactions(now_ms)
        self._update_direction_arrow(dt)
        self._update_active_cards(dt)

        if self.hand_transfer_animation is not None:
            self.visual_state = HAND_TRANSFER_ANIMATION
            self._update_hand_transfer_animation(screen, dt, now_ms)
            if self.game.winner is not None and self.hand_transfer_animation is None:
                return EndScreen(self.atlas, self.game, self.audio_settings)
            return None

        self.visual_state = ""

        tick_message = self.game.tick(now_ms)
        if tick_message:
            self.last_message = tick_message

        self.hovered_index = None
        self.wild_hovered_color = None
        if self.game.pending_draw_decision_card is None:
            self.pending_draw_decision_card = None
            self.pending_draw_decision_choosing_color = False

        if self._wild_color_picker_active():
            self.wild_hovered_color = get_wild_color_at_pos(pygame.mouse.get_pos(), screen.get_rect())

        if (
            not self._has_modal_input()
            and self.game.winner is None
            and self.game.current_player == 0
        ):
            self.hovered_index = get_hovered_hand_index(
                pygame.mouse.get_pos(),
                self.game.player_hands[0],
                screen.get_width(),
                screen.get_height(),
                hidden_card_ids=self._effective_hidden_ids(),
            )

        self._update_player_hand_animation(screen, dt)

        if (
            self.game.winner is None
            and self.game.current_player != 0
            and self.pending_draw_decision_card is None
            and not self.active_cards
            and not self.game.is_animating
        ):
            if self.game.pending_effect in (RULE_ZERO_DIRECTION, RULE_SEVEN_TARGET):
                ai_choice = self._build_ai_hand_transfer_action()
                if ai_choice is not None:
                    self._begin_hand_transfer_animation(ai_choice, screen, now_ms)
            elif self.game.pending_effect is None and now_ms >= self.next_ai_time:
                previous_player = self.game.current_player
                ai_turn = perform_simple_ai_turn(self.game, now_ms=now_ms)
                self.last_message = ai_turn.message
                if ai_turn.result is not None and getattr(ai_turn.result, "uno_caught_player", None) is not None:
                    self._play_uno_catch_sound()
                    self._trigger_uno_flash(False)
                self._spawn_ai_animation(previous_player, ai_turn, screen, now_ms)
                ai_delay = self.ai_rng.randint(1000, 1500)
                self.next_ai_time = now_ms + ai_delay

        return None

    def draw(self, screen: pygame.Surface, now_ms: int) -> None:
        render_target = screen
        if self.screen_shake_remaining_ms > 0:
            render_target = pygame.Surface(screen.get_size(), pygame.SRCALPHA)

        if self._compact_back_virtual_size > 0:
            compact_back_rect = card_rect_for_hand(
                0, self._compact_back_virtual_size, render_target.get_width(), render_target.get_height()
            )
            compact_hidden_count = len(self._compact_hidden_ids)
        else:
            compact_back_rect = None
            compact_hidden_count = 0

        render_ui(
            render_target,
            self.game,
            self.atlas,
            now_ms,
            self.selected_index,
            self.last_message,
            hovered_index=self.hovered_index,
            wild_color_picker_active=self._wild_color_picker_active(),
            hidden_card_ids=self._effective_hidden_ids(),
            display_top_card=self.display_top_card,
            direction_arrow_angle=self.direction_arrow_angle,
            wild_hovered_color=self.wild_hovered_color,
            draw_decision_card=self.pending_draw_decision_card,
            player_names=self._player_name_map(),
            local_player_id=self._local_player_id(),
            compact_back_rect=compact_back_rect,
            compact_hidden_count=compact_hidden_count,
        )
        self._draw_active_cards(render_target)
        self._draw_hand_transfer_cards(render_target)

        if render_target is not screen:
            upscale_margin = 12
            scaled = pygame.transform.smoothscale(
                render_target,
                (screen.get_width() + upscale_margin, screen.get_height() + upscale_margin),
            )
            scaled_rect = scaled.get_rect(
                center=(
                    screen.get_width() // 2 + self.screen_shake_offset[0],
                    screen.get_height() // 2 + self.screen_shake_offset[1],
                )
            )
            screen.blit(scaled, scaled_rect)

        self._draw_uno_flash(screen)

        if self.pause_menu_open:
            self._draw_pause_menu(screen)

    def _player_name_map(self) -> dict[int, str] | None:
        return None

    def _local_player_id(self) -> int:
        return 0

    def _trigger_screen_shake(self, duration_ms: int | None = None) -> None:
        self.screen_shake_remaining_ms = max(self.screen_shake_remaining_ms, duration_ms or self.SHAKE_DURATION_MS)

    def _update_screen_shake(self, dt: float) -> None:
        if self.screen_shake_remaining_ms <= 0:
            self.screen_shake_remaining_ms = 0
            self.screen_shake_offset = (0, 0)
            return

        self.screen_shake_remaining_ms = max(0, self.screen_shake_remaining_ms - int(dt * 1000.0))
        intensity = max(1.0, (self.screen_shake_remaining_ms / self.SHAKE_DURATION_MS) * self.SHAKE_MAX_OFFSET)
        self.screen_shake_offset = (
            int(self.ai_rng.uniform(-intensity, intensity)),
            int(self.ai_rng.uniform(-intensity, intensity)),
        )

    def _trigger_uno_flash(self, success: bool) -> None:
        self.uno_flash_text = "tao tay` roi" if success else "chua tay` dau"
        self.uno_flash_color = (65, 190, 100) if success else (230, 55, 55)
        self.uno_flash_remaining_ms = self.UNO_FLASH_DURATION_MS

    def _update_uno_flash(self, dt: float) -> None:
        if self.uno_flash_remaining_ms <= 0:
            self.uno_flash_remaining_ms = 0
            return
        self.uno_flash_remaining_ms = max(0, self.uno_flash_remaining_ms - int(dt * 1000.0))

    def _draw_uno_flash(self, screen: pygame.Surface) -> None:
        if self.uno_flash_remaining_ms <= 0 or not self.uno_flash_text:
            return

        progress = self.uno_flash_remaining_ms / self.UNO_FLASH_DURATION_MS
        alpha = max(0, min(190, int(170 * progress)))
        overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        overlay.fill((*self.uno_flash_color, alpha))
        screen.blit(overlay, (0, 0))

        screen_rect = screen.get_rect()
        font = theme_font(screen_rect.width, screen_rect.height, 64, bold=True)
        text = font.render(self.uno_flash_text, True, (255, 255, 255))
        shadow = font.render(self.uno_flash_text, True, (20, 24, 28))
        center = screen_rect.center
        screen.blit(shadow, shadow.get_rect(center=(center[0] + 4, center[1] + 5)))
        screen.blit(text, text.get_rect(center=center))

    @staticmethod
    def _pause_button_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
        panel_width = min(560, screen_rect.width - 120)
        panel_height = 340
        panel_rect = pygame.Rect(0, 0, panel_width, panel_height)
        panel_rect.center = screen_rect.center

        button_w = min(340, panel_rect.width - 80)
        button_h = 66
        gap = 24
        left = panel_rect.centerx - button_w // 2
        first_y = panel_rect.y + 150
        return {
            "resume": pygame.Rect(left, first_y, button_w, button_h),
            "return_title": pygame.Rect(left, first_y + button_h + gap, button_w, button_h),
        }

    def _handle_pause_menu_event(
        self,
        event: pygame.event.Event,
        screen: pygame.Surface,
    ) -> Optional[ScreenResult]:
        button_rects = self._pause_button_rects(screen.get_rect())

        if event.type == pygame.MOUSEMOTION:
            self.pause_hovered_button = None
            for button_name, rect in button_rects.items():
                if rect.collidepoint(event.pos):
                    self.pause_hovered_button = button_name
                    self.pause_selected_index = self.PAUSE_MENU_OPTIONS.index(button_name)
                    break
            return None

        if event.type == pygame.KEYDOWN:
            if event.key in (pygame.K_UP, pygame.K_w):
                self.pause_selected_index = (self.pause_selected_index - 1) % len(self.PAUSE_MENU_OPTIONS)
                self.pause_hovered_button = None
                return None

            if event.key in (pygame.K_DOWN, pygame.K_s):
                self.pause_selected_index = (self.pause_selected_index + 1) % len(self.PAUSE_MENU_OPTIONS)
                self.pause_hovered_button = None
                return None

            if event.key in (pygame.K_RETURN, pygame.K_SPACE):
                selected = self.PAUSE_MENU_OPTIONS[self.pause_selected_index]
                return self._activate_pause_menu_option(selected)
            return None

        if event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            for button_name, rect in button_rects.items():
                if rect.collidepoint(event.pos):
                    return self._activate_pause_menu_option(button_name)

        return None

    def _activate_pause_menu_option(self, option: str) -> ScreenResult:
        if option == "resume":
            self.pause_menu_open = False
            self.pause_hovered_button = None
            self.last_message = "Game resumed."
            return ScreenResult()

        if option == "return_title":
            self.pause_menu_open = False
            self.pause_hovered_button = None
            return ScreenResult(next_screen=TitleScreen(self.atlas, self.audio_settings))

        return ScreenResult()

    def _draw_pause_menu(self, screen: pygame.Surface) -> None:
        overlay = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
        overlay.fill((8, 12, 18, 185))
        screen.blit(overlay, (0, 0))

        screen_rect = screen.get_rect()
        panel_width = min(560, screen_rect.width - 120)
        panel_height = 340
        panel_rect = pygame.Rect(0, 0, panel_width, panel_height)
        panel_rect.center = screen_rect.center

        draw_theme_panel(screen, panel_rect, alpha=230)

        title_font = theme_font(screen_rect.width, screen_rect.height, 48, bold=True)
        hint_font = theme_font(screen_rect.width, screen_rect.height, 24)
        title_text = title_font.render("PAUSE MENU", True, (245, 245, 245))
        hint_text = hint_font.render("Esc: Resume", True, (200, 210, 220))
        screen.blit(title_text, title_text.get_rect(center=(panel_rect.centerx, panel_rect.y + 62)))
        screen.blit(hint_text, hint_text.get_rect(center=(panel_rect.centerx, panel_rect.y + 106)))

        labels = {
            "resume": "RESUME",
            "return_title": "RETURN TO TITLE",
        }
        button_rects = self._pause_button_rects(screen_rect)
        for idx, option in enumerate(self.PAUSE_MENU_OPTIONS):
            rect = button_rects[option]
            is_selected = idx == self.pause_selected_index
            is_hovered = option == self.pause_hovered_button
            if option == "return_title":
                fill = SETTINGS_DANGER_FILL if (is_selected or is_hovered) else SETTINGS_IDLE_FILL
                border = SETTINGS_DANGER_BORDER if (is_selected or is_hovered) else SETTINGS_IDLE_BORDER
            else:
                fill = SETTINGS_ACTIVE_FILL if (is_selected or is_hovered) else SETTINGS_IDLE_FILL
                border = SETTINGS_ACTIVE_BORDER if (is_selected or is_hovered) else SETTINGS_IDLE_BORDER
            GameSettingsScreen._draw_button(screen, rect, labels[option], fill, border)

    def _schedule_reaction_ai(self, now_ms: int) -> None:
        if self.game.pending_effect == RULE_REACTION:
            if not self.reaction_ai_due_times:
                for pid in range(1, self.game.num_players):
                    self.reaction_ai_due_times[pid] = now_ms + self.ai_rng.randint(500, 2900)
        else:
            self.reaction_ai_due_times.clear()

    def _submit_ai_reactions(self, now_ms: int) -> None:
        for pid, due_time in list(self.reaction_ai_due_times.items()):
            if (
                now_ms >= due_time
                and pid not in self.game.pending_reaction_players
                and self.game.pending_effect == RULE_REACTION
            ):
                result = self.game.submit_action(
                    PlayerAction(player_id=pid, action_type="react", timestamp_ms=now_ms)
                )
                if result.ok:
                    self.last_message = result.message
                del self.reaction_ai_due_times[pid]

    def _handle_mouse_down(
        self,
        mouse_pos: tuple[int, int],
        screen: pygame.Surface,
        now_ms: int,
    ) -> None:
        if self.game.is_animating:
            return

        if self.game.pending_effect == RULE_ZERO_DIRECTION and self.game.current_player == 0:
            for direction, rect in get_rule_zero_choice_rects(screen.get_rect()).items():
                if rect.collidepoint(mouse_pos):
                    choice_action = PlayerAction(
                        player_id=self.game.current_player,
                        action_type="choose_zero_direction",
                        chosen_direction=direction,
                        timestamp_ms=now_ms,
                    )
                    self._begin_hand_transfer_animation(choice_action, screen, now_ms)
                    self.last_message = "Rule of 0: transferring hands."
                    break
            return

        if self.game.pending_effect == RULE_SEVEN_TARGET and self.game.current_player == 0:
            for target_player_id, rect in get_rule_seven_target_rects(
                self.game,
                screen.get_rect(),
            ).items():
                if rect.collidepoint(mouse_pos):
                    choice_action = PlayerAction(
                        player_id=self.game.current_player,
                        action_type="choose_seven_target",
                        target_player_id=target_player_id,
                        timestamp_ms=now_ms,
                    )
                    self._begin_hand_transfer_animation(choice_action, screen, now_ms)
                    self.last_message = "Rule of 7: transferring hands."
                    break
            return

        if self.game.pending_effect == RULE_REACTION:
            react_rect = get_reaction_button_rect(screen.get_rect())
            if react_rect.collidepoint(mouse_pos):
                result = self.game.submit_action(
                    PlayerAction(player_id=0, action_type="react", timestamp_ms=now_ms)
                )
                self._record_player_action_result(result, now_ms)
            return

        if self._wild_color_picker_active():
            color = get_wild_color_at_pos(mouse_pos, screen.get_rect())
            if color is not None:
                if self.pending_draw_decision_choosing_color:
                    result = self.game.play_pending_draw_decision(
                        0,
                        chosen_color=color,
                        timestamp_ms=now_ms,
                    )
                    if result.ok:
                        self.pending_draw_decision_card = None
                        self.pending_draw_decision_choosing_color = False
                        self._spawn_player_animation(0, result, "draw", screen, now_ms)
                    self._record_player_action_result(result, now_ms)
                else:
                    result = self.game.submit_action(
                        PlayerAction(
                            player_id=0,
                            action_type="play",
                            card_index=self.pending_wild_card_index,
                            chosen_color=color,
                            timestamp_ms=now_ms,
                        )
                    )
                    self.pending_wild_card_index = None
                    self._spawn_player_animation(0, result, "play", screen, now_ms)
                    self._record_player_action_result(result, now_ms)
                    self._clamp_selected_index()
            return

        if self.pending_draw_decision_card is not None:
            for button_name, rect in get_draw_decision_button_rects(screen.get_rect()).items():
                if rect.collidepoint(mouse_pos):
                    if button_name == "play":
                        self._play_pending_draw_decision(screen, now_ms)
                    elif button_name == "keep":
                        self._keep_pending_draw_decision(screen, now_ms)
                    break
            return

        if self.game.current_player != 0 or self.game.pending_effect is not None:
            return

        sort_rect = get_sort_hand_button_rect(screen.get_rect())
        if sort_rect.collidepoint(mouse_pos):
            result = self.game.submit_action(
                PlayerAction(player_id=0, action_type="sort_hand", timestamp_ms=now_ms)
            )
            self._record_player_action_result(result, now_ms)
            self._clamp_selected_index()
            return

        uno_rect = get_uno_button_rect(screen.get_rect())
        if uno_rect.collidepoint(mouse_pos):
            result = self.game.submit_action(
                PlayerAction(player_id=0, action_type="uno", timestamp_ms=now_ms)
            )
            self._record_player_action_result(result, now_ms)
            return

        hand = self.game.player_hands[0]
        effective_hidden = self._effective_hidden_ids()
        clicked_card = False
        for i in range(len(hand) - 1, -1, -1):
            if id(hand[i]) in effective_hidden:
                continue
            rect = self._hand_card_rect(hand[i])
            if rect.collidepoint(mouse_pos):
                self.selected_index = i
                card = hand[i]
                if card.is_wild:
                    self.pending_wild_card_index = i
                    self.last_message = "Choose a color for the wild card."
                else:
                    result = self.game.submit_action(
                        PlayerAction(
                            player_id=0,
                            action_type="play",
                            card_index=i,
                            timestamp_ms=now_ms,
                        )
                    )
                    self._spawn_player_animation(0, result, "play", screen, now_ms)
                    self._record_player_action_result(result, now_ms)
                    self._clamp_selected_index()
                clicked_card = True
                break

        if not clicked_card:
            draw_rect = get_draw_pile_rect(screen.get_width(), screen.get_height())
            if draw_rect.collidepoint(mouse_pos):
                self._draw_for_decision(screen, now_ms)

    def _handle_key_down(self, event: pygame.event.Event, screen: pygame.Surface, now_ms: int) -> None:
        hand = self.game.player_hands[0]

        if self._wild_color_picker_active():
            if event.key == pygame.K_ESCAPE:
                if self.pending_draw_decision_choosing_color:
                    self.pending_draw_decision_choosing_color = False
                    self.last_message = "Choose whether to play or keep the drawn card."
                else:
                    self.pending_wild_card_index = None
                    self.last_message = "Wild color selection canceled."
            return

        if self.pending_draw_decision_card is not None:
            if event.key in (pygame.K_RETURN, pygame.K_SPACE, pygame.K_p):
                self._play_pending_draw_decision(screen, now_ms)
            elif event.key in (pygame.K_k, pygame.K_ESCAPE):
                self._keep_pending_draw_decision(screen, now_ms)
            return

        if self._compact_back_virtual_size > 0:
            visible_indices = self._compact_visible_card_indices()
            if event.key == pygame.K_LEFT and visible_indices:
                pos = visible_indices.index(self.selected_index) if self.selected_index in visible_indices else 0
                self.selected_index = visible_indices[(pos - 1) % len(visible_indices)]
                return
            elif event.key == pygame.K_RIGHT and visible_indices:
                pos = visible_indices.index(self.selected_index) if self.selected_index in visible_indices else 0
                self.selected_index = visible_indices[(pos + 1) % len(visible_indices)]
                return

        if event.key == pygame.K_LEFT and hand:
            self.selected_index = (self.selected_index - 1) % len(hand)
        elif event.key == pygame.K_RIGHT and hand:
            self.selected_index = (self.selected_index + 1) % len(hand)
        elif event.key in (pygame.K_RETURN, pygame.K_SPACE) and hand and self.game.pending_effect is None:
            card = hand[self.selected_index]
            if card.is_wild:
                self.pending_wild_card_index = self.selected_index
                self.last_message = "Choose a color for the wild card."
            else:
                result = self.game.submit_action(
                    PlayerAction(
                        player_id=0,
                        action_type="play",
                        card_index=self.selected_index,
                        timestamp_ms=now_ms,
                    )
                )
                self._record_player_action_result(result, now_ms)
                self._clamp_selected_index()
        elif event.key == pygame.K_s and self.game.pending_effect is None:
            result = self.game.submit_action(
                PlayerAction(player_id=0, action_type="sort_hand", timestamp_ms=now_ms)
            )
            self._record_player_action_result(result, now_ms)
            self._clamp_selected_index()
        elif event.key == pygame.K_d and self.game.pending_effect is None:
            self._draw_for_decision(screen, now_ms)
        elif event.key == pygame.K_u and self.game.pending_effect is None:
            result = self.game.submit_action(
                PlayerAction(player_id=0, action_type="uno", timestamp_ms=now_ms)
            )
            self._record_player_action_result(result, now_ms)

    def _has_modal_input(self) -> bool:
        return self._wild_color_picker_active() or self.pending_draw_decision_card is not None

    def _wild_color_picker_active(self) -> bool:
        return self.pending_wild_card_index is not None or self.pending_draw_decision_choosing_color

    def _draw_for_decision(self, screen: pygame.Surface, now_ms: int) -> None:
        result = self.game.draw_for_decision(0)
        if result.ok and self.game.pending_draw_decision_card is result.drew_card:
            self.pending_draw_decision_card = result.drew_card
            self.pending_draw_decision_choosing_color = False
            self._record_player_action_result(result, now_ms)
            return

        self._spawn_player_animation(0, result, "draw", screen, now_ms)
        self._record_player_action_result(result, now_ms)

    def _play_pending_draw_decision(self, screen: pygame.Surface, now_ms: int) -> None:
        card = self.pending_draw_decision_card
        if card is None:
            return
        if card.is_wild:
            self.pending_draw_decision_choosing_color = True
            self.last_message = "Choose a color for the drawn wild card."
            return

        result = self.game.play_pending_draw_decision(0, timestamp_ms=now_ms)
        if result.ok:
            self.pending_draw_decision_card = None
            self.pending_draw_decision_choosing_color = False
            self._spawn_player_animation(0, result, "draw", screen, now_ms)
        self._record_player_action_result(result, now_ms)

    def _keep_pending_draw_decision(self, screen: pygame.Surface, now_ms: int) -> None:
        result = self.game.keep_pending_draw_decision(0)
        if result.ok:
            self.pending_draw_decision_card = None
            self.pending_draw_decision_choosing_color = False
            self._spawn_player_animation(0, result, "draw", screen, now_ms)
            self._clamp_selected_index()
        self._record_player_action_result(result, now_ms)

    def _record_player_action_result(self, result, now_ms: int) -> None:
        self.last_message = result.message
        if result.ok and getattr(result, "uno_call_player", None) is not None:
            self._play_uno_catch_sound()
            self._trigger_uno_flash(True)
        if result.ok and getattr(result, "uno_caught_player", None) is not None:
            self._play_uno_catch_sound()
            self._trigger_uno_flash(False)
        # Prevent same-frame AI actions from hiding turn transitions (e.g., after Reverse).
        if result.ok and self.game.winner is None and self.game.current_player != 0:
            self.next_ai_time = max(self.next_ai_time, now_ms + self.AI_TURN_DELAY_MS)

    def _load_uno_catch_sound(self) -> pygame.mixer.Sound | None:
        if not pygame.mixer.get_init():
            return None
        sound_path = asset_path("sfx", "woww.mp3")
        if not sound_path.exists():
            return None
        try:
            sound = pygame.mixer.Sound(str(sound_path))
            sound.set_volume(self.audio_settings.sfx_mix(0.75))
            return sound
        except pygame.error:
            return None

    def _play_uno_catch_sound(self) -> None:
        if self.uno_catch_sound is not None:
            self.uno_catch_sound.set_volume(self.audio_settings.sfx_mix(0.75))
            self.uno_catch_sound.play()

    def _load_card_sound(self, filename: str) -> pygame.mixer.Sound | None:
        """Load a card sound effect from the sfx directory."""
        if not pygame.mixer.get_init():
            return None
        sound_path = asset_path("sfx", filename)
        if not sound_path.exists():
            return None
        try:
            sound = pygame.mixer.Sound(str(sound_path))
            sound.set_volume(self.audio_settings.sfx_mix(0.65))
            return sound
        except pygame.error:
            return None

    def _play_card_sound(self, sound: pygame.mixer.Sound | None) -> None:
        """Play a card sound effect."""
        if sound is not None:
            sound.set_volume(self.audio_settings.sfx_mix(0.65))
            sound.play()

    def _build_ai_hand_transfer_action(self) -> PlayerAction | None:
        if self.game.pending_effect == RULE_ZERO_DIRECTION:
            return PlayerAction(
                player_id=self.game.current_player,
                action_type="choose_zero_direction",
                chosen_direction=self.ai_rng.choice([1, -1]),
                timestamp_ms=self._last_update_ms,
            )

        if self.game.pending_effect == RULE_SEVEN_TARGET:
            targets = [pid for pid in range(self.game.num_players) if pid != self.game.pending_effect_player]
            if not targets:
                return None
            return PlayerAction(
                player_id=self.game.current_player,
                action_type="choose_seven_target",
                target_player_id=self.ai_rng.choice(targets),
                timestamp_ms=self._last_update_ms,
            )

        return None

    def _begin_hand_transfer_animation(self, choice_action: PlayerAction, screen: pygame.Surface, now_ms: int) -> None:
        if self.hand_transfer_animation is not None:
            return

        screen_rect = screen.get_rect()
        center = get_discard_pile_rect(screen_rect).center
        center_point = (float(center[0]), float(center[1]))

        if choice_action.action_type == "choose_zero_direction":
            assert choice_action.chosen_direction in (PASS_CLOCKWISE, PASS_COUNTER_CLOCKWISE)
            affected_players = list(range(self.game.num_players))
        elif choice_action.action_type == "choose_seven_target":
            if choice_action.target_player_id is None:
                return
            affected_players = [self.game.pending_effect_player, choice_action.target_player_id]
        else:
            return

        cards: list[ActiveCard] = []
        target_owner_by_card_id: dict[int, int] = {}

        for source_player in affected_players:
            if source_player is None:
                continue
            source_hand = list(self.game.player_hands[source_player])
            source_rects = get_player_hand_card_rects(
                screen_rect,
                source_player,
                self.game.num_players,
                len(source_hand),
                source_hand,
                use_current_positions=(source_player == 0),
            )

            for index, card in enumerate(source_hand):
                if choice_action.action_type == "choose_zero_direction":
                    target_owner = (source_player + choice_action.chosen_direction) % self.game.num_players
                else:
                    target_owner = choice_action.target_player_id if source_player == self.game.pending_effect_player else self.game.pending_effect_player

                target_owner_by_card_id[id(card)] = target_owner
                self.hidden_hand_card_ids.add(id(card))

                source_center = source_rects[index].center
                source_rotation = get_player_card_rotation(source_player, self.game.num_players)

                cards.append(
                    ActiveCard(
                        card=card,
                        owner_id=source_player,
                        kind="transfer",
                        current_pos=(float(source_center[0]), float(source_center[1])),
                        target_pos=center_point,
                        current_rotation=source_rotation,
                        target_rotation=source_rotation,
                        current_scale=1.0,
                        target_scale=0.88,
                    )
                )

        self.hand_transfer_animation = HandTransferAnimation(
            choice_action=choice_action,
            phase=1,
            cards=cards,
            target_owner_by_card_id=target_owner_by_card_id,
        )
        self.visual_state = HAND_TRANSFER_ANIMATION
        self.game.is_animating = True

    def _update_hand_transfer_animation(self, screen: pygame.Surface, dt: float, now_ms: int) -> None:
        animation = self.hand_transfer_animation
        if animation is None:
            return

        if animation.phase == 1:
            all_finished = True
            for active_card in animation.cards:
                finished = active_card.update(dt)
                active_card.card.current_pos = active_card.current_pos
                active_card.card.current_rotation = active_card.current_rotation
                active_card.card.current_scale = active_card.current_scale
                if not finished:
                    all_finished = False

            if not all_finished:
                return

            resolution_result = self.game.submit_action(animation.choice_action)
            self.last_message = resolution_result.message
            if not resolution_result.ok:
                for active_card in animation.cards:
                    self.hidden_hand_card_ids.discard(id(active_card.card))
                self.hand_transfer_animation = None
                self.visual_state = ""
                return

            screen_rect = screen.get_rect()
            center = get_discard_pile_rect(screen_rect).center
            center_point = (float(center[0]), float(center[1]))

            for active_card in animation.cards:
                new_owner = animation.target_owner_by_card_id[id(active_card.card)]
                new_hand = self.game.player_hands[new_owner]
                hand_rects = get_player_hand_card_rects(
                    screen_rect,
                    new_owner,
                    self.game.num_players,
                    len(new_hand),
                    new_hand,
                    use_current_positions=False,
                )
                new_index = new_hand.index(active_card.card)
                target_rect = hand_rects[new_index]

                active_card.owner_id = new_owner
                active_card.current_pos = center_point
                active_card.target_pos = (float(target_rect.centerx), float(target_rect.centery))
                active_card.current_rotation = 0.0
                active_card.target_rotation = get_player_card_rotation(new_owner, self.game.num_players)
                active_card.current_scale = 0.88
                active_card.target_scale = 1.0

            animation.phase = 2
            self.next_ai_time = max(self.next_ai_time, now_ms + self.AI_TURN_DELAY_MS)
            return

        all_finished = True
        for active_card in animation.cards:
            finished = active_card.update(dt)
            active_card.card.current_pos = active_card.current_pos
            active_card.card.current_rotation = active_card.current_rotation
            active_card.card.current_scale = active_card.current_scale
            if not finished:
                all_finished = False

        if not all_finished:
            return

        for active_card in animation.cards:
            self.hidden_hand_card_ids.discard(id(active_card.card))

        self.hand_transfer_animation = None
        self.visual_state = ""
        self._hand_layout_initialized = False
        self.next_ai_time = max(self.next_ai_time, now_ms + self.AI_TURN_DELAY_MS)
        self.game.is_animating = False

    def _update_direction_arrow(self, dt: float) -> None:
        target_speed = self.DIRECTION_ARROW_BASE_SPEED * self.game.turn_direction

        if self.direction_arrow_speed * target_speed < 0 and abs(self.direction_arrow_speed) > 8.0:
            self.direction_arrow_speed = lerp(self.direction_arrow_speed, 0.0, smooth_factor(dt, self.DIRECTION_ARROW_DECEL))
        else:
            self.direction_arrow_speed = lerp(
                self.direction_arrow_speed,
                target_speed,
                smooth_factor(dt, self.DIRECTION_ARROW_ACCEL),
            )

        self.direction_arrow_angle = (self.direction_arrow_angle + self.direction_arrow_speed * dt) % 360.0

    def _spawn_player_animation(
        self,
        player_id: int,
        result,
        action_kind: str,
        screen: pygame.Surface,
        now_ms: int,
    ) -> None:
        if not result.ok:
            return

        screen_rect = screen.get_rect()

        if action_kind == "play":
            card = result.played_card
            if card is not None:
                # Play card-specific sounds for extension pack cards
                if card.kind == ACTION_COUNTER:
                    self._play_card_sound(self.counter_card_sound)
                elif card.kind == ACTION_DRAW_67:
                    self._play_card_sound(self.draw67_card_sound)
                elif card.kind == ACTION_SILENCE:
                    self._play_card_sound(self.silence_card_sound)
                
                self._spawn_active_card(
                    card=card,
                    owner_id=player_id,
                    kind="play",
                    start_pos=get_player_anchor_point(screen_rect, player_id, self.game.num_players),
                    target_pos=get_discard_pile_rect(screen_rect).center,
                    start_rotation=get_player_card_rotation(player_id, self.game.num_players),
                    target_rotation=get_player_card_rotation(player_id, self.game.num_players)
                    + self.ai_rng.uniform(-15.0, 15.0),
                )
            self._spawn_uno_penalty_animation(result, screen)
            return

        if action_kind == "draw":
            if result.played_card is not None and result.drew_card is not None:
                # The drawn card was auto-played; animate it from the draw pile to the discard pile.
                self._spawn_active_card(
                    card=result.played_card,
                    owner_id=player_id,
                    kind="play",
                    start_pos=get_draw_pile_rect(screen.get_width(), screen.get_height()).center,
                    target_pos=get_discard_pile_rect(screen_rect).center,
                    start_rotation=0.0,
                    target_rotation=self.ai_rng.uniform(-15.0, 15.0),
                )
                self._spawn_uno_penalty_animation(result, screen)
                return

            card = result.drew_card
            if card is not None:
                self.hidden_hand_card_ids.add(id(card))
                self._spawn_active_card(
                    card=card,
                    owner_id=player_id,
                    kind="draw",
                    start_pos=get_draw_pile_rect(screen.get_width(), screen.get_height()).center,
                    target_pos=get_player_anchor_point(screen_rect, player_id, self.game.num_players),
                    start_rotation=0.0,
                    target_rotation=get_player_card_rotation(player_id, self.game.num_players),
                    reveal_hand_card=True,
                )
            self._spawn_uno_penalty_animation(result, screen)

    def _spawn_ai_animation(self, player_id: int, outcome: AITurnOutcome, screen: pygame.Surface, now_ms: int) -> None:
        if outcome.action_type == "play" and outcome.card is not None:
            self._spawn_active_card(
                card=outcome.card,
                owner_id=player_id,
                kind="play",
                start_pos=get_player_anchor_point(screen.get_rect(), player_id, self.game.num_players),
                target_pos=get_discard_pile_rect(screen.get_rect()).center,
                start_rotation=get_player_card_rotation(player_id, self.game.num_players),
                target_rotation=get_player_card_rotation(player_id, self.game.num_players)
                + self.ai_rng.uniform(-15.0, 15.0),
            )
            if outcome.result is not None:
                self._spawn_uno_penalty_animation(outcome.result, screen)
            return

        if outcome.action_type == "draw" and outcome.card is not None:
            self._spawn_active_card(
                card=outcome.card,
                owner_id=player_id,
                kind="draw",
                start_pos=get_draw_pile_rect(screen.get_width(), screen.get_height()).center,
                target_pos=get_player_anchor_point(screen.get_rect(), player_id, self.game.num_players),
                start_rotation=0.0,
                target_rotation=get_player_card_rotation(player_id, self.game.num_players),
            )
            if outcome.result is not None:
                self._spawn_uno_penalty_animation(outcome.result, screen)
            return

        if outcome.action_type == "draw_played" and outcome.card is not None:
            self._spawn_active_card(
                card=outcome.card,
                owner_id=player_id,
                kind="play",
                start_pos=get_draw_pile_rect(screen.get_width(), screen.get_height()).center,
                target_pos=get_discard_pile_rect(screen.get_rect()).center,
                start_rotation=0.0,
                target_rotation=self.ai_rng.uniform(-15.0, 15.0),
            )
            if outcome.result is not None:
                self._spawn_uno_penalty_animation(outcome.result, screen)

    def _spawn_uno_penalty_animation(self, result, screen: pygame.Surface) -> None:
        player_id = getattr(result, "uno_caught_player", None)
        penalty_cards = getattr(result, "uno_penalty_cards", None) or []
        if player_id is None or not penalty_cards:
            return

        screen_rect = screen.get_rect()
        draw_center = get_draw_pile_rect(screen.get_width(), screen.get_height()).center
        target_center = get_player_anchor_point(screen_rect, player_id, self.game.num_players)

        for offset, card in enumerate(penalty_cards):
            if player_id == 0:
                self.hidden_hand_card_ids.add(id(card))
            stagger = float((offset - 0.5) * 14)
            self._spawn_active_card(
                card=card,
                owner_id=player_id,
                kind="draw",
                start_pos=(float(draw_center[0] + stagger), float(draw_center[1] - stagger)),
                target_pos=(float(target_center[0] + stagger), float(target_center[1])),
                start_rotation=0.0,
                target_rotation=get_player_card_rotation(player_id, self.game.num_players),
                reveal_hand_card=(player_id == 0),
            )

    def _spawn_active_card(
        self,
        card,
        owner_id: int,
        kind: str,
        start_pos: tuple[float, float],
        target_pos: tuple[float, float],
        start_rotation: float,
        target_rotation: float,
        reveal_hand_card: bool = False,
    ) -> None:
        card.current_pos = start_pos
        card.target_pos = target_pos
        card.current_rotation = start_rotation
        card.target_rotation = target_rotation
        card.current_scale = 1.0
        card.target_scale = 1.0
        duration = 0.24 if kind == "play" else 0.32
        if kind == "draw" and owner_id != 0:
            duration = 0.28
        self.active_cards.append(
            ActiveCard(
                card=card,
                owner_id=owner_id,
                kind=kind,
                current_pos=start_pos,
                target_pos=target_pos,
                current_rotation=start_rotation,
                target_rotation=target_rotation,
                current_scale=1.0,
                target_scale=1.0,
                reveal_hand_card=reveal_hand_card,
                duration=duration,
            )
        )

    def _update_active_cards(self, dt: float) -> None:
        still_active: list[ActiveCard] = []
        for active_card in self.active_cards:
            finished = active_card.update(dt)
            active_card.card.current_pos = active_card.current_pos
            active_card.card.target_pos = active_card.target_pos
            active_card.card.current_rotation = active_card.current_rotation
            active_card.card.target_rotation = active_card.target_rotation
            active_card.card.current_scale = active_card.current_scale
            active_card.card.target_scale = active_card.target_scale

            if finished:
                active_card.card.current_pos = active_card.target_pos
                active_card.card.current_rotation = active_card.target_rotation
                active_card.card.current_scale = active_card.target_scale
                if active_card.kind == "play":
                    self.display_top_card = active_card.card
                    if active_card.card.kind == ACTION_WILD_DRAW_FOUR:
                        self._trigger_screen_shake()
                if active_card.reveal_hand_card:
                    self.hidden_hand_card_ids.discard(id(active_card.card))
                continue

            still_active.append(active_card)

        self.active_cards = still_active
        
        if not self.active_cards and self.game.is_animating:
            self.game.is_animating = False

    def _get_shadow_surface(self, width: int, height: int, alpha: int) -> pygame.Surface:
        key = (width, height, alpha)
        shadow = self._shadow_cache.get(key)
        if shadow is not None:
            return shadow
        shadow = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.ellipse(shadow, (0, 0, 0, alpha), shadow.get_rect())
        self._shadow_cache[key] = shadow
        return shadow

    def _draw_active_cards(self, screen: pygame.Surface) -> None:
        shadow = self._get_shadow_surface(66, 20, 95)
        for active_card in self.active_cards:
            card_center = (int(active_card.current_pos[0]), int(active_card.current_pos[1]))
            shadow_rect = shadow.get_rect(center=(card_center[0], card_center[1] + 10))
            screen.blit(shadow, shadow_rect)

            if active_card.kind == "draw" and active_card.owner_id == 0:
                showing_front = active_card.progress >= 0.5
                base_surface = (
                    self.atlas.get_card_surface(active_card.card, 88, 130)
                    if showing_front
                    else self.atlas.get_back_surface(88, 130)
                )
                flip_x = max(0.08, abs(0.5 - active_card.progress) * 2.0)
                flip_width = max(8, int(88 * flip_x))
                card_img = pygame.transform.smoothscale(base_surface, (flip_width, 130))
            elif active_card.kind == "draw" and active_card.owner_id != 0:
                card_img = self.atlas.get_back_surface(88, 130)
            else:
                card_img = self.atlas.get_card_surface(active_card.card, 88, 130)
            card_img = transform_card_surface(card_img, active_card.current_rotation, active_card.current_scale)
            rect = card_img.get_rect(center=card_center)
            screen.blit(card_img, rect)

    def _draw_hand_transfer_cards(self, screen: pygame.Surface) -> None:
        if self.hand_transfer_animation is None:
            return

        shadow = self._get_shadow_surface(66, 20, 90)
        for active_card in self.hand_transfer_animation.cards:
            shadow_rect = shadow.get_rect(
                center=(int(active_card.current_pos[0]), int(active_card.current_pos[1] + 10))
            )
            screen.blit(shadow, shadow_rect)
            card_img = self.atlas.get_back_surface(88, 130)
            card_img = transform_card_surface(card_img, active_card.current_rotation, active_card.current_scale)
            rect = card_img.get_rect(center=(int(active_card.current_pos[0]), int(active_card.current_pos[1])))
            screen.blit(card_img, rect)

    def _update_player_hand_animation(self, screen: pygame.Surface, dt: float) -> None:
        hand = self.game.player_hands[0]
        if not hand:
            self._hand_layout_initialized = True
            self._compact_back_virtual_size = 0
            self._compact_hidden_ids = set()
            return

        speed = 14.0
        width = screen.get_width()
        height = screen.get_height()

        # When hand is large, compact it: sort first, then show only 10 visible cards
        compact = len(hand) >= 15
        if compact:
            sort_hand_cards(hand)
            visible_indices = self._compact_visible_card_indices()
            visible_count = len(visible_indices)
            virtual_size = visible_count + 1 if visible_count else 1
            self._compact_back_virtual_size = virtual_size
            self._compact_hidden_ids = set()
            back_target_rect = card_rect_for_hand(0, virtual_size, width, height)
            if visible_indices:
                if self.selected_index not in visible_indices:
                    self.selected_index = visible_indices[0]
            else:
                self.selected_index = 0
        else:
            self._compact_back_virtual_size = 0
            self._compact_hidden_ids = set()
            visible_indices = []

        for i, card in enumerate(hand):
            if id(card) in self.hidden_hand_card_ids:
                continue

            if compact and i not in visible_indices:
                self._compact_hidden_ids.add(id(card))
                card.target_pos = (float(back_target_rect.x), float(back_target_rect.y))
                card.target_rotation = 0.0
                card.target_scale = 1.0
                if not self._hand_layout_initialized:
                    card.current_pos = card.target_pos
                    card.current_rotation = 0.0
                else:
                    factor = smooth_factor(dt, speed)
                    card.current_pos = lerp_point(card.current_pos, card.target_pos, factor)
                    card.current_rotation = lerp(card.current_rotation, 0.0, smooth_factor(dt, speed * 0.75))
                card.current_scale = card.current_scale + (1.0 - card.current_scale) * smooth_factor(dt, 12.0)
                continue

            # For visible cards, calculate target position
            if compact:
                rank = visible_indices.index(i)
                target_rect = card_rect_for_hand(rank + 1, virtual_size, width, height, hovered=(i == self.hovered_index))
                target_rotation = get_player_hand_rotation(rank + 1, virtual_size)
            else:
                target_rect = card_rect_for_hand(i, len(hand), width, height, hovered=(i == self.hovered_index))
                target_rotation = get_player_hand_rotation(i, len(hand))

            card.target_pos = (float(target_rect.x), float(target_rect.y))
            if i == self.hovered_index:
                target_rotation *= 0.78
            card.target_rotation = target_rotation

            if not self._hand_layout_initialized:
                card.current_pos = card.target_pos
                card.current_rotation = target_rotation
            else:
                factor = smooth_factor(dt, speed)
                card.current_pos = lerp_point(card.current_pos, card.target_pos, factor)
                card.current_rotation = lerp(card.current_rotation, card.target_rotation, smooth_factor(dt, speed * 0.75))

            if i == self.hovered_index:
                card.target_scale = 1.04
            else:
                card.target_scale = 1.0

            card.current_scale = card.current_scale + (card.target_scale - card.current_scale) * smooth_factor(dt, 12.0)

        self._hand_layout_initialized = True

    def _effective_hidden_ids(self) -> set[int]:
        return self.hidden_hand_card_ids | self._compact_hidden_ids

    def _compact_visible_card_indices(self) -> list[int]:
        hand = self.game.player_hands[0]
        if len(hand) < 15:
            return []
        legal_indices = self.game.get_legal_card_indices(0)
        return legal_indices[:10]

    def _clamp_selected_index(self) -> None:
        hand = self.game.player_hands[0]
        if self._compact_back_virtual_size > 0:
            visible_indices = self._compact_visible_card_indices()
            if visible_indices:
                if self.selected_index not in visible_indices:
                    self.selected_index = visible_indices[0]
                return
            self.selected_index = 0
        elif hand:
            self.selected_index = min(self.selected_index, len(hand) - 1)
        else:
            self.selected_index = 0

    def _hand_card_rect(self, card) -> pygame.Rect:
        return get_card_rect_from_pos(card)


class _NetworkGameProxy:
    def __init__(self, base_game: UnoGameManager, submit_callback) -> None:
        self._base_game = base_game
        self._submit_callback = submit_callback

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base_game, name)

    def submit_action(self, action: PlayerAction):
        return self._submit_callback(
            {
                "action_type": action.action_type,
                "card_index": action.card_index,
                "chosen_color": action.chosen_color,
                "chosen_direction": action.chosen_direction,
                "target_player_id": action.target_player_id,
                "timestamp_ms": action.timestamp_ms,
            }
        )

    def draw_for_decision(self, player_id: int):
        return self._submit_callback({"action_type": "draw_for_decision"})

    def play_pending_draw_decision(
        self,
        player_id: int,
        chosen_color: Optional[str] = None,
        timestamp_ms: Optional[int] = None,
    ):
        return self._submit_callback(
            {
                "action_type": "play_drawn",
                "chosen_color": chosen_color,
                "timestamp_ms": timestamp_ms,
            }
        )

    def keep_pending_draw_decision(self, player_id: int):
        return self._submit_callback({"action_type": "keep_drawn"})


class MultiplayerPlayingScreen(PlayingScreen):
    def __init__(
        self,
        atlas: CardSpriteAtlas,
        game: UnoGameManager,
        audio_settings: AudioSettings,
        is_host: bool,
        host: Optional[MultiplayerHost],
        client: Optional[MultiplayerClient],
        local_canonical_player_id: int,
        room_state: dict[str, Any],
        seat_names: dict[int, str],
        initial_seq: int = 0,
        last_message: str = "",
        next_ai_time: int = 0,
    ) -> None:
        self._base_game = game
        self.is_host_player = is_host
        self.host = host
        self.client = client
        self.local_canonical_player_id = local_canonical_player_id
        self.room_state = room_state
        self.seat_names = seat_names
        self._last_sync_seq = initial_seq
        proxy = _NetworkGameProxy(game, self._submit_network_action)
        super().__init__(
            atlas=atlas,
            game=proxy,  # type: ignore[arg-type]
            audio_settings=audio_settings,
            last_message=last_message or "Multiplayer match synchronized.",
            next_ai_time=next_ai_time,
        )
        self._pending_network_message = ""
        self._action_in_flight = False
        self._awaiting_hand_transfer_snapshot = False
        self._queued_hand_transfer_payload: dict[str, Any] | None = None
        self._hand_transfer_pre_signature: tuple[tuple[tuple[Optional[str], str, Optional[int], Optional[str]], ...], ...] | None = None

    def _handoff_to_room_screen(self, message: str) -> BaseScreen:
        next_screen = MultiplayerScreen(self.atlas, self.audio_settings)
        next_screen.mode = MultiplayerScreen.MODE_ROOM
        next_screen.is_host = self.is_host_player
        next_screen.host = self.host
        next_screen.client = self.client
        if self.is_host_player and self.host is not None:
            next_screen.room_state = self.host.room_state
        else:
            next_screen.room_state = self.room_state
        next_screen.message = message
        self.host = None
        self.client = None
        return next_screen

    def _player_name_map(self) -> dict[int, str] | None:
        return self.seat_names

    def _local_player_id(self) -> int:
        return 0

    def _submit_network_action(self, action_payload: dict[str, Any]) -> ActionResult:
        if self._action_in_flight:
            return ActionResult(False, "Waiting for host synchronization...")
        mapped = dict(action_payload)
        target = mapped.get("target_player_id")
        if target is not None:
            mapped["target_player_id"] = _view_to_canonical_player(
                int(target),
                self.local_canonical_player_id,
                self._base_game.num_players,
            )
        now_ms = int(time.time() * 1000)
        if self.is_host_player and self.host is not None:
            result = self.host.apply_host_action(mapped, now_ms=now_ms)
            if not result.ok:
                self._pending_network_message = result.message
                return ActionResult(False, result.message)
            self._action_in_flight = True
            return ActionResult(False, "Waiting for host synchronization...")
        if self.client is not None:
            self.client.send({"type": "submit_action", "action": mapped, "now_ms": now_ms})
            self._action_in_flight = True
            return ActionResult(False, "Action sent. Waiting for host...")
        return ActionResult(False, "Multiplayer link not available.")

    def _hand_signature_from_game(
        self,
        game: UnoGameManager,
    ) -> tuple[tuple[tuple[Optional[str], str, Optional[int], Optional[str]], ...], ...]:
        return tuple(
            tuple(sorted((_card_signature(card) for card in hand), key=_card_signature_sort_key))
            for hand in game.player_hands
        )

    def _hand_signature_from_payload(
        self,
        payload: dict[str, Any],
    ) -> tuple[tuple[tuple[Optional[str], str, Optional[int], Optional[str]], ...], ...]:
        hands_payload = payload.get("player_hands", [])
        if not isinstance(hands_payload, list):
            return tuple()
        result: list[tuple[tuple[Optional[str], str, Optional[int], Optional[str]], ...]] = []
        for hand_payload in hands_payload:
            if not isinstance(hand_payload, list):
                result.append(tuple())
                continue
            hand_signature: list[tuple[Optional[str], str, Optional[int], Optional[str]]] = []
            for card_payload in hand_payload:
                if not isinstance(card_payload, dict):
                    continue
                hand_signature.append(
                    (
                        card_payload.get("color"),
                        str(card_payload.get("kind", "")),
                        card_payload.get("number"),
                        card_payload.get("chosen_color"),
                    )
                )
            result.append(tuple(sorted(hand_signature, key=_card_signature_sort_key)))
        return tuple(result)

    def _apply_network_snapshot(
        self,
        remapped_payload: dict[str, Any],
        previous_game: UnoGameManager,
    ) -> None:
        self._base_game = deserialize_game_state(remapped_payload)
        self._preserve_local_hand_visual_state(previous_game)
        self.game = _NetworkGameProxy(self._base_game, self._submit_network_action)  # type: ignore[assignment]
        self.display_top_card = self.game.top_discard
        self._prune_hidden_hand_cards()
        pending_draw_card = self.game.pending_draw_decision_card
        if self.game.pending_draw_decision_player == 0 and pending_draw_card is not None:
            self.pending_draw_decision_card = pending_draw_card
        else:
            self.pending_draw_decision_card = None
            self.pending_draw_decision_choosing_color = False

    def _begin_remote_hand_transfer_phase1(self, event: dict[str, Any], previous_game: UnoGameManager) -> None:
        action = str(event.get("action", "")).strip().lower()
        actor_raw = event.get("actor_id")
        try:
            actor_canonical = int(actor_raw)
        except (TypeError, ValueError):
            return
        actor_id = _canonical_to_view_player(
            actor_canonical,
            self.local_canonical_player_id,
            previous_game.num_players,
        )

        if action == "rule_0":
            direction = int(event.get("chosen_direction", PASS_CLOCKWISE))
            if direction not in (PASS_CLOCKWISE, PASS_COUNTER_CLOCKWISE):
                return
            affected_players = list(range(previous_game.num_players))
            choice_action = PlayerAction(
                player_id=actor_id,
                action_type="choose_zero_direction",
                chosen_direction=direction,
            )
        elif action == "rule_7":
            target_raw = event.get("target_player_id")
            try:
                target_canonical = int(target_raw)
            except (TypeError, ValueError):
                return
            target_id = _canonical_to_view_player(
                target_canonical,
                self.local_canonical_player_id,
                previous_game.num_players,
            )
            if target_id == actor_id:
                return
            affected_players = [actor_id, target_id]
            choice_action = PlayerAction(
                player_id=actor_id,
                action_type="choose_seven_target",
                target_player_id=target_id,
            )
        else:
            return

        if self.hand_transfer_animation is not None:
            return

        surface = pygame.display.get_surface()
        screen_rect = surface.get_rect() if surface is not None else pygame.Rect(0, 0, 1920, 1080)
        center = get_discard_pile_rect(screen_rect).center
        center_point = (float(center[0]), float(center[1]))
        cards: list[ActiveCard] = []
        target_owner_by_card_id: dict[int, int] = {}

        for source_player in affected_players:
            source_hand = list(previous_game.player_hands[source_player])
            source_rects = get_player_hand_card_rects(
                screen_rect,
                source_player,
                previous_game.num_players,
                len(source_hand),
                source_hand,
                use_current_positions=(source_player == 0),
            )
            for index, card in enumerate(source_hand):
                if action == "rule_0":
                    assert choice_action.chosen_direction is not None
                    target_owner = (source_player + choice_action.chosen_direction) % previous_game.num_players
                else:
                    assert choice_action.target_player_id is not None
                    target_owner = (
                        choice_action.target_player_id
                        if source_player == actor_id
                        else actor_id
                    )
                target_owner_by_card_id[id(card)] = target_owner
                self.hidden_hand_card_ids.add(id(card))

                source_center = source_rects[index].center
                source_rotation = get_player_card_rotation(source_player, previous_game.num_players)
                cards.append(
                    ActiveCard(
                        card=card,
                        owner_id=source_player,
                        kind="transfer",
                        current_pos=(float(source_center[0]), float(source_center[1])),
                        target_pos=center_point,
                        current_rotation=source_rotation,
                        target_rotation=source_rotation,
                        current_scale=1.0,
                        target_scale=0.88,
                    )
                )

        if not cards:
            return

        self.hand_transfer_animation = HandTransferAnimation(
            choice_action=choice_action,
            phase=1,
            cards=cards,
            target_owner_by_card_id=target_owner_by_card_id,
        )
        self.visual_state = HAND_TRANSFER_ANIMATION
        self.game.is_animating = True
        self._trigger_screen_shake()

    def _sync_from_packet(self, packet: dict[str, Any]) -> None:
        seq = int(packet.get("seq", 0) or 0)
        if seq <= self._last_sync_seq:
            return
        game_payload = packet.get("game")
        if not isinstance(game_payload, dict):
            return
        room_payload = packet.get("room")
        if isinstance(room_payload, dict):
            self.room_state = room_payload

        previous_game = self._base_game
        remapped_payload = _remap_game_payload_to_local_view(game_payload, self.local_canonical_player_id)
        event = packet.get("event")
        action = ""
        if isinstance(event, dict):
            action = str(event.get("action", "")).strip().lower()

        if self._awaiting_hand_transfer_snapshot:
            pending_effect = remapped_payload.get("pending_effect")
            if (
                self._hand_transfer_pre_signature is not None
                and self._hand_signature_from_payload(remapped_payload) != self._hand_transfer_pre_signature
            ):
                self._queued_hand_transfer_payload = remapped_payload
            elif pending_effect not in (RULE_ZERO_DIRECTION, RULE_SEVEN_TARGET):
                # Fallback: accept resolved state even if card multisets are unchanged after a swap.
                self._queued_hand_transfer_payload = remapped_payload
            self._last_sync_seq = seq
            self._action_in_flight = False
            if isinstance(event, dict):
                message = str(event.get("message", "")).strip()
                if message:
                    self.last_message = message
            return

        if action in {"rule_0", "rule_7"}:
            self._begin_remote_hand_transfer_phase1(event, previous_game)
            if self.hand_transfer_animation is not None:
                self._awaiting_hand_transfer_snapshot = True
                self._hand_transfer_pre_signature = self._hand_signature_from_game(previous_game)
                payload_signature = self._hand_signature_from_payload(remapped_payload)
                if (
                    self._hand_transfer_pre_signature is not None
                    and payload_signature != self._hand_transfer_pre_signature
                ):
                    # Many host flows already include the post-swap state in the same rule event packet.
                    self._queued_hand_transfer_payload = remapped_payload
                else:
                    self._queued_hand_transfer_payload = None
                self._last_sync_seq = seq
                self._action_in_flight = False
                message = str(event.get("message", "")).strip()
                if message:
                    self.last_message = message
                return

        self._apply_network_snapshot(remapped_payload, previous_game)
        self._last_sync_seq = seq
        self._action_in_flight = False
        if isinstance(event, dict):
            self._spawn_remote_event_animation(event, previous_game)
            message = str(event.get("message", "")).strip()
            if message:
                self.last_message = message

    def _preserve_local_hand_visual_state(self, previous_game: UnoGameManager) -> None:
        if not previous_game.player_hands or not self._base_game.player_hands:
            return
        previous_hand = list(previous_game.player_hands[0])
        current_hand = self._base_game.player_hands[0]
        if not previous_hand or not current_hand:
            return

        previous_by_signature: dict[tuple[Optional[str], str, Optional[int], Optional[str]], list[Card]] = {}
        for card in previous_hand:
            previous_by_signature.setdefault(_card_signature(card), []).append(card)

        for card in current_hand:
            signature = _card_signature(card)
            matches = previous_by_signature.get(signature)
            if not matches:
                continue
            previous_card = matches.pop(0)
            card.current_pos = previous_card.current_pos
            card.target_pos = previous_card.target_pos
            card.current_rotation = previous_card.current_rotation
            card.target_rotation = previous_card.target_rotation
            card.current_scale = previous_card.current_scale
            card.target_scale = previous_card.target_scale

    def _prune_hidden_hand_cards(self) -> None:
        known_card_ids = {id(card) for hand in self._base_game.player_hands for card in hand}
        self.hidden_hand_card_ids.intersection_update(known_card_ids)

    def _find_removed_local_card(self, previous_game: UnoGameManager, reference_card: Card) -> Optional[Card]:
        if not previous_game.player_hands or not self._base_game.player_hands:
            return None
        signature = _card_signature(reference_card)
        previous_matches = [card for card in previous_game.player_hands[0] if _card_signature(card) == signature]
        if not previous_matches:
            return None
        current_count = sum(1 for card in self._base_game.player_hands[0] if _card_signature(card) == signature)
        removed_count = len(previous_matches) - current_count
        if removed_count <= 0:
            return None
        return previous_matches[-1]

    def _find_added_local_card(self, previous_game: UnoGameManager, reference_card: Card) -> Optional[Card]:
        if not previous_game.player_hands or not self._base_game.player_hands:
            return None
        signature = _card_signature(reference_card)
        previous_count = sum(1 for card in previous_game.player_hands[0] if _card_signature(card) == signature)
        current_cards = [card for card in self._base_game.player_hands[0] if _card_signature(card) == signature]
        if len(current_cards) <= previous_count:
            return None
        return current_cards[-1]

    def _spawn_remote_event_animation(self, event: dict[str, Any], previous_game: UnoGameManager) -> None:
        actor_raw = event.get("actor_id")
        try:
            actor_canonical = int(actor_raw)
        except (TypeError, ValueError):
            return
        actor_id = _canonical_to_view_player(
            actor_canonical,
            self.local_canonical_player_id,
            self._base_game.num_players,
        )
        action = str(event.get("action", "")).strip().lower()
        if event.get("uno_caught_player") is not None:
            self._play_uno_catch_sound()
            self._trigger_uno_flash(False)
        elif action == "uno" and event.get("ok", True):
            self._play_uno_catch_sound()
            self._trigger_uno_flash(True)

        played_payload = event.get("played_card")
        drew_payload = event.get("drew_card")
        played_card = None
        drew_card = None
        if isinstance(played_payload, dict):
            played_card = Card(
                color=played_payload.get("color"),
                kind=str(played_payload.get("kind", "")),
                number=played_payload.get("number"),
            )
            chosen = played_payload.get("chosen_color")
            played_card.chosen_color = str(chosen) if chosen is not None else None
        if isinstance(drew_payload, dict):
            drew_card = Card(
                color=drew_payload.get("color"),
                kind=str(drew_payload.get("kind", "")),
                number=drew_payload.get("number"),
            )
            chosen = drew_payload.get("chosen_color")
            drew_card.chosen_color = str(chosen) if chosen is not None else None

        screen_rect = pygame.display.get_surface().get_rect() if pygame.display.get_surface() else pygame.Rect(0, 0, 1920, 1080)
        if played_card is not None and action in {"play", "draw_play"}:
            start_pos = get_player_anchor_point(screen_rect, actor_id, self._base_game.num_players)
            source_card = played_card
            start_rotation = get_player_card_rotation(actor_id, self._base_game.num_players)
            if actor_id == 0 and action != "draw_play":
                removed_local_card = self._find_removed_local_card(previous_game, played_card)
                if removed_local_card is not None:
                    source_card = removed_local_card
                    start_pos = (
                        float(removed_local_card.current_pos[0] + 44.0),
                        float(removed_local_card.current_pos[1] + 65.0),
                    )
                    start_rotation = removed_local_card.current_rotation
            if action == "draw_play":
                start_pos = get_draw_pile_rect(screen_rect.width, screen_rect.height).center
                start_rotation = 0.0
            self.game.is_animating = True
            self._spawn_active_card(
                card=source_card,
                owner_id=actor_id,
                kind="play",
                start_pos=start_pos,
                target_pos=get_discard_pile_rect(screen_rect).center,
                start_rotation=start_rotation,
                target_rotation=get_player_card_rotation(actor_id, self._base_game.num_players) + self.ai_rng.uniform(-15.0, 15.0),
            )
            return
        if drew_card is not None and action in {"draw", "draw_keep"}:
            source_card = drew_card
            reveal_hand_card = False
            if actor_id == 0:
                added_local_card = self._find_added_local_card(previous_game, drew_card)
                if added_local_card is not None:
                    source_card = added_local_card
                    self.hidden_hand_card_ids.add(id(source_card))
                    reveal_hand_card = True
            self.game.is_animating = True
            self._spawn_active_card(
                card=source_card,
                owner_id=actor_id,
                kind="draw",
                start_pos=get_draw_pile_rect(screen_rect.width, screen_rect.height).center,
                target_pos=get_player_anchor_point(screen_rect, actor_id, self._base_game.num_players),
                start_rotation=0.0,
                target_rotation=get_player_card_rotation(actor_id, self._base_game.num_players),
                reveal_hand_card=reveal_hand_card,
            )
            return

    def _begin_hand_transfer_animation(self, choice_action: PlayerAction, screen: pygame.Surface, now_ms: int) -> None:
        result = self.game.submit_action(choice_action)
        self._record_player_action_result(result, now_ms)

    def _update_hand_transfer_animation(self, screen: pygame.Surface, dt: float, now_ms: int) -> None:
        animation = self.hand_transfer_animation
        if animation is None:
            return

        if animation.phase == 1:
            all_finished = True
            for active_card in animation.cards:
                finished = active_card.update(dt)
                active_card.card.current_pos = active_card.current_pos
                active_card.card.current_rotation = active_card.current_rotation
                active_card.card.current_scale = active_card.current_scale
                if not finished:
                    all_finished = False

            if not all_finished:
                return

            if self._queued_hand_transfer_payload is None:
                return

            snapshot = self._queued_hand_transfer_payload
            previous_game = self._base_game
            self._apply_network_snapshot(snapshot, previous_game)
            self._queued_hand_transfer_payload = None
            self._awaiting_hand_transfer_snapshot = False
            self._hand_transfer_pre_signature = None
            self.game.is_animating = True

            screen_rect = screen.get_rect()
            center = get_discard_pile_rect(screen_rect).center
            center_point = (float(center[0]), float(center[1]))
            owner_offsets: dict[int, int] = {}
            for active_card in animation.cards:
                new_owner = animation.target_owner_by_card_id.get(id(active_card.card), active_card.owner_id)
                owner_offsets[new_owner] = owner_offsets.get(new_owner, 0) + 1
                offset_index = owner_offsets[new_owner] - 1
                stagger_x = float((offset_index % 5 - 2) * 12)
                stagger_y = float((offset_index // 5) * 8)
                target_anchor = get_player_anchor_point(screen_rect, new_owner, self.game.num_players)

                active_card.owner_id = new_owner
                active_card.current_pos = center_point
                active_card.target_pos = (float(target_anchor[0] + stagger_x), float(target_anchor[1] + stagger_y))
                active_card.current_rotation = 0.0
                active_card.target_rotation = get_player_card_rotation(new_owner, self.game.num_players)
                active_card.current_scale = 0.88
                active_card.target_scale = 1.0

            animation.phase = 2
            self.next_ai_time = max(self.next_ai_time, now_ms + self.AI_TURN_DELAY_MS)
            return

        all_finished = True
        for active_card in animation.cards:
            finished = active_card.update(dt)
            active_card.card.current_pos = active_card.current_pos
            active_card.card.current_rotation = active_card.current_rotation
            active_card.card.current_scale = active_card.current_scale
            if not finished:
                all_finished = False

        if not all_finished:
            return

        self.hand_transfer_animation = None
        self.visual_state = ""
        self._hand_layout_initialized = False
        self.game.is_animating = False
        self._prune_hidden_hand_cards()
        self.next_ai_time = max(self.next_ai_time, now_ms + self.AI_TURN_DELAY_MS)

    def _update_direction_arrow(self, dt: float) -> None:
        self.direction_arrow_speed = self.DIRECTION_ARROW_BASE_SPEED * self.game.turn_direction
        self.direction_arrow_angle = (self.direction_arrow_angle + self.direction_arrow_speed * dt) % 360.0

    def _close_network(self) -> None:
        if self.client is not None:
            self.client.send({"type": "leave"})
            self.client.close()
            self.client = None
        if self.host is not None:
            self.host.leave_host()
            self.host = None

    def _activate_pause_menu_option(self, option: str) -> ScreenResult:
        if option == "return_title":
            self._close_network()
        return super()._activate_pause_menu_option(option)

    def _schedule_reaction_ai(self, now_ms: int) -> None:
        # Host already resolves AI/reaction timing and broadcasts authoritative snapshots.
        return

    def _submit_ai_reactions(self, now_ms: int) -> None:
        return

    def _build_ai_hand_transfer_action(self) -> PlayerAction | None:
        return None

    def update(self, screen: pygame.Surface, now_ms: int) -> Optional[BaseScreen]:
        if self.client is not None:
            for packet in self.client.poll_messages():
                packet_type = packet.get("type")
                if packet_type == "match_sync":
                    self._sync_from_packet(packet)
                elif packet_type == "room_state":
                    room_payload = packet.get("room")
                    if isinstance(room_payload, dict):
                        self.room_state = room_payload
                        if not bool(room_payload.get("started", False)):
                            return self._handoff_to_room_screen("Match ended. Returned to room.")
                elif packet_type == "match_ended":
                    room_payload = packet.get("room")
                    if isinstance(room_payload, dict):
                        self.room_state = room_payload
                    message = str(packet.get("message", "Match ended. Returned to room.")).strip()
                    return self._handoff_to_room_screen(message)
                elif packet_type == "action_ack":
                    if not packet.get("ok", False):
                        self._action_in_flight = False
                        self.last_message = str(packet.get("message", "Action rejected by host."))
                elif packet_type == "disconnected":
                    self._close_network()
                    return TitleScreen(self.atlas, self.audio_settings)

        if self.is_host_player and self.host is not None:
            sync_packet = self.host.current_match_sync()
            if sync_packet is not None:
                self._sync_from_packet(sync_packet)
            room_snapshot = self.host.room_state
            self.room_state = room_snapshot
            if not bool(room_snapshot.get("started", False)):
                return self._handoff_to_room_screen("Match ended. Returned to room.")

        if self._pending_network_message:
            self.last_message = self._pending_network_message
            self._pending_network_message = ""

        dt = 0.0 if self._last_update_ms is None else max(0.0, (now_ms - self._last_update_ms) / 1000.0)
        self._last_update_ms = now_ms
        self._update_screen_shake(dt)
        self._update_uno_flash(dt)

        if self.game.winner is not None:
            winner_name = self.seat_names.get(self.game.winner, f"Player {self.game.winner + 1}")
            return self._handoff_to_room_screen(f"Match ended. Winner: {winner_name}.")

        if self.pause_menu_open:
            return None

        self._update_direction_arrow(dt)
        self._update_active_cards(dt)
        if self.hand_transfer_animation is not None:
            self.visual_state = HAND_TRANSFER_ANIMATION
            self._update_hand_transfer_animation(screen, dt, now_ms)
            return None
        self.visual_state = ""

        self.hovered_index = None
        self.wild_hovered_color = None
        if self.game.pending_draw_decision_card is None:
            self.pending_draw_decision_card = None
            self.pending_draw_decision_choosing_color = False

        if self._wild_color_picker_active():
            self.wild_hovered_color = get_wild_color_at_pos(pygame.mouse.get_pos(), screen.get_rect())

        if (
            not self._has_modal_input()
            and self.game.winner is None
            and self.game.current_player == 0
        ):
            self.hovered_index = get_hovered_hand_index(
                pygame.mouse.get_pos(),
                self.game.player_hands[0],
                screen.get_width(),
                screen.get_height(),
                hidden_card_ids=self._effective_hidden_ids(),
            )

        self._update_player_hand_animation(screen, dt)
        return None
