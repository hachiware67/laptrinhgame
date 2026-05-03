from dataclasses import dataclass
from typing import Optional

COLORS = ["red", "yellow", "green", "blue"]

HAND_COLOR_ORDER = {
    "red": 0,
    "yellow": 1,
    "blue": 2,
    "green": 3,
}

ACTION_SKIP = "skip"
ACTION_REVERSE = "reverse"
ACTION_DRAW_TWO = "draw2"
ACTION_WILD = "wild"
ACTION_WILD_DRAW_FOUR = "wild_draw4"
ACTION_COUNTER = "counter"
ACTION_SILENCE = "silence"
ACTION_DRAW_67 = "draw67"

ACTION_ORDER = {
    ACTION_SKIP: 0,
    ACTION_REVERSE: 1,
    ACTION_DRAW_TWO: 2,
}


@dataclass
class Card:
    color: Optional[str]
    kind: str
    number: Optional[int] = None
    current_pos: tuple[float, float] = (0.0, 0.0)
    target_pos: tuple[float, float] = (0.0, 0.0)
    current_rotation: float = 0.0
    target_rotation: float = 0.0
    current_scale: float = 1.0
    target_scale: float = 1.0
    chosen_color: Optional[str] = None

    @property
    def is_wild(self) -> bool:
        return self.kind in (ACTION_WILD, ACTION_WILD_DRAW_FOUR, ACTION_SILENCE, ACTION_DRAW_67)

    @property
    def short_label(self) -> str:
        if self.kind == "number":
            return str(self.number)
        if self.kind == ACTION_SKIP:
            return "SKIP"
        if self.kind == ACTION_REVERSE:
            return "REV"
        if self.kind == ACTION_DRAW_TWO:
            return "+2"
        if self.kind == ACTION_WILD:
            return "WILD"
        if self.kind == ACTION_COUNTER:
            return "DWP"
        if self.kind == ACTION_SILENCE:
            return "SIL"
        if self.kind == ACTION_DRAW_67:
            return "+67"
        return "+4"


def sort_hand_cards(cards: list[Card]) -> None:
    """Sort a hand so wild cards stay left and colors are grouped consistently."""

    def sort_key(card: Card) -> tuple[int, int, int, int, int]:
        if card.is_wild:
            return (0, 0, 0, 0, 0)

        color_rank = HAND_COLOR_ORDER.get(card.color or "", len(HAND_COLOR_ORDER))
        if card.kind == "number":
            number_rank = card.number if card.number is not None else 99
            return (1, color_rank, 0, number_rank, 0)

        action_rank = ACTION_ORDER.get(card.kind, len(ACTION_ORDER))
        return (1, color_rank, 1, 0, action_rank)

    cards.sort(key=sort_key)
