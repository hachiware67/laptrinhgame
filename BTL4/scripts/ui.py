import math
from functools import lru_cache

import pygame
from scripts.assets import asset_path
from scripts.cards import Card
from scripts.game_manager import (
    PASS_CLOCKWISE,
    PASS_COUNTER_CLOCKWISE,
    RULE_REACTION,
    RULE_SEVEN_TARGET,
    RULE_ZERO_DIRECTION,
    UnoGameManager,
)
from scripts.sprites import CardSpriteAtlas
from scripts.animation import transform_card_surface

PLAYER_CARD_SIZE = (88, 130)
OPPONENT_HORIZONTAL_SIZE = (70, 102)
OPPONENT_SIDE_SIZE = (102, 70)
HAND_SPACING = 34
HOVER_LIFT = 28
TABLE_CARD_SIZE = (104, 154)
DIRECTION_ARROW_SIZE = 280
TOP_OPPONENT_CARD_Y = 170
TOP_OPPONENT_LABEL_GAP = 24
PENALTY_BADGE_TOP_Y = 92
WILD_WHEEL_RADIUS = 142
HAND_ARC_DEPTH = 42
HAND_ARC_ROTATION = 16.0
TABLE_TEXTURE_PATH = asset_path("enhance", "close-up-natural-texture.jpg")
HUD_PANEL_PATH = asset_path(
    "enhance",
    "kenney_ui-pack",
    "PNG",
    "Grey",
    "Default",
    "button_rectangle_depth_gloss.png",
)
DIRECTION_ICON_PATH = (
    asset_path(
        "enhance",
        "kenney_ui-pack",
        "PNG",
        "Extra",
        "Default",
        "icon_arrow_up_light.png",
    )
)
LILITA_FONT_PATH = asset_path("enhance", "Lilita_One", "LilitaOne-Regular.ttf")
BACKGROUND_DARK = (10, 18, 28)
FELT_GREEN = (18, 96, 72)
DARK_PANEL = (18, 24, 34)
LIGHT_BORDER = (230, 238, 245)
UNO_RED = (225, 55, 55)
UNO_YELLOW = (245, 205, 65)
UNO_GREEN = (65, 175, 95)
UNO_BLUE = (70, 130, 225)
TEXT_LIGHT = (238, 244, 248)
_EXTENSION_TOOLTIPS: dict[str, tuple[str, list[str]]] = {
    "draw67":      ("Mixi Airstrike",      ["+67.", "Only Mixi Airstrikes stack with each other, and cannot be countered."]),
    "counter":     ("Mixi Counter",        ["Dogs will pay.", "Playable only when a +2 or +4 penalty is active."]),
    "silence":     ("Faker's Silence",     ["The next player will feel Faker's aura (skipped for 3 turns)."]),
    "mom_may_cry": ("Mom Physics May Cry", ["Cuts your hand down to 7 random cards and shuffles the rest back into the draw pile."]),
    "flashbang":   ("Mixi Smile",          ["Flashbangs everyone: your next turn is face-down once, every other player is face-down twice."]),
}
WILD_WHEEL_SEGMENTS = (
    ("red", 180.0, 270.0),
    ("blue", 270.0, 360.0),
    ("green", 0.0, 90.0),
    ("yellow", 90.0, 180.0),
)
WILD_COLOR_RGB = {
    "red": UNO_RED,
    "yellow": UNO_YELLOW,
    "green": UNO_GREEN,
    "blue": UNO_BLUE,
}


def _clamp(value: int, lower: int, upper: int) -> int:
    return max(lower, min(upper, value))


def _safe_load_image(path) -> pygame.Surface | None:
    try:
        if not path.exists():
            return None
        return pygame.image.load(str(path)).convert_alpha()
    except pygame.error:
        return None


@lru_cache(maxsize=1)
def _table_texture_base() -> pygame.Surface | None:
    try:
        if not TABLE_TEXTURE_PATH.exists():
            return None
        return pygame.image.load(str(TABLE_TEXTURE_PATH)).convert()
    except pygame.error:
        return None


@lru_cache(maxsize=8)
def _table_texture_scaled(width: int, height: int) -> pygame.Surface | None:
    base = _table_texture_base()
    if base is None:
        return None
    return pygame.transform.smoothscale(base, (width, height))


@lru_cache(maxsize=1)
def _hud_panel_base() -> pygame.Surface | None:
    return _safe_load_image(HUD_PANEL_PATH)


@lru_cache(maxsize=16)
def _hud_panel_scaled(width: int, height: int) -> pygame.Surface | None:
    base = _hud_panel_base()
    if base is None:
        return None
    return pygame.transform.smoothscale(base, (width, height))


@lru_cache(maxsize=1)
def _direction_arrow_icon() -> pygame.Surface | None:
    return _safe_load_image(DIRECTION_ICON_PATH)


@lru_cache(maxsize=128)
def _scaled_font(width: int, height: int, size: int, bold: bool = False) -> pygame.font.Font:
    scaled_size = _clamp(int(size * _ui_scale(width, height)), 14, size)
    if LILITA_FONT_PATH.exists():
        try:
            return pygame.font.Font(str(LILITA_FONT_PATH), scaled_size)
        except pygame.error:
            pass
    return pygame.font.SysFont("verdana", scaled_size, bold=bold)


def _ui_scale(width: int, height: int) -> float:
    return max(0.75, min(1.08, min(width / 1920, height / 1080)))


def _render_fit_text(
    font: pygame.font.Font,
    text: str,
    color: tuple[int, int, int],
    max_width: int,
) -> pygame.Surface:
    if max_width <= 0:
        return font.render("", True, color)

    rendered = font.render(text, True, color)
    if rendered.get_width() <= max_width:
        return rendered

    ellipsis = "..."
    trimmed = text
    while trimmed and font.size(trimmed + ellipsis)[0] > max_width:
        trimmed = trimmed[:-1]
    return font.render(trimmed.rstrip() + ellipsis, True, color)


def _top_opponent_card_y(height: int) -> int:
    return _clamp(int(height * 0.157), 112, TOP_OPPONENT_CARD_Y)


def _bottom_hand_margin(height: int) -> int:
    return _clamp(int(height * 0.09), 72, 96)


def _side_opponent_margin(width: int) -> int:
    return _clamp(int(width * 0.022), 24, 42)


def get_table_card_size(screen_rect: pygame.Rect) -> tuple[int, int]:
    scale = _ui_scale(screen_rect.width, screen_rect.height)
    return (int(TABLE_CARD_SIZE[0] * scale), int(TABLE_CARD_SIZE[1] * scale))


def get_direction_arrow_size(screen_rect: pygame.Rect) -> int:
    return _clamp(int(min(screen_rect.width, screen_rect.height) * 0.23), 160, DIRECTION_ARROW_SIZE)


def card_rect_for_hand(
    index: int,
    hand_size: int,
    width: int,
    height: int,
    hovered: bool = False,
) -> pygame.Rect:
    card_w, card_h = PLAYER_CARD_SIZE
    if hand_size <= 1:
        x = width // 2 - card_w // 2
        y = height - card_h - _bottom_hand_margin(height)
        if hovered:
            y -= HOVER_LIFT
        return pygame.Rect(x, y, card_w, card_h)

    spacing = _clamp(HAND_SPACING, 24, 38)
    half_span = (hand_size - 1) * spacing * 0.5
    center_x = width * 0.5
    t = (index / (hand_size - 1)) * 2.0 - 1.0
    x = int(center_x + t * half_span - card_w / 2)

    base_y = height - card_h - _bottom_hand_margin(height)
    arc_shift = int((abs(t) ** 1.45) * HAND_ARC_DEPTH)
    y = base_y + arc_shift
    if hovered:
        y -= HOVER_LIFT
    return pygame.Rect(x, y, card_w, card_h)


