from pathlib import Path
from typing import Dict, Optional, Tuple

import pygame

from scripts.cards import (
    ACTION_COUNTER,
    ACTION_FLASHBANG,
    ACTION_DRAW_67,
    ACTION_DRAW_TWO,
    ACTION_MOM_MAY_CRY,
    ACTION_REVERSE,
    ACTION_SILENCE,
    ACTION_SKIP,
    ACTION_WILD,
    ACTION_WILD_DRAW_FOUR,
    Card,
)

MIXI_CARD_KINDS: frozenset = frozenset(
    {ACTION_COUNTER, ACTION_SILENCE, ACTION_DRAW_67, ACTION_FLASHBANG, ACTION_MOM_MAY_CRY}
)
MIXI_IMAGE_FILES: Dict[str, str] = {
    ACTION_COUNTER: "counter.png",
    ACTION_SILENCE: "silence.jpg",
    ACTION_DRAW_67: "draw67.jpg",
    ACTION_FLASHBANG: "flashbang.webp",
    ACTION_MOM_MAY_CRY: "mom_may_cry.png",
}


class CardSpriteAtlas:
    """Maps UNO card definitions to regions in the provided sprite sheet."""

    # Measured directly from the sprite sheet. The source atlas is not a perfectly
    # uniform grid, so use explicit column / row positions to keep crops snug.
    X_POSITIONS = [0, 167, 335, 502, 670, 837, 1005, 1175, 1342, 1510, 1677, 1845]
    X_WIDTHS = [165, 166, 165, 166, 165, 166, 165, 165, 166, 165, 166, 165]
    Y_POSITIONS = [0, 258, 517, 775, 1034, 1292]
    Y_HEIGHTS = [256, 257, 256, 257, 256, 257]

    def __init__(self, sprite_sheet_path: Path):
        self.sprite_sheet_path = sprite_sheet_path
        if not sprite_sheet_path.exists():
            raise FileNotFoundError(f"Required UNO card atlas is missing: {sprite_sheet_path}")
        try:
            self.sheet = pygame.image.load(str(sprite_sheet_path)).convert_alpha()
        except pygame.error as exc:
            raise pygame.error(f"Could not load required UNO card atlas: {sprite_sheet_path}") from exc
        self.card_map = self._build_card_map()
        self.cache: Dict[Tuple[str, int, int], pygame.Surface] = {}
        self._mixi_surfaces: Dict[str, pygame.Surface] = {}
        for kind, filename in MIXI_IMAGE_FILES.items():
            path = self._resolve_mixi_image_path(sprite_sheet_path.parent, filename)
            if path.exists():
                try:
                    self._mixi_surfaces[kind] = pygame.image.load(str(path)).convert_alpha()
                except pygame.error:
                    pass

    @staticmethod
    def _resolve_mixi_image_path(base_dir: Path, filename: str) -> Path:
        direct = base_dir / filename
        if direct.exists():
            return direct

        stem = Path(filename).stem.lower()
        for candidate in sorted(base_dir.glob(f"{stem}.*")):
            if candidate.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                return candidate
        return direct

    def _src_rect(self, row: int, col: int) -> pygame.Rect:
        return pygame.Rect(
            self.X_POSITIONS[col],
            self.Y_POSITIONS[row],
            self.X_WIDTHS[col],
            self.Y_HEIGHTS[row],
        )

    def _build_card_map(self) -> Dict[Tuple[Optional[str], str, Optional[int]], Tuple[int, int]]:
        # Positions below are the recalculated row / column slots from the atlas image.
        return {
            (None, ACTION_WILD, None): (0, 1),
            ("yellow", ACTION_WILD, None): (0, 2),
            ("red", ACTION_WILD, None): (0, 3),
            ("blue", ACTION_WILD, None): (0, 4),
            ("green", ACTION_WILD, None): (0, 5),
            (None, ACTION_WILD_DRAW_FOUR, None): (0, 6),
            ("yellow", ACTION_WILD_DRAW_FOUR, None): (0, 7),
            ("red", ACTION_WILD_DRAW_FOUR, None): (0, 8),
            ("blue", ACTION_WILD_DRAW_FOUR, None): (0, 9),
            ("green", ACTION_WILD_DRAW_FOUR, None): (0, 10),
            ("yellow", "number", 1): (1, 0),
            ("yellow", "number", 2): (1, 1),
            ("yellow", "number", 3): (1, 2),
            ("yellow", "number", 4): (1, 3),
            ("yellow", "number", 5): (1, 4),
            ("yellow", "number", 6): (1, 5),
            ("yellow", "number", 7): (1, 6),
            ("yellow", "number", 8): (1, 7),
            ("yellow", "number", 9): (1, 8),
            ("yellow", "number", 0): (1, 9),
            ("yellow", ACTION_DRAW_TWO, None): (1, 10),
            ("yellow", ACTION_SKIP, None): (1, 11),
            ("yellow", ACTION_REVERSE, None): (2, 0),
            ("red", "number", 1): (2, 1),
            ("red", "number", 2): (2, 2),
            ("red", "number", 3): (2, 3),
            ("red", "number", 4): (2, 4),
            ("red", "number", 5): (2, 5),
            ("red", "number", 6): (2, 6),
            ("red", "number", 7): (2, 7),
            ("red", "number", 8): (2, 8),
            ("red", "number", 9): (2, 9),
            ("red", "number", 0): (2, 10),
            ("red", ACTION_DRAW_TWO, None): (2, 11),
            ("red", ACTION_SKIP, None): (3, 0),
            ("red", ACTION_REVERSE, None): (3, 1),
            ("blue", "number", 1): (3, 2),
            ("blue", "number", 2): (3, 3),
            ("blue", "number", 3): (3, 4),
            ("blue", "number", 4): (3, 5),
            ("blue", "number", 5): (3, 6),
            ("blue", "number", 6): (3, 7),
            ("blue", "number", 7): (3, 8),
            ("blue", "number", 8): (3, 9),
            ("blue", "number", 9): (3, 10),
            ("blue", "number", 0): (3, 11),
            ("blue", ACTION_DRAW_TWO, None): (4, 0),
            ("blue", ACTION_SKIP, None): (4, 1),
            ("blue", ACTION_REVERSE, None): (4, 2),
            ("green", "number", 1): (4, 3),
            ("green", "number", 2): (4, 4),
            ("green", "number", 3): (4, 5),
            ("green", "number", 4): (4, 6),
            ("green", "number", 5): (4, 7),
            ("green", "number", 6): (4, 8),
            ("green", "number", 7): (4, 9),
            ("green", "number", 8): (4, 10),
            ("green", "number", 9): (4, 11),
            ("green", "number", 0): (5, 0),
            ("green", ACTION_DRAW_TWO, None): (5, 1),
            ("green", ACTION_SKIP, None): (5, 2),
            ("green", ACTION_REVERSE, None): (5, 3),
        }

    def _card_key(self, card: Card) -> Tuple[Optional[str], str, Optional[int]]:
        if card.is_wild:
            # Wild cards in this atlas have dedicated per-color variants on row 0.
            return (card.chosen_color, card.kind, None)
        if card.kind == "number":
            return (card.color, card.kind, card.number)
        return (card.color, card.kind, None)

    def _rounded_radius(self, width: int, height: int) -> int:
        return max(8, min(width, height) // 9)

    def _apply_rounded_corners(self, surface: pygame.Surface) -> pygame.Surface:
        width, height = surface.get_size()
        radius = self._rounded_radius(width, height)

        mask = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.rect(mask, (255, 255, 255, 255), mask.get_rect(), border_radius=radius)

        rounded = surface.copy()
        rounded.blit(mask, (0, 0), special_flags=pygame.BLEND_RGBA_MULT)
        return rounded

    def _apply_wild_color_choice(self, surface: pygame.Surface, chosen_color: str) -> pygame.Surface:
        palette = {
            "red": (224, 68, 68),
            "yellow": (238, 206, 62),
            "green": (76, 178, 92),
            "blue": (72, 128, 220),
        }
        color = palette.get(chosen_color)
        if color is None:
            return surface

        width, height = surface.get_size()
        radius = self._rounded_radius(width, height)
        result = surface.copy()

        glow = pygame.Surface((width, height), pygame.SRCALPHA)
        pygame.draw.rect(glow, (*color, 58), glow.get_rect().inflate(-6, -6), border_radius=radius)
        result.blit(glow, (0, 0))

        border_width = max(4, min(width, height) // 12)
        pygame.draw.rect(
            result,
            color,
            result.get_rect().inflate(-border_width, -border_width),
            width=border_width,
            border_radius=radius,
        )
        pygame.draw.rect(
            result,
            (255, 255, 255),
            result.get_rect().inflate(-(border_width * 3), -(border_width * 3)),
            width=max(2, border_width // 2),
            border_radius=max(4, radius - border_width),
        )

        ribbon = [
            (width, 0),
            (width, int(height * 0.34)),
            (int(width * 0.66), 0),
        ]
        pygame.draw.polygon(result, color, ribbon)
        pygame.draw.line(result, (255, 255, 255), ribbon[1], ribbon[2], width=max(2, border_width // 2))
        return result

    def get_back_surface(self, width: int, height: int) -> pygame.Surface:
        key = ("back", width, height)
        if key in self.cache:
            return self.cache[key]

        src = self._src_rect(0, 0)
        card = self.sheet.subsurface(src).copy()
        scaled = pygame.transform.smoothscale(card, (width, height))
        rounded = self._apply_rounded_corners(scaled)
        self.cache[key] = rounded
        return rounded

    def _get_mixi_card_surface(self, card: Card, width: int, height: int) -> pygame.Surface:
        chosen_color = card.chosen_color if card.is_wild else None
        cache_key = (f"mixi:{card.kind}:{chosen_color}", width, height)
        if cache_key in self.cache:
            return self.cache[cache_key]

        source = self._mixi_surfaces.get(card.kind)
        if source is None:
            src = self._src_rect(0, 0)
            source = self.sheet.subsurface(src).copy()

        scaled = pygame.transform.smoothscale(source, (width, height))
        rounded = self._apply_rounded_corners(scaled)
        if card.is_wild and chosen_color:
            rounded = self._apply_wild_color_choice(rounded, chosen_color)
        self.cache[cache_key] = rounded
        return rounded

    def get_card_surface(self, card: Card, width: int, height: int) -> pygame.Surface:
        if card.kind in MIXI_CARD_KINDS:
            return self._get_mixi_card_surface(card, width, height)

        map_key = self._card_key(card)
        chosen_color = card.chosen_color if card.is_wild else None
        cache_key = (f"{map_key}:{chosen_color}", width, height)
        if cache_key in self.cache:
            return self.cache[cache_key]

        row_col = self.card_map.get(map_key)
        if row_col is None:
            # Fallback to wild blank card if mapping is missing.
            row_col = (0, 11)

        src = self._src_rect(row_col[0], row_col[1])
        image = self.sheet.subsurface(src).copy()
        scaled = pygame.transform.smoothscale(image, (width, height))
        rounded = self._apply_rounded_corners(scaled)
        self.cache[cache_key] = rounded
        return rounded
