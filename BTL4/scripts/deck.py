from typing import List

from scripts.cards import (
    ACTION_COUNTER,
    ACTION_DRAW_67,
    ACTION_DRAW_TWO,
    ACTION_REVERSE,
    ACTION_SILENCE,
    ACTION_SKIP,
    ACTION_WILD,
    ACTION_WILD_DRAW_FOUR,
    COLORS,
    Card,
)


def build_standard_uno_deck() -> List[Card]:
    """Create a standard 108-card UNO deck."""
    deck: List[Card] = []

    for color in COLORS:
        deck.append(Card(color=color, kind="number", number=0))

        for number in range(1, 10):
            deck.append(Card(color=color, kind="number", number=number))
            deck.append(Card(color=color, kind="number", number=number))

        for _ in range(2):
            deck.append(Card(color=color, kind=ACTION_SKIP))
            deck.append(Card(color=color, kind=ACTION_REVERSE))
            deck.append(Card(color=color, kind=ACTION_DRAW_TWO))

    for _ in range(4):
        deck.append(Card(color=None, kind=ACTION_WILD))
        deck.append(Card(color=None, kind=ACTION_WILD_DRAW_FOUR))

    return deck


def build_mixi_extension_pack() -> List[Card]:
    """Returns 12 Mixi pack cards: 4 × Dogs Will Pay, 4 × Faker's Silence, 4 × Mixi Airstrike."""
    cards: List[Card] = []
    for kind in (ACTION_COUNTER, ACTION_SILENCE, ACTION_DRAW_67):
        for _ in range(4):
            cards.append(Card(color=None, kind=kind))
    return cards


def build_deck_for_settings(extension_packs: List[str]) -> List[Card]:
    """Build the full deck, doubling the standard deck and adding Mixi cards when the pack is enabled."""
    if "mixi" in extension_packs:
        return build_standard_uno_deck() + build_standard_uno_deck() + build_mixi_extension_pack()
    return build_standard_uno_deck()