def get_player_hand_rotation(index: int, hand_size: int) -> float:
    if hand_size <= 1:
        return 0.0
    t = (index / (hand_size - 1)) * 2.0 - 1.0
    return t * HAND_ARC_ROTATION


def get_hovered_hand_index(
    mouse_pos: tuple[int, int],
    hand: list,
    width: int,
    height: int,
    hidden_card_ids: set[int] | None = None,
) -> int | None:
    hidden_card_ids = hidden_card_ids or set()
    for i in range(len(hand) - 1, -1, -1):
        card = hand[i]
        if id(card) in hidden_card_ids:
            continue
        rect = pygame.Rect(int(card.current_pos[0]), int(card.current_pos[1]), *PLAYER_CARD_SIZE)
        if rect.collidepoint(mouse_pos):
            return i
    return None


def get_card_rect_from_pos(card) -> pygame.Rect:
    return pygame.Rect(int(card.current_pos[0]), int(card.current_pos[1]), *PLAYER_CARD_SIZE)


def get_player_hand_card_rects(
    screen_rect: pygame.Rect,
    player_id: int,
    num_players: int,
    hand_size: int,
    cards: list[Card] | None = None,
    use_current_positions: bool = False,
) -> list[pygame.Rect]:
    if player_id == 0:
        if use_current_positions and cards is not None and cards:
            return [get_card_rect_from_pos(card) for card in cards]
        return [card_rect_for_hand(i, hand_size, screen_rect.width, screen_rect.height, hovered=False) for i in range(hand_size)]

    return _opponent_card_rects(_opponent_positions(num_players).get(player_id, "top"), hand_size, screen_rect.width, screen_rect.height)


def get_player_anchor_point(screen_rect: pygame.Rect, player_id: int, num_players: int) -> tuple[float, float]:
    top_opponent_anchor_y = screen_rect.top + _top_opponent_card_y(screen_rect.height) + OPPONENT_HORIZONTAL_SIZE[1] // 2
    bottom_player_anchor_y = screen_rect.bottom - PLAYER_CARD_SIZE[1] // 2 - _bottom_hand_margin(screen_rect.height)
    side_anchor_x = _side_opponent_margin(screen_rect.width) + OPPONENT_SIDE_SIZE[0] + 6
    if num_players == 4:
        anchors = {
            0: (screen_rect.centerx, bottom_player_anchor_y),
            1: (screen_rect.left + side_anchor_x, screen_rect.centery),
            2: (screen_rect.centerx, top_opponent_anchor_y),
            3: (screen_rect.right - side_anchor_x, screen_rect.centery),
        }
        return anchors.get(player_id, (screen_rect.centerx, screen_rect.centery))

    if player_id == 0:
        return (screen_rect.centerx, bottom_player_anchor_y)
    if player_id == 1:
        return (screen_rect.centerx, top_opponent_anchor_y)
    if player_id == 2:
        return (screen_rect.left + side_anchor_x, screen_rect.centery)
    return (screen_rect.right - side_anchor_x, screen_rect.centery)


def get_player_card_rotation(player_id: int, num_players: int) -> float:
    if num_players == 4:
        rotations = {0: 0.0, 1: 90.0, 2: 0.0, 3: -90.0}
        return rotations.get(player_id, 0.0)
    if player_id == 2:
        return 90.0
    if player_id == 3:
        return -90.0
    return 0.0


def get_discard_pile_rect(screen_rect: pygame.Rect) -> pygame.Rect:
    card_w, card_h = get_table_card_size(screen_rect)
    gap = int(36 * _ui_scale(screen_rect.width, screen_rect.height))
    return pygame.Rect(screen_rect.centerx + gap // 2, screen_rect.centery - card_h // 2, card_w, card_h)


def get_direction_indicator_center(screen_rect: pygame.Rect) -> tuple[int, int]:
    draw_rect = get_draw_pile_rect(screen_rect.width, screen_rect.height)
    discard_rect = get_discard_pile_rect(screen_rect)
    return ((draw_rect.centerx + discard_rect.centerx) // 2, (draw_rect.centery + discard_rect.centery) // 2)


@lru_cache(maxsize=8)
def _vignette_overlay(width: int, height: int) -> pygame.Surface:
    overlay = pygame.Surface((width, height), pygame.SRCALPHA)
    bands = 18
    max_alpha = 72
    for i in range(bands):
        t = i / bands
        alpha = int(max_alpha * (1.0 - t) ** 2)
        band_w = max(1, int(width * 0.006))
        band_h = max(1, int(height * 0.006))
        x = int(i * band_w)
        y = int(i * band_h)
        pygame.draw.rect(overlay, (0, 0, 0, alpha), (x, y, band_w, height - y * 2))
        pygame.draw.rect(overlay, (0, 0, 0, alpha), (width - x - band_w, y, band_w, height - y * 2))
        pygame.draw.rect(overlay, (0, 0, 0, alpha), (x, y, width - x * 2, band_h))
        pygame.draw.rect(overlay, (0, 0, 0, alpha), (x, height - y - band_h, width - x * 2, band_h))
    return overlay


def _table_rect(width: int, height: int) -> pygame.Rect:
    margin_x = _clamp(int(width * 0.07), 54, 150)
    top = _clamp(int(height * 0.105), 72, 118)
    bottom_margin = _clamp(int(height * 0.16), 112, 176)
    return pygame.Rect(margin_x, top, width - margin_x * 2, height - top - bottom_margin)


@lru_cache(maxsize=8)
def _felt_noise(width: int, height: int) -> pygame.Surface:
    surface = pygame.Surface((width, height), pygame.SRCALPHA)
    step = 18
    for y in range(-height, height, step):
        pygame.draw.line(surface, (255, 255, 255, 7), (0, y), (width, y + width), 1)
    for y in range(0, height + width, step * 2):
        pygame.draw.line(surface, (0, 0, 0, 10), (0, y), (width, y - width), 1)
    return surface


def _draw_table_background(screen: pygame.Surface) -> None:
    width, height = screen.get_size()
    screen.fill(BACKGROUND_DARK)

    table_rect = _table_rect(width, height)
    shadow_rect = table_rect.move(0, 10)
    pygame.draw.rect(screen, (0, 0, 0, 96), shadow_rect, border_radius=32)
    pygame.draw.rect(screen, (9, 47, 43), table_rect.inflate(22, 18), border_radius=34)
    pygame.draw.rect(screen, (38, 130, 91), table_rect.inflate(12, 10), border_radius=30)
    pygame.draw.rect(screen, FELT_GREEN, table_rect, border_radius=26)

    texture = _table_texture_scaled(table_rect.width, table_rect.height)
    if texture is not None:
        texture = texture.copy()
        texture.set_alpha(14)
        mask = pygame.Surface(table_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=26)
        texture.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        screen.blit(texture, table_rect)
    else:
        screen.blit(_felt_noise(table_rect.width, table_rect.height), table_rect)

    pygame.draw.rect(screen, (86, 180, 128), table_rect, width=2, border_radius=26)
    pygame.draw.rect(screen, (7, 46, 41), table_rect.inflate(-28, -28), width=1, border_radius=20)
    screen.blit(_vignette_overlay(width, height), (0, 0))


def _blit_card_shadow(
    screen: pygame.Surface,
    center: tuple[int, int],
    card_size: tuple[int, int],
    alpha: int = 85,
    y_offset: int = 8,
    spread: float = 0.72,
) -> None:
    shadow_w = max(18, int(card_size[0] * spread))
    shadow_h = max(8, int(card_size[1] * 0.16))
    shadow = pygame.Surface((shadow_w, shadow_h), pygame.SRCALPHA)
    pygame.draw.ellipse(shadow, (0, 0, 0, _clamp(alpha, 10, 210)), shadow.get_rect())
    rect = shadow.get_rect(center=(center[0], center[1] + y_offset))
    screen.blit(shadow, rect)


def _stable_discard_jitter(card: Card) -> tuple[int, int, float]:
    seed = id(card) & 0xFFFFFFFF
    x = ((seed >> 1) % 17) - 8
    y = ((seed >> 6) % 13) - 6
    rotation = (((seed >> 11) % 2900) / 100.0) - 14.5
    return x, y, rotation


def _polar_point(center: tuple[float, float], radius: float, angle_degrees: float) -> tuple[float, float]:
    angle_radians = math.radians(angle_degrees)
    return (center[0] + math.cos(angle_radians) * radius, center[1] + math.sin(angle_radians) * radius)


def _draw_tangent_arrowhead(
    surface: pygame.Surface,
    center: tuple[float, float],
    angle_degrees: float,
    radius: float,
    color: tuple[int, int, int, int],
) -> None:
    angle_radians = math.radians(angle_degrees)
    tip = _polar_point(center, radius, angle_degrees)
    tangent = (-math.sin(angle_radians), math.cos(angle_radians))
    normal = (-tangent[1], tangent[0])
    head_length = 30.0
    head_width = 18.0
    base = (tip[0] - tangent[0] * head_length, tip[1] - tangent[1] * head_length)
    left = (base[0] + normal[0] * head_width, base[1] + normal[1] * head_width)
    right = (base[0] - normal[0] * head_width, base[1] - normal[1] * head_width)
    pygame.draw.polygon(surface, color, [tip, left, right])


@lru_cache(maxsize=8)
def build_direction_arrow_surface(size: int) -> pygame.Surface:
    surface = pygame.Surface((size, size), pygame.SRCALPHA)
    center = (size / 2, size / 2)
    radius = size * 0.38

    guide_color = (210, 225, 230, 34)
    accent_color = (230, 238, 245, 92)
    shadow_color = (0, 0, 0, 44)

    arc_rect = pygame.Rect(center[0] - radius, center[1] - radius, radius * 2, radius * 2)
    pygame.draw.circle(surface, guide_color, (int(center[0]), int(center[1])), int(radius), width=3)

    for start_angle in (-76, 14, 104, 194):
        end_angle = start_angle + 58
        pygame.draw.arc(
            surface,
            shadow_color,
            arc_rect.move(0, 3),
            math.radians(start_angle),
            math.radians(end_angle),
            width=10,
        )
        pygame.draw.arc(
            surface,
            accent_color,
            arc_rect,
            math.radians(start_angle),
            math.radians(end_angle),
            width=7,
        )
        _draw_tangent_arrowhead(surface, center, end_angle, radius, accent_color)

    arrow_icon = _direction_arrow_icon()
    if arrow_icon is not None:
        icon_size = _clamp(int(size * 0.13), 24, 64)
        icon = pygame.transform.smoothscale(arrow_icon, (icon_size, icon_size))
        for mid_angle in (-47, 43, 133, 223):
            icon_center = _polar_point(center, radius + size * 0.01, mid_angle)
            oriented = pygame.transform.rotozoom(icon, -(mid_angle + 90), 1.0)
            icon_rect = oriented.get_rect(center=(int(icon_center[0]), int(icon_center[1])))
            surface.blit(oriented, icon_rect)
    return surface


def draw_direction_arrows(screen: pygame.Surface, center: tuple[int, int], angle: float, size: int) -> None:
    base = build_direction_arrow_surface(size)
    rotated = pygame.transform.rotozoom(base, -angle, 1.0)
    pulse = 0.72 + 0.28 * ((math.sin(math.radians(angle * 4.0)) + 1.0) * 0.5)
    rotated.set_alpha(_clamp(int(70 + 42 * pulse), 70, 118))
    rect = rotated.get_rect(center=center)
    screen.blit(rotated, rect)


def _opponent_positions(num_players: int) -> dict[int, str]:
    if num_players == 2:
        return {1: "top"}
    if num_players == 3:
        return {1: "left", 2: "right"}
    return {1: "left", 2: "top", 3: "right"}


def _opponent_card_rects(position: str, count: int, width: int, height: int) -> list[pygame.Rect]:
    rects: list[pygame.Rect] = []
    if count <= 0:
        return rects

    if position == "top":
        card_w, card_h = OPPONENT_HORIZONTAL_SIZE
        spacing = min(22, max(10, 220 // max(1, count - 1))) if count > 1 else 0
        row_width = card_w + (count - 1) * spacing
        start_x = (width - row_width) // 2
        y = _top_opponent_card_y(height)
        for i in range(count):
            rects.append(pygame.Rect(start_x + i * spacing, y, card_w, card_h))
        return rects

    card_w, card_h = OPPONENT_SIDE_SIZE
    spacing = min(18, max(8, 180 // max(1, count - 1))) if count > 1 else 0
    col_height = card_h + (count - 1) * spacing
    start_y = (height - col_height) // 2
    side_margin = _side_opponent_margin(width)
    x = side_margin if position == "left" else width - card_w - side_margin
    for i in range(count):
        rects.append(pygame.Rect(x, start_y + i * spacing, card_w, card_h))
    return rects


def _draw_opponent_hands(
    screen: pygame.Surface,
    game: UnoGameManager,
    atlas: CardSpriteAtlas,
    body_font: pygame.font.Font,
    player_names: dict[int, str] | None = None,
) -> None:
    width, height = screen.get_size()
    back_h = atlas.get_back_surface(*OPPONENT_HORIZONTAL_SIZE)
    side_base = atlas.get_back_surface(OPPONENT_SIDE_SIZE[1], OPPONENT_SIDE_SIZE[0])
    back_left = pygame.transform.rotate(side_base, 90)
    back_right = pygame.transform.rotate(side_base, -90)

    for pid, position in _opponent_positions(game.num_players).items():
        rects = _opponent_card_rects(position, len(game.player_hands[pid]), width, height)
        if position == "top":
            image = back_h
        elif position == "left":
            image = back_left
        else:
            image = back_right
        for rect in rects:
            _blit_card_shadow(screen, rect.center, rect.size, alpha=70, y_offset=6, spread=0.76)
            screen.blit(image, rect)

        player_name = (player_names or {}).get(pid, f"Player {pid + 1}")
        label = body_font.render(f"{player_name}: {len(game.player_hands[pid])}", True, TEXT_LIGHT)
        if position == "top" and rects:
            label_rect = label.get_rect(center=(width // 2, rects[0].top - TOP_OPPONENT_LABEL_GAP))
        elif position == "left" and rects:
            label_rect = label.get_rect(midleft=(28, rects[0].top - 14))
        elif position == "right" and rects:
            label_rect = label.get_rect(midright=(width - 28, rects[0].top - 14))
        else:
            label_rect = label.get_rect(topleft=(20, 20))
        panel_rect = label_rect.inflate(28, 14)
        panel = pygame.Surface(panel_rect.size, pygame.SRCALPHA)
        pygame.draw.rect(panel, (*DARK_PANEL, 205), panel.get_rect(), border_radius=10)
        is_current_turn = pid == game.current_player
        border_color = (255, 220, 120, 210) if is_current_turn else (*LIGHT_BORDER, 86)
        border_width = 3 if is_current_turn else 2
        pygame.draw.rect(panel, border_color, panel.get_rect(), width=border_width, border_radius=10)
        screen.blit(panel, panel_rect)
        screen.blit(label, label_rect)


def get_draw_pile_rect(width: int, height: int) -> pygame.Rect:
    screen_rect = pygame.Rect(0, 0, width, height)
    card_w, card_h = get_table_card_size(screen_rect)
    gap = int(36 * _ui_scale(width, height))
    top_rect = pygame.Rect(width // 2 + gap // 2, height // 2 - card_h // 2, card_w, card_h)
    return pygame.Rect(top_rect.left - card_w - gap, top_rect.top, card_w, card_h)


def get_color_picker_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
    picker_w = 420
    picker_h = 90
    start_x = screen_rect.centerx - picker_w // 2
    y = screen_rect.centery + 140
    size = 80
    gap = 20

    return {
        "red": pygame.Rect(start_x, y, size, size),
        "yellow": pygame.Rect(start_x + (size + gap), y, size, size),
        "green": pygame.Rect(start_x + 2 * (size + gap), y, size, size),
        "blue": pygame.Rect(start_x + 3 * (size + gap), y, size, size),
    }


def get_wild_color_wheel_center(screen_rect: pygame.Rect) -> tuple[int, int]:
    return screen_rect.center


def get_wild_color_at_pos(mouse_pos: tuple[int, int], screen_rect: pygame.Rect) -> str | None:
    center = get_wild_color_wheel_center(screen_rect)
    dx = mouse_pos[0] - center[0]
    dy = mouse_pos[1] - center[1]
    if dx * dx + dy * dy > WILD_WHEEL_RADIUS * WILD_WHEEL_RADIUS:
        return None

    if dx == 0 and dy == 0:
        return None

    angle = (math.degrees(math.atan2(dy, dx)) + 360.0) % 360.0
    for color, start_angle, end_angle in WILD_WHEEL_SEGMENTS:
        if start_angle <= angle < end_angle:
            return color
    return "blue"


def get_rule_zero_choice_rects(screen_rect: pygame.Rect) -> dict[int, pygame.Rect]:
    button_w = 220
    button_h = 76
    gap = 24
    total_w = button_w * 2 + gap
    start_x = screen_rect.centerx - total_w // 2
    y = screen_rect.centery + 132

    return {
        PASS_CLOCKWISE: pygame.Rect(start_x, y, button_w, button_h),
        PASS_COUNTER_CLOCKWISE: pygame.Rect(start_x + button_w + gap, y, button_w, button_h),
    }


def get_rule_seven_target_rects(game: UnoGameManager, screen_rect: pygame.Rect) -> dict[int, pygame.Rect]:
    targets = [player_id for player_id in range(game.num_players) if player_id != game.pending_effect_player]
    count = len(targets)
    button_w = 190
    button_h = 76
    gap = 18
    total_w = count * button_w + max(0, count - 1) * gap
    start_x = screen_rect.centerx - total_w // 2
    y = screen_rect.centery + 132

    rects: dict[int, pygame.Rect] = {}
    for index, player_id in enumerate(targets):
        rects[player_id] = pygame.Rect(start_x + index * (button_w + gap), y, button_w, button_h)
    return rects


def get_reaction_button_rect(screen_rect: pygame.Rect) -> pygame.Rect:
    return pygame.Rect(screen_rect.centerx - 130, screen_rect.centery + 188, 260, 76)


def get_reaction_panel_rect(screen_rect: pygame.Rect) -> pygame.Rect:
    panel_w = min(700, screen_rect.width - 96)
    panel_h = 238
    y = min(screen_rect.centery + 72, screen_rect.bottom - panel_h - 34)
    return pygame.Rect(screen_rect.centerx - panel_w // 2, y, panel_w, panel_h)


def get_draw_decision_button_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
    button_w = 150
    button_h = 64
    gap = 22
    total_w = button_w * 2 + gap
    start_x = screen_rect.centerx - total_w // 2
    y = screen_rect.centery + 94
    return {
        "play": pygame.Rect(start_x, y, button_w, button_h),
        "keep": pygame.Rect(start_x + button_w + gap, y, button_w, button_h),
    }


def get_uno_button_rect(screen_rect: pygame.Rect) -> pygame.Rect:
    button_w = 128
    button_h = 64
    margin = max(24, int(screen_rect.width * 0.025))
    footer_height = _clamp(int(screen_rect.height * 0.045), 34, 48)
    y = screen_rect.bottom - footer_height - button_h - 22
    return pygame.Rect(screen_rect.right - margin - button_w, y, button_w, button_h)


def get_sort_hand_button_rect(screen_rect: pygame.Rect) -> pygame.Rect:
    button_w = 154
    button_h = 64
    gap = 14
    margin = max(24, int(screen_rect.width * 0.025))
    footer_height = _clamp(int(screen_rect.height * 0.045), 34, 48)
    y = screen_rect.bottom - footer_height - button_h - 22
    uno_rect = pygame.Rect(screen_rect.right - margin - 128, y, 128, button_h)
    return pygame.Rect(uno_rect.left - gap - button_w, y, button_w, button_h)


def theme_font(width: int, height: int, size: int, bold: bool = False) -> pygame.font.Font:
    return _scaled_font(width, height, size, bold=bold)


@lru_cache(maxsize=8)
def _menu_background(width: int, height: int) -> pygame.Surface:
    surface = pygame.Surface((width, height))
    surface.fill(BACKGROUND_DARK)

    top_band = pygame.Surface((width, max(1, height // 3)), pygame.SRCALPHA)
    top_band.fill((18, 30, 44, 86))
    surface.blit(top_band, (0, 0))

    vignette = _vignette_overlay(width, height)
    surface.blit(vignette, (0, 0))
    return surface


def draw_theme_background(screen: pygame.Surface) -> None:
    screen.blit(_menu_background(*screen.get_size()), (0, 0))


def draw_theme_panel(screen: pygame.Surface, rect: pygame.Rect, alpha: int = 205) -> None:
    _draw_hud_glass_panel(screen, rect, alpha=alpha)


def _text_color_for_fill(fill: tuple[int, int, int]) -> tuple[int, int, int]:
    brightness = fill[0] * 0.299 + fill[1] * 0.587 + fill[2] * 0.114
    return BACKGROUND_DARK if brightness > 155 else TEXT_LIGHT


def draw_theme_button(
    screen: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    fill: tuple[int, int, int],
    border: tuple[int, int, int] | None = None,
    text_color: tuple[int, int, int] | None = None,
    selected: bool = False,
    font_size: int = 26,
) -> None:
    border = border or LIGHT_BORDER
    shadow = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0, 0, 0, 92), shadow.get_rect(), border_radius=12)
    screen.blit(shadow, rect.move(0, 5))

    pygame.draw.rect(screen, fill, rect, border_radius=12)
    pygame.draw.rect(screen, tuple(min(255, c + 22) for c in fill), rect.inflate(-6, -6), width=2, border_radius=9)
    pygame.draw.rect(screen, border, rect, width=3 if selected else 2, border_radius=12)

    font = _scaled_font(screen.get_width(), screen.get_height(), font_size, bold=True)
    rendered = _render_fit_text(font, label, text_color or _text_color_for_fill(fill), rect.width - 26)
    screen.blit(rendered, rendered.get_rect(center=rect.center))


def _draw_button(
    screen: pygame.Surface,
    rect: pygame.Rect,
    label: str,
    fill: tuple[int, int, int],
    border: tuple[int, int, int] = (255, 255, 255),
) -> None:
    draw_theme_button(screen, rect, label, fill, border)


def _draw_uno_button(screen: pygame.Surface, rect: pygame.Rect, enabled: bool, armed: bool) -> None:
    if armed:
        fill = UNO_YELLOW
        border = LIGHT_BORDER
        label = "UNO READY"
    elif enabled:
        fill = UNO_RED
        border = UNO_YELLOW
        label = "UNO"
    else:
        fill = (68, 74, 84)
        border = (120, 130, 144)
        label = "UNO"

    shadow = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0, 0, 0, 110), shadow.get_rect(), border_radius=12)
    screen.blit(shadow, rect.move(0, 5))
    pygame.draw.rect(screen, fill, rect, border_radius=12)
    pygame.draw.rect(screen, border, rect, width=3 if enabled or armed else 2, border_radius=12)
    font_size = 20 if armed else 28
    font = _scaled_font(screen.get_width(), screen.get_height(), font_size, bold=True)
    text_color = BACKGROUND_DARK if enabled or armed else (185, 188, 194)
    text = font.render(label, True, text_color)
    screen.blit(text, text.get_rect(center=rect.center))


def _draw_modal_panel(screen: pygame.Surface, rect: pygame.Rect) -> None:
    _draw_hud_glass_panel(screen, rect, alpha=232)


def _sector_points(
    center: tuple[int, int],
    radius: int,
    start_angle: float,
    end_angle: float,
    steps: int = 28,
) -> list[tuple[int, int]]:
    points = [center]
    for step in range(steps + 1):
        angle = start_angle + (end_angle - start_angle) * (step / steps)
        points.append(
            (
                int(center[0] + math.cos(math.radians(angle)) * radius),
                int(center[1] + math.sin(math.radians(angle)) * radius),
            )
        )
    return points


def _brighten(color: tuple[int, int, int], amount: int) -> tuple[int, int, int]:
    return tuple(min(255, component + amount) for component in color)


def _draw_wild_color_wheel(
    screen: pygame.Surface,
    screen_rect: pygame.Rect,
    hovered_color: str | None,
) -> None:
    center = get_wild_color_wheel_center(screen_rect)
    radius = WILD_WHEEL_RADIUS

    glow = pygame.Surface(screen.get_size(), pygame.SRCALPHA)
    pygame.draw.circle(glow, (255, 255, 255, 24), center, radius + 16)
    screen.blit(glow, (0, 0))

    for color, start_angle, end_angle in WILD_WHEEL_SEGMENTS:
        fill = WILD_COLOR_RGB[color]
        if color == hovered_color:
            fill = _brighten(fill, 34)
        pygame.draw.polygon(screen, fill, _sector_points(center, radius, start_angle, end_angle))

    line_color = (245, 245, 245)
    pygame.draw.circle(screen, line_color, center, radius, width=4)
    pygame.draw.line(screen, line_color, (center[0] - radius, center[1]), (center[0] + radius, center[1]), width=4)
    pygame.draw.line(screen, line_color, (center[0], center[1] - radius), (center[0], center[1] + radius), width=4)

    if hovered_color is not None:
        for color, start_angle, end_angle in WILD_WHEEL_SEGMENTS:
            if color == hovered_color:
                pygame.draw.polygon(
                    screen,
                    (255, 255, 255),
                    _sector_points(center, radius, start_angle, end_angle),
                    width=3,
                )
                break


def _draw_draw_decision_prompt(
    screen: pygame.Surface,
    screen_rect: pygame.Rect,
    atlas: CardSpriteAtlas,
    card: Card,
) -> None:
    width, height = screen.get_size()
    overlay = pygame.Surface((width, height), pygame.SRCALPHA)
    overlay.fill((0, 0, 0, 130))
    screen.blit(overlay, (0, 0))

    prompt_font = _scaled_font(width, height, 36, bold=True)
    body_font = _scaled_font(width, height, 24)
    prompt = prompt_font.render("Play the drawn card?", True, (255, 255, 255))
    screen.blit(prompt, prompt.get_rect(center=(screen_rect.centerx, screen_rect.centery - 164)))

    card_rect = pygame.Rect(0, 0, *TABLE_CARD_SIZE)
    card_rect.center = (screen_rect.centerx, screen_rect.centery - 42)
    _blit_card_shadow(screen, card_rect.center, card_rect.size, alpha=110, y_offset=12, spread=0.74)
    card_img = atlas.get_card_surface(card, card_rect.width, card_rect.height)
    screen.blit(card_img, card_rect)

    label = body_font.render(card.short_label, True, (235, 235, 235))
    screen.blit(label, label.get_rect(center=(screen_rect.centerx, screen_rect.centery + 52)))

    rects = get_draw_decision_button_rects(screen_rect)
    _draw_button(screen, rects["play"], "Play", (84, 186, 102))
    _draw_button(screen, rects["keep"], "Keep", (230, 196, 86))


def _draw_hud_glass_panel(screen: pygame.Surface, rect: pygame.Rect, alpha: int = 180) -> None:
    shadow = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(shadow, (0, 0, 0, 82), shadow.get_rect(), border_radius=14)
    screen.blit(shadow, rect.move(0, 5))
    glass = pygame.Surface(rect.size, pygame.SRCALPHA)
    pygame.draw.rect(glass, (*DARK_PANEL, alpha), glass.get_rect(), border_radius=14)
    pygame.draw.rect(glass, (255, 255, 255, 22), glass.get_rect().inflate(-8, -8), width=1, border_radius=10)
    pygame.draw.rect(glass, (*LIGHT_BORDER, 82), glass.get_rect(), width=2, border_radius=14)
    screen.blit(glass, rect)


def draw_card_tooltip(screen: pygame.Surface, card: Card, hovered_rect: pygame.Rect) -> None:
    entry = _EXTENSION_TOOLTIPS.get(card.kind)
    if entry is None:
        return
    title, bullets = entry

    font_title = pygame.font.SysFont("Arial", 15, bold=True)
    font_body = pygame.font.SysFont("Arial", 13)
    PAD = 10
    LINE_GAP = 4
    MAX_WIDTH = 280

    def _wrap(text: str, font: pygame.font.Font, max_w: int) -> list[str]:
        words = text.split()
        lines: list[str] = []
        cur = ""
        for w in words:
            test = (cur + " " + w).strip()
            if font.size(test)[0] <= max_w:
                cur = test
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines

    title_surf = font_title.render(title, True, (255, 220, 80))
    body_surfs: list[pygame.Surface] = []
    for bullet in bullets:
        for line in _wrap("• " + bullet, font_body, MAX_WIDTH - PAD * 2):
            body_surfs.append(font_body.render(line, True, (220, 220, 220)))

    total_h = PAD + title_surf.get_height() + LINE_GAP
    for s in body_surfs:
        total_h += s.get_height() + 2
    total_h += PAD

    box_x = max(4, min(hovered_rect.centerx - MAX_WIDTH // 2, screen.get_width() - MAX_WIDTH - 4))
    box_y = hovered_rect.top - total_h - 8
    tooltip_rect = pygame.Rect(box_x, box_y, MAX_WIDTH, total_h)
    _draw_hud_glass_panel(screen, tooltip_rect, alpha=210)

    y = box_y + PAD
    screen.blit(title_surf, (box_x + PAD, y))
    y += title_surf.get_height() + LINE_GAP
    for s in body_surfs:
        screen.blit(s, (box_x + PAD, y))
        y += s.get_height() + 2


def render_ui(
    screen: pygame.Surface,
    game: UnoGameManager,
    atlas: CardSpriteAtlas,
    now_ms: int,
    selected_index: int,
    last_message: str,
    hovered_index: int | None = None,
    wild_color_picker_active: bool = False,
    hidden_card_ids: set[int] | None = None,
    facedown_card_ids: set[int] | None = None,
    display_top_card: Card | None = None,
    direction_arrow_angle: float = 0.0,
    wild_hovered_color: str | None = None,
    draw_decision_card: Card | None = None,
    player_names: dict[int, str] | None = None,
    local_player_id: int = 0,
    compact_back_rect: pygame.Rect | None = None,
    compact_hidden_count: int = 0,
) -> None:
    width, height = screen.get_size()
    _draw_table_background(screen)

    hidden_card_ids = hidden_card_ids or set()
    facedown_card_ids = facedown_card_ids or set()
    footer_height = _clamp(int(height * 0.045), 34, 48)

    title_font = _scaled_font(width, height, 32, bold=True)
    body_font = _scaled_font(width, height, 24)
    small_font = _scaled_font(width, height, 20)

    screen_rect = screen.get_rect()
    arrow_size = get_direction_arrow_size(screen_rect)
    draw_direction_arrows(screen, get_direction_indicator_center(screen_rect), direction_arrow_angle, arrow_size)

    draw_rect = get_draw_pile_rect(width, height)
    draw_img = atlas.get_back_surface(draw_rect.width, draw_rect.height)
    stack_layers = _clamp(2 + len(game.draw_pile) // 20, 2, 8)
    for layer in range(stack_layers - 1, -1, -1):
        layer_rect = draw_rect.move(-layer, layer)
        _blit_card_shadow(
            screen,
            layer_rect.center,
            layer_rect.size,
            alpha=120 if layer == 0 else 88,
            y_offset=10,
            spread=0.78,
        )
        screen.blit(draw_img, layer_rect)

    discard_rect = get_discard_pile_rect(screen_rect)
    if len(game.discard_pile) > 1:
        for previous_card in game.discard_pile[max(0, len(game.discard_pile) - 5):-1]:
            jitter_x, jitter_y, jitter_rotation = _stable_discard_jitter(previous_card)
            center = (discard_rect.centerx + jitter_x, discard_rect.centery + jitter_y)
            _blit_card_shadow(screen, center, discard_rect.size, alpha=68, y_offset=10, spread=0.72)
            prev_img = atlas.get_card_surface(previous_card, discard_rect.width, discard_rect.height)
            prev_img = transform_card_surface(prev_img, jitter_rotation, 0.98)
            screen.blit(prev_img, prev_img.get_rect(center=center))

    top = display_top_card or game.top_discard
    _blit_card_shadow(screen, discard_rect.center, discard_rect.size, alpha=115, y_offset=11, spread=0.75)
    top_img = atlas.get_card_surface(top, discard_rect.width, discard_rect.height)
    top_img = transform_card_surface(top_img, getattr(top, "current_rotation", 0.0), getattr(top, "current_scale", 1.0))
    screen.blit(top_img, top_img.get_rect(center=discard_rect.center))

    header_margin = max(24, int(width * 0.025))
    left_panel_width = _clamp(int(width * 0.24), 280, 380)  # Reduced from 0.28 to make room for compact badge
    left_panel = pygame.Rect(
        header_margin,
        height - footer_height - 148,
        left_panel_width,
        108,
    )
    right_panel = pygame.Rect(
        width - header_margin - _clamp(int(width * 0.30), 340, 500),
        24,
        _clamp(int(width * 0.30), 340, 500),
        108,
    )
    _draw_hud_glass_panel(screen, left_panel, alpha=174)
    _draw_hud_glass_panel(screen, right_panel, alpha=174)

    current_turn_name = (player_names or {}).get(game.current_player, f"Player {game.current_player + 1}")
    if game.current_player == local_player_id:
        turn_title = f"Your Turn ({current_turn_name})"
    else:
        turn_title = f"Turn: {current_turn_name}"
    turn_lbl = _render_fit_text(
        title_font,
        turn_title,
        TEXT_LIGHT,
        left_panel.width - 26,
    )
    pass_direction_lbl = _render_fit_text(
        body_font,
        f"Pass Direction: {'CW' if game.hand_pass_direction == PASS_CLOCKWISE else 'CCW'}",
        (224, 228, 232),
        left_panel.width - 26,
    )
    screen.blit(turn_lbl, turn_lbl.get_rect(midleft=(left_panel.x + 16, left_panel.y + 38)))
    screen.blit(pass_direction_lbl, pass_direction_lbl.get_rect(midleft=(left_panel.x + 16, left_panel.y + 78)))

    current_color = game.current_color or "none"
    swatch_color = WILD_COLOR_RGB.get(current_color, (132, 140, 152))
    swatch_rect = pygame.Rect(right_panel.x + 18, right_panel.y + 22, 54, 32)
    pygame.draw.rect(screen, swatch_color, swatch_rect, border_radius=16)
    pygame.draw.rect(screen, LIGHT_BORDER, swatch_rect, width=2, border_radius=16)
    active_color_text = _render_fit_text(
        title_font,
        f"Active Color: {current_color.upper()}",
        TEXT_LIGHT,
        right_panel.width - 94,
    )
    draw_count_text = _render_fit_text(
        body_font,
        f"Draw Pile: {len(game.draw_pile)}",
        (224, 228, 232),
        right_panel.width - 26,
    )
    screen.blit(active_color_text, active_color_text.get_rect(midleft=(swatch_rect.right + 12, right_panel.y + 38)))
    screen.blit(draw_count_text, draw_count_text.get_rect(midleft=(right_panel.x + 16, right_panel.y + 78)))

    for label_text, label_center_x, label_top in (
        (f"Draw: {len(game.draw_pile)}", draw_rect.centerx, draw_rect.bottom + 16),
        ("Discard", discard_rect.centerx, discard_rect.bottom + 16),
    ):
        label = small_font.render(label_text, True, TEXT_LIGHT)
        label_rect = label.get_rect(center=(label_center_x, label_top + 13))
        _draw_hud_glass_panel(screen, label_rect.inflate(20, 10), alpha=142)
        screen.blit(label, label_rect)

    penalty_label = game.get_active_effect_label(now_ms)
    if penalty_label and game.pending_effect != RULE_REACTION:
        badge = small_font.render(penalty_label, True, (255, 224, 155))
        badge_bg = badge.get_rect(midtop=(width // 2, PENALTY_BADGE_TOP_Y))
        pygame.draw.rect(screen, (64, 47, 24), badge_bg.inflate(24, 14), border_radius=10)
        screen.blit(badge, badge_bg)

    _draw_opponent_hands(screen, game, atlas, body_font, player_names=player_names)

    if last_message:
        max_status_width = max(240, min(int(width * 0.30), width // 2 - header_margin - 100))
        status_text = _render_fit_text(small_font, last_message, (245, 220, 120), max_status_width)
        status_rect = status_text.get_rect(midleft=(header_margin, 36))
        _draw_hud_glass_panel(screen, status_rect.inflate(22, 12), alpha=140)
        screen.blit(status_text, status_rect)

    footer = pygame.Surface((width, footer_height), pygame.SRCALPHA)
    footer.fill((0, 0, 0, 82))
    screen.blit(footer, (0, height - footer_height))

    help_line = "Click card: select | Enter/Space: play | Draw pile/D: draw | Sort button/S: sort hand | UNO button/U: call UNO"
    help_text = _render_fit_text(small_font, help_line, (220, 220, 220), width - header_margin * 2)
    screen.blit(help_text, help_text.get_rect(center=(width // 2, height - footer_height // 2)))

    hand = game.player_hands[0]
    if hand:
        selected_index = max(0, min(selected_index, len(hand) - 1))

    draw_order = [i for i in range(len(hand)) if i != hovered_index]
    if hovered_index is not None and 0 <= hovered_index < len(hand):
        draw_order.append(hovered_index)

    for i in draw_order:
        card = hand[i]
        if id(card) in hidden_card_ids:
            continue
        is_hovered = i == hovered_index
        rect = get_card_rect_from_pos(card)
        if id(card) in facedown_card_ids:
            card_img = atlas.get_back_surface(rect.width, rect.height)
        else:
            card_img = atlas.get_card_surface(card, rect.width, rect.height)
        card_img = transform_card_surface(card_img, getattr(card, "current_rotation", 0.0), getattr(card, "current_scale", 1.0))
        _blit_card_shadow(
            screen,
            rect.center,
            rect.size,
            alpha=98 if is_hovered else 76,
            y_offset=10 if is_hovered else 8,
            spread=0.76,
        )
        card_rect = card_img.get_rect(center=rect.center)
        screen.blit(card_img, card_rect)

        if i == selected_index:
            pygame.draw.rect(screen, (4, 8, 12), card_rect.inflate(8, 8), width=4, border_radius=12)
            pygame.draw.rect(screen, UNO_YELLOW, card_rect.inflate(6, 6), width=2, border_radius=11)

    if compact_back_rect is not None and compact_hidden_count > 0:
        badge_x = header_margin + left_panel_width + 12
        badge_y = height - footer_height - 126
        badge_font = _scaled_font(width, height, 20, bold=True)
        badge_text = badge_font.render(f"+{compact_hidden_count}", True, (255, 255, 255))
        badge_bg = badge_text.get_rect(topleft=(badge_x, badge_y))
        pygame.draw.rect(screen, (200, 55, 55), badge_bg.inflate(12, 8), border_radius=8)
        pygame.draw.rect(screen, (240, 100, 100), badge_bg.inflate(12, 8), width=2, border_radius=8)
        screen.blit(badge_text, badge_bg)
        cw, ch = PLAYER_CARD_SIZE
        back_img = atlas.get_back_surface(cw, ch)
        compact_card_x = badge_bg.right + 12
        compact_card_y = badge_y - 12
        compact_rect = pygame.Rect(compact_card_x, compact_card_y, cw, ch)
        _blit_card_shadow(screen, compact_rect.center, PLAYER_CARD_SIZE, alpha=80, y_offset=8)
        screen.blit(back_img, compact_rect)

    sort_rect = get_sort_hand_button_rect(screen_rect)
    sort_enabled = game.current_player == 0 and game.pending_effect is None and not wild_color_picker_active and draw_decision_card is None
    draw_theme_button(
        screen,
        sort_rect,
        "SORT HAND",
        (94, 138, 222) if sort_enabled else (68, 74, 84),
        selected=sort_enabled,
        font_size=22,
    )

    uno_rect = get_uno_button_rect(screen_rect)
    uno_enabled = game.current_player == 0 and game.pending_effect is None and game.can_call_uno(0)
    uno_armed = 0 in game.uno_called_players
    _draw_uno_button(screen, uno_rect, uno_enabled, uno_armed)

    if game.pending_effect == RULE_ZERO_DIRECTION and game.current_player == 0:
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        screen.blit(overlay, (0, 0))

        prompt_font = _scaled_font(width, height, 34, bold=True)
        prompt = prompt_font.render("Rule of 0: choose hand pass direction", True, (255, 255, 255))
        screen.blit(prompt, prompt.get_rect(center=(width // 2, height // 2 + 58)))

        for direction, rect in get_rule_zero_choice_rects(screen_rect).items():
            label = "Clockwise" if direction == PASS_CLOCKWISE else "Counter-Clockwise"
            fill = (230, 196, 86) if direction == PASS_CLOCKWISE else (86, 151, 230)
            _draw_button(screen, rect, label, fill)

    elif game.pending_effect == RULE_SEVEN_TARGET and game.current_player == 0:
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 140))
        screen.blit(overlay, (0, 0))

        prompt_font = _scaled_font(width, height, 34, bold=True)
        prompt = prompt_font.render("Rule of 7: choose a target player to swap hands with", True, (255, 255, 255))
        screen.blit(prompt, prompt.get_rect(center=(width // 2, height // 2 + 58)))

        for player_id, rect in get_rule_seven_target_rects(game, screen_rect).items():
            label = f"Player {player_id + 1}"
            fill = (98, 180, 105)
            _draw_button(screen, rect, label, fill)

    elif game.pending_effect == RULE_REACTION:
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 120))
        screen.blit(overlay, (0, 0))

        panel_rect = get_reaction_panel_rect(screen_rect)
        _draw_modal_panel(screen, panel_rect)

        prompt_font = _scaled_font(width, height, 34, bold=True)
        prompt = _render_fit_text(
            prompt_font,
            "Rule of 8: reaction window active",
            (255, 255, 255),
            panel_rect.width - 48,
        )
        screen.blit(prompt, prompt.get_rect(center=(panel_rect.centerx, panel_rect.top + 42)))

        timer = small_font.render(f"Time left: {game.get_reaction_remaining_ms(now_ms) / 1000:.1f}s", True, (255, 226, 145))
        screen.blit(timer, timer.get_rect(center=(panel_rect.centerx, panel_rect.top + 82)))

        reacted = small_font.render(
            f"Reacted: {len(game.pending_reaction_players)} / {game.num_players}",
            True,
            (220, 220, 220),
        )
        screen.blit(reacted, reacted.get_rect(center=(panel_rect.centerx, panel_rect.top + 112)))

        react_rect = get_reaction_button_rect(screen_rect)
        _draw_button(screen, react_rect, "REACT", (233, 126, 68))

    if draw_decision_card is not None and not wild_color_picker_active:
        _draw_draw_decision_prompt(screen, screen_rect, atlas, draw_decision_card)

    if wild_color_picker_active:
        overlay = pygame.Surface((width, height), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 125))
        screen.blit(overlay, (0, 0))

        prompt_font = _scaled_font(width, height, 34, bold=True)
        prompt = prompt_font.render("Choose a color for Wild / +4", True, (255, 255, 255))
        screen.blit(prompt, prompt.get_rect(center=(width // 2, height // 2 - WILD_WHEEL_RADIUS - 48)))
        _draw_wild_color_wheel(screen, screen_rect, wild_hovered_color)


def get_title_screen_button_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
    """Get button rectangles for the title screen."""
    scale = _ui_scale(screen_rect.width, screen_rect.height)
    button_w = _clamp(int(320 * scale), 280, 320)
    button_h = _clamp(int(90 * scale), 68, 90)
    gap = _clamp(int(28 * scale), 16, 28)
    button_order = ("start_local", "multiplayer", "instructions", "settings", "quit")
    y_start = screen_rect.centery + _clamp(int(screen_rect.height * 0.015), 8, 24)
    stack_height = len(button_order) * button_h + (len(button_order) - 1) * gap
    bottom_margin = _clamp(int(screen_rect.height * 0.075), 56, 110)
    bottom_limit = screen_rect.bottom - bottom_margin
    if y_start + stack_height > bottom_limit:
        y_start = bottom_limit - stack_height

    return {
        name: pygame.Rect(
            screen_rect.centerx - button_w // 2,
            y_start + idx * (button_h + gap),
            button_w,
            button_h,
        )
        for idx, name in enumerate(button_order)
    }


def render_title_screen(screen: pygame.Surface) -> None:
    """Render the main title screen."""
    width, height = screen.get_size()
    draw_theme_background(screen)

    title_font = _scaled_font(width, height, 92, bold=True)
    title = title_font.render("UNO Tay`", True, TEXT_LIGHT)
    title_shadow = title_font.render("UNO Tay`", True, (0, 0, 0))
    title_y = max(18, int(height * 0.035))
    screen.blit(title_shadow, title_shadow.get_rect(midtop=(width // 2 + 4, title_y + 5)))
    screen.blit(title, title.get_rect(midtop=(width // 2, title_y)))

    subtitle_font = _scaled_font(width, height, 34)
    subtitle = subtitle_font.render("An UNO game inspired by Domixi", True, (218, 226, 232))
    subtitle_rect = subtitle.get_rect(midtop=(width // 2, title_y + title.get_height() + 6))
    screen.blit(subtitle, subtitle_rect)

    screen_rect = screen.get_rect()
    button_rects = get_title_screen_button_rects(screen_rect)

    button_area = button_rects["start_local"].unionall(list(button_rects.values()))
    panel = button_area.inflate(96, 72)
    _draw_hud_glass_panel(screen, panel, alpha=132)

    _draw_button(screen, button_rects["start_local"], "Start Local Match", UNO_GREEN, border=(160, 235, 172))
    _draw_button(screen, button_rects["multiplayer"], "Multiplayer", UNO_BLUE, border=(160, 195, 245))
    _draw_button(screen, button_rects["instructions"], "Instructions", (233, 126, 68), border=(255, 184, 128))
    _draw_button(screen, button_rects["settings"], "Settings", UNO_YELLOW, border=(255, 236, 145))
    _draw_button(screen, button_rects["quit"], "Quit", UNO_RED, border=(246, 166, 166))


def get_multiplayer_screen_button_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
    """Get button rectangles for the multiplayer submenu."""
    scale = _ui_scale(screen_rect.width, screen_rect.height)
    button_w = _clamp(int(320 * scale), 280, 320)
    button_h = _clamp(int(90 * scale), 68, 90)
    gap = _clamp(int(28 * scale), 16, 28)
    y_start = screen_rect.centery + _clamp(int(screen_rect.height * 0.015), 8, 24)

    return {
        "host_game": pygame.Rect(screen_rect.centerx - button_w // 2, y_start, button_w, button_h),
        "join_game": pygame.Rect(screen_rect.centerx - button_w // 2, y_start + button_h + gap, button_w, button_h),
        "back": pygame.Rect(screen_rect.centerx - button_w // 2, y_start + 2 * (button_h + gap), button_w, button_h),
    }


def render_multiplayer_screen(screen: pygame.Surface) -> None:
    """Render the multiplayer submenu screen."""
    width, height = screen.get_size()
    draw_theme_background(screen)

    title_font = _scaled_font(width, height, 86, bold=True)
    title = title_font.render("MULTIPLAYER", True, TEXT_LIGHT)
    screen.blit(title, title.get_rect(center=(width // 2, height // 2 - 180)))

    subtitle_font = _scaled_font(width, height, 34)
    subtitle = subtitle_font.render("Online menu", True, (218, 226, 232))
    screen.blit(subtitle, subtitle.get_rect(center=(width // 2, height // 2 - 100)))

    screen_rect = screen.get_rect()
    button_rects = get_multiplayer_screen_button_rects(screen_rect)
    button_area = button_rects["host_game"].unionall(list(button_rects.values()))
    panel = button_area.inflate(96, 72)
    _draw_hud_glass_panel(screen, panel, alpha=132)
    _draw_button(screen, button_rects["host_game"], "Host Game", (233, 126, 68), border=(255, 184, 128))
    _draw_button(screen, button_rects["join_game"], "Join Game", UNO_BLUE, border=(160, 195, 245))
    _draw_button(screen, button_rects["back"], "Back", (94, 102, 116), border=(150, 158, 172))

    footer_font = _scaled_font(width, height, 18)
    footer = footer_font.render("Create or join host-authoritative UNO rooms.", True, (170, 180, 190))
    screen.blit(footer, footer.get_rect(center=(width // 2, height - 100)))


def get_end_screen_button_rects(screen_rect: pygame.Rect) -> dict[str, pygame.Rect]:
    """Get button rectangles for the end screen."""
    button_w = 360
    button_h = 96
    y = screen_rect.centery + 240

    return {
        "return_title": pygame.Rect(screen_rect.centerx - button_w // 2, y, button_w, button_h),
    }


def render_end_screen(screen: pygame.Surface, game: UnoGameManager) -> None:
    """Render the game end screen with winner info."""
    width, height = screen.get_size()
    draw_theme_background(screen)

    title_font = _scaled_font(width, height, 88, bold=True)
    section_font = _scaled_font(width, height, 40, bold=True)
    info_font = _scaled_font(width, height, 34)

    if game.winner is not None:
        winner_text = title_font.render(f"Player {game.winner + 1} Wins!", True, UNO_YELLOW)
    else:
        winner_text = title_font.render("Game Over", True, UNO_YELLOW)

    center_x = width // 2
    title_y = height // 2 - 220
    section_y = title_y + 150
    list_start_y = section_y + 70
    line_gap = 56

    panel = pygame.Rect(0, 0, min(680, width - 120), 460)
    panel.center = (center_x, height // 2)
    _draw_hud_glass_panel(screen, panel, alpha=150)

    screen.blit(winner_text, winner_text.get_rect(center=(center_x, title_y)))

    final_hands_text = section_font.render("Final Hands", True, TEXT_LIGHT)
    screen.blit(final_hands_text, final_hands_text.get_rect(center=(center_x, section_y)))

    for pid in range(game.num_players):
        hand_size = len(game.player_hands[pid])
        label = f"Player {pid + 1}: {hand_size} card{'s' if hand_size != 1 else ''}"
        color = UNO_GREEN if pid == game.winner else (220, 228, 232)
        text = info_font.render(label, True, color)
        screen.blit(text, text.get_rect(center=(center_x, list_start_y + pid * line_gap)))

    screen_rect = screen.get_rect()
    button_rects = get_end_screen_button_rects(screen_rect)
    _draw_button(screen, button_rects["return_title"], "Return to Title", UNO_GREEN, border=(160, 235, 172))
