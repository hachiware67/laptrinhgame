import random
from dataclasses import dataclass, field
from typing import List, Optional, Set, Tuple

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
    COLORS,
    Card,
    sort_hand_cards,
)
from scripts.deck import build_deck_for_settings, build_standard_uno_deck

RULE_ZERO_DIRECTION = "zero_direction"
RULE_SEVEN_TARGET = "seven_target"
RULE_REACTION = "reaction"

PASS_CLOCKWISE = 1
PASS_COUNTER_CLOCKWISE = -1


@dataclass
class PlayerAction:
    player_id: int
    action_type: str  # "play" or "draw"
    card_index: Optional[int] = None
    chosen_color: Optional[str] = None
    chosen_direction: Optional[int] = None
    target_player_id: Optional[int] = None
    timestamp_ms: Optional[int] = None


@dataclass
class ActionResult:
    ok: bool
    message: str
    played_card: Optional[Card] = None
    drew_card: Optional[Card] = None
    uno_call_player: Optional[int] = None
    uno_caught_player: Optional[int] = None
    uno_penalty_cards: List[Card] = field(default_factory=list)


@dataclass
class GameSettings:
    """Configuration for a UNO game."""
    num_players: int = 4
    initial_cards: int = 7
    rule_0_enabled: bool = True
    rule_7_enabled: bool = True
    rule_8_enabled: bool = True
    rule_8_reaction_timer_ms: int = 3000
    two_player_reverse_behavior: str = "reverse"  # "skip" or "reverse"
    extension_packs: List[str] = field(default_factory=list)
    uno_call_player: Optional[int] = None
    uno_caught_player: Optional[int] = None
    uno_penalty_cards: List[Card] = field(default_factory=list)


class UnoGameManager:
    """Host-authoritative game state manager decoupled from input and rendering."""

    def __init__(self, settings: Optional[GameSettings] = None, seed: Optional[int] = None):
        if settings is None:
            settings = GameSettings()

        if not 2 <= settings.num_players <= 4:
            raise ValueError("UNO supports 2 to 4 players in this module.")

        self.settings = settings
        self.rng = random.Random(seed)
        self.num_players = settings.num_players

        self.draw_pile: List[Card] = []
        self.discard_pile: List[Card] = []
        self.player_hands: List[List[Card]] = [[] for _ in range(self.num_players)]

        self.current_player = 0
        self.turn_direction = 1
        self.hand_pass_direction = PASS_CLOCKWISE
        self.current_color: Optional[str] = None
        self.winner: Optional[int] = None

        self.pending_effect: Optional[str] = None
        self.pending_effect_player: Optional[int] = None
        self.pending_reaction_started_at_ms: Optional[int] = None
        self.pending_reaction_due_ms: Optional[int] = None
        self.pending_reaction_players: Set[int] = set()
        self.pending_reaction_times: List[Tuple[int, int]] = []

        self.pending_draw_penalty_count = 0
        self.pending_draw_penalty_kind: Optional[str] = None
        self.pending_draw_penalty_source: Optional[int] = None
        self.pending_draw_decision_player: Optional[int] = None
        self.pending_draw_decision_card: Optional[Card] = None
        self.uno_called_players: Set[int] = set()
        self.silence_remaining: dict[int, int] = {}
        self.flashbang_remaining: dict[int, int] = {}
        self.active_flashbang_player: Optional[int] = None

        self.is_animating = False

        self.start_game()

    @property
    def top_discard(self) -> Card:
        return self.discard_pile[-1]

    def start_game(self) -> None:
        self.draw_pile = build_deck_for_settings(self.settings.extension_packs)
        self.rng.shuffle(self.draw_pile)
        self.discard_pile.clear()

        for hand in self.player_hands:
            hand.clear()

        for _ in range(self.settings.initial_cards):
            for player in range(self.num_players):
                self.player_hands[player].append(self.draw_from_pile())

        for hand in self.player_hands:
            sort_hand_cards(hand)

        while True:
            card = self.draw_from_pile()
            if card.is_wild or card.is_none_type:
                self.draw_pile.insert(0, card)
                self.rng.shuffle(self.draw_pile)
                continue
            self.discard_pile.append(card)
            self.current_color = card.color
            break

        self.current_player = 0
        self.turn_direction = 1
        self.hand_pass_direction = PASS_CLOCKWISE
        self.winner = None
        self.pending_effect = None
        self.pending_effect_player = None
        self.pending_reaction_started_at_ms = None
        self.pending_reaction_due_ms = None
        self.pending_reaction_players.clear()
        self.pending_reaction_times.clear()
        self.pending_draw_penalty_count = 0
        self.pending_draw_penalty_kind = None
        self.pending_draw_penalty_source = None
        self.pending_draw_decision_player = None
        self.pending_draw_decision_card = None
        self.uno_called_players.clear()
        self.silence_remaining.clear()
        self.flashbang_remaining.clear()
        self.active_flashbang_player = None

    def draw_from_pile(self) -> Card:
        self.rebuild_draw_pile_if_needed()
        card = self.draw_pile.pop()
        card.chosen_color = None
        return card

    def tick(self, now_ms: int) -> Optional[str]:
        if self.pending_effect == RULE_REACTION and self.pending_reaction_due_ms is not None and now_ms >= self.pending_reaction_due_ms:
            self._resolve_reaction_event()
            return "Rule of 8 resolved."

        return None

    def is_player_flashbanged(self, player_id: int) -> bool:
        return self.active_flashbang_player == player_id or self.flashbang_remaining.get(player_id, 0) > 0

    def rebuild_draw_pile_if_needed(self) -> None:
        if self.draw_pile:
            return

        if len(self.discard_pile) > 1:
            top = self.discard_pile[-1]
            to_shuffle = self.discard_pile[:-1]
            self.discard_pile = [top]
            self.rng.shuffle(to_shuffle)
            self.draw_pile = to_shuffle
            return

        # Both piles exhausted (can happen after Mixi Airstrike) — inject an emergency deck.
        emergency = build_standard_uno_deck()
        self.rng.shuffle(emergency)
        self.draw_pile = emergency

    def get_legal_card_indices(self, player_id: int) -> List[int]:
        if self.pending_effect is not None:
            return []

        legal = []
        hand = self.player_hands[player_id]
        has_single_card = len(hand) == 1
        for i, card in enumerate(hand):
            if has_single_card and self._is_forbidden_last_card(card):
                continue
            if self.is_legal_play(card):
                legal.append(i)
        return legal

    def is_legal_play(self, candidate: Card) -> bool:
        if self.pending_effect is not None:
            return False

        # Counter card can ONLY be played after a +2 or +4, everything else is invalid
        if candidate.kind == ACTION_COUNTER:
            return self.pending_draw_penalty_count > 0 and self.pending_draw_penalty_kind in (ACTION_DRAW_TWO, ACTION_WILD_DRAW_FOUR)

        top = self.top_discard

        if self.pending_draw_penalty_count > 0:
            return self.can_stack_draw_penalty(candidate)

        if candidate.is_wild or candidate.is_none_type:
            return True
        if candidate.color == self.current_color:
            return True
        if candidate.kind == "number" and top.kind == "number":
            return candidate.number == top.number
        if candidate.kind != "number" and top.kind != "number":
            return candidate.kind == top.kind

        return False

    def submit_action(self, action: PlayerAction) -> ActionResult:
        if self.winner is not None:
            return ActionResult(False, "Game is already over.")

        if self.pending_effect == RULE_REACTION:
            if action.action_type == "react":
                return self._handle_reaction(action)
            return ActionResult(False, "Reaction window is active.")

        if self.pending_effect == RULE_ZERO_DIRECTION:
            if action.action_type == "choose_zero_direction":
                return self._resolve_zero_direction(action)
            return ActionResult(False, "Choose the hand pass direction first.")

        if self.pending_effect == RULE_SEVEN_TARGET:
            if action.action_type == "choose_seven_target":
                return self._resolve_seven_target(action)
            return ActionResult(False, "Choose a swap target first.")

        if self.pending_draw_decision_card is not None:
            return ActionResult(False, "Choose whether to play or keep the drawn card.")

        if action.action_type == "uno":
            return self.call_uno(action.player_id)

        if action.action_type == "sort_hand":
            return self.sort_player_hand(action.player_id)

        if action.player_id != self.current_player:
            return ActionResult(False, "Not this player's turn.")

        if action.action_type == "play":
            return self._handle_play(action)
        if action.action_type == "draw":
            return self._handle_draw(action.player_id)

        return ActionResult(False, "Unknown action.")

    def _handle_play(self, action: PlayerAction) -> ActionResult:
        self.is_animating = True
        hand = self.player_hands[action.player_id]
        if action.card_index is None or not (0 <= action.card_index < len(hand)):
            return ActionResult(False, "Invalid card index.")

        card = hand[action.card_index]
        if not self.is_legal_play(card):
            return ActionResult(False, "Illegal card for current top card/color.")

        if len(hand) == 1 and self._is_forbidden_last_card(card):
            return ActionResult(False, "You cannot win with that action card.")

        chosen_color = action.chosen_color
        if card.is_wild:
            if chosen_color not in COLORS:
                return ActionResult(False, "Wild cards require a chosen color.")
        else:
            chosen_color = card.color

        hand.pop(action.card_index)
        return self._finish_played_card(action.player_id, card, chosen_color, action.timestamp_ms)

    def _finish_played_card(
        self,
        player_id: int,
        card: Card,
        chosen_color: Optional[str],
        timestamp_ms: Optional[int] = None,
    ) -> ActionResult:
        hand = self.player_hands[player_id]
        card.chosen_color = chosen_color if card.is_wild else None
        self.discard_pile.append(card)
        if not card.is_none_type:
            self.current_color = chosen_color if chosen_color is not None else card.color

        if card.kind == "number" and card.number == 0 and self.settings.rule_0_enabled:
            self.pending_effect = RULE_ZERO_DIRECTION
            self.pending_effect_player = player_id
            return self._apply_uno_check(
                player_id,
                ActionResult(True, "Rule of 0: choose hand pass direction.", played_card=card),
            )

        if card.kind == "number" and card.number == 7 and self.settings.rule_7_enabled:
            self.pending_effect = RULE_SEVEN_TARGET
            self.pending_effect_player = player_id
            return self._apply_uno_check(
                player_id,
                ActionResult(True, "Rule of 7: choose a target player to swap hands with.", played_card=card),
            )

        if card.kind == "number" and card.number == 8 and self.settings.rule_8_enabled:
            self.pending_effect = RULE_REACTION
            self.pending_effect_player = player_id
            started_at = timestamp_ms or 0
            self.pending_reaction_started_at_ms = started_at
            self.pending_reaction_due_ms = started_at + self.settings.rule_8_reaction_timer_ms
            self.pending_reaction_players = set()
            self.pending_reaction_times = []
            return self._apply_uno_check(
                player_id,
                ActionResult(True, "Rule of 8: reaction event started.", played_card=card),
            )

        if len(hand) == 0:
            self.winner = player_id
            return ActionResult(True, f"Player {player_id + 1} wins!", played_card=card)

        self._apply_played_card_effect(card)

        return self._apply_uno_check(player_id, ActionResult(True, "Card played.", played_card=card))

    def can_call_uno(self, player_id: int) -> bool:
        if not (0 <= player_id < self.num_players):
            return False
        hand = self.player_hands[player_id]
        if len(hand) != 2:
            return False
        return any(self.is_legal_play(card) for card in hand)

    def call_uno(self, player_id: int) -> ActionResult:
        if not (0 <= player_id < self.num_players):
            return ActionResult(False, "Invalid player.")

        if player_id != self.current_player:
            return ActionResult(False, "Not this player's turn.")

        hand_size = len(self.player_hands[player_id])
        if hand_size != 2:
            return ActionResult(False, "UNO can be called when you have exactly two cards.")
        if not self.can_call_uno(player_id):
            return ActionResult(False, "UNO can be called only when you have a legal play.")

        self.uno_called_players.add(player_id)
        return ActionResult(True, f"Player {player_id + 1} called UNO.", uno_call_player=player_id)

    def _apply_uno_check(self, player_id: int, result: ActionResult) -> ActionResult:
        if not result.ok:
            return result

        hand_size = len(self.player_hands[player_id])
        if hand_size != 1:
            self.uno_called_players.discard(player_id)
            return result

        if player_id in self.uno_called_players:
            if "called UNO" not in result.message:
                result.message = f"{result.message} Player {player_id + 1} called UNO."
            return result

        penalty_cards = [self.draw_from_pile() for _ in range(2)]
        self.player_hands[player_id].extend(penalty_cards)
        sort_hand_cards(self.player_hands[player_id])
        self.uno_called_players.discard(player_id)
        result.message = f"UNO was not called. Player {player_id + 1} drew 2 cards."
        result.uno_caught_player = player_id
        result.uno_penalty_cards = penalty_cards
        return result

    def draw_for_decision(self, player_id: int) -> ActionResult:
        if self.winner is not None:
            return ActionResult(False, "Game is already over.")
        if self.pending_effect is not None:
            return ActionResult(False, "Resolve the pending effect first.")
        if self.pending_draw_decision_card is not None:
            return ActionResult(False, "Choose whether to play or keep the drawn card.")
        if player_id != self.current_player:
            return ActionResult(False, "Not this player's turn.")

        if self.pending_draw_penalty_count > 0:
            self.is_animating = True
            result = self._draw_pending_penalty(player_id)
            self._sync_uno_calls()
            return result

        legal_before_draw = [] if self.is_player_flashbanged(player_id) else self.get_legal_card_indices(player_id)
        if legal_before_draw:
            return ActionResult(False, "You can play a card; draw only when you have no legal move.")

        drawn = self.draw_from_pile()
        if self.is_legal_play(drawn):
            self.pending_draw_decision_player = player_id
            self.pending_draw_decision_card = drawn
            return ActionResult(True, "Choose whether to play or keep the drawn card.", drew_card=drawn)

        self.player_hands[player_id].append(drawn)
        sort_hand_cards(self.player_hands[player_id])
        self._advance_turn(1)
        self.is_animating = True
        self._sync_uno_calls()
        return ActionResult(True, "Drew one card and ended turn.", drew_card=drawn)

    def keep_pending_draw_decision(self, player_id: int) -> ActionResult:
        card = self.pending_draw_decision_card
        if card is None or self.pending_draw_decision_player != player_id:
            return ActionResult(False, "No drawn card is waiting for that player.")
        if player_id != self.current_player:
            return ActionResult(False, "Not this player's turn.")

        self.pending_draw_decision_player = None
        self.pending_draw_decision_card = None
        self.player_hands[player_id].append(card)
        sort_hand_cards(self.player_hands[player_id])
        self._advance_turn(1)
        self.is_animating = True
        self._sync_uno_calls()
        return ActionResult(True, "Kept the drawn card and ended turn.", drew_card=card)

    def play_pending_draw_decision(
        self,
        player_id: int,
        chosen_color: Optional[str] = None,
        timestamp_ms: Optional[int] = None,
    ) -> ActionResult:
        card = self.pending_draw_decision_card
        if card is None or self.pending_draw_decision_player != player_id:
            return ActionResult(False, "No drawn card is waiting for that player.")
        if player_id != self.current_player:
            return ActionResult(False, "Not this player's turn.")
        if not self.is_legal_play(card):
            return ActionResult(False, "The drawn card is no longer legal to play.")
        if len(self.player_hands[player_id]) == 0 and self._is_forbidden_last_card(card):
            return ActionResult(False, "You cannot win with that action card.")

        if card.is_wild:
            if chosen_color not in COLORS:
                return ActionResult(False, "Wild cards require a chosen color.")
        else:
            chosen_color = card.color

        self.pending_draw_decision_player = None
        self.pending_draw_decision_card = None
        self.is_animating = True
        result = self._finish_played_card(player_id, card, chosen_color, timestamp_ms)
        result.drew_card = card
        return result

    def _handle_draw(self, player_id: int) -> ActionResult:
        self.is_animating = True
        if self.pending_draw_penalty_count > 0:
            result = self._draw_pending_penalty(player_id)
            self._sync_uno_calls()
            return result

        legal_before_draw = [] if self.is_player_flashbanged(player_id) else self.get_legal_card_indices(player_id)
        if legal_before_draw:
            return ActionResult(False, "You can play a card; draw only when you have no legal move.")

        drawn = self.draw_from_pile()
        self.player_hands[player_id].append(drawn)

        if self.is_legal_play(drawn):
            self.player_hands[player_id].pop()
            chosen_color = drawn.color if not drawn.is_wild else self.choose_color_for_player(player_id)
            result = self._finish_played_card(player_id, drawn, chosen_color)
            result.drew_card = drawn
            if result.ok and result.message == "Card played.":
                result.message = "Drew and auto-played a card."
            return result

        self._advance_turn(1)
        self._sync_uno_calls()
        return ActionResult(True, "Drew one card and ended turn.", drew_card=drawn)

    def sort_player_hand(self, player_id: int) -> ActionResult:
        if self.winner is not None:
            return ActionResult(False, "Game is already over.")
        if player_id != self.current_player:
            return ActionResult(False, "Not this player's turn.")

        sort_hand_cards(self.player_hands[player_id])
        return ActionResult(True, "Hand sorted.")

    def _apply_played_card_effect(self, card: Card) -> None:
        if card.kind == ACTION_SKIP:
            self._advance_turn(2)
            return

        if card.kind == ACTION_REVERSE:
            if self.num_players == 2 and self.settings.two_player_reverse_behavior == "skip":
                self._advance_turn(2)
                return
            self.turn_direction *= -1
            self._advance_turn(1)
            return

        if card.kind == ACTION_DRAW_TWO:
            self._start_or_stack_draw_penalty(ACTION_DRAW_TWO)
            self._advance_turn(1)
            return

        if card.kind == ACTION_WILD_DRAW_FOUR:
            self._start_or_stack_draw_penalty(ACTION_WILD_DRAW_FOUR)
            self._advance_turn(1)
            return

        if card.kind == ACTION_COUNTER:
            source = self.pending_draw_penalty_source
            count = self.pending_draw_penalty_count
            self.pending_draw_penalty_count = 0
            self.pending_draw_penalty_kind = None
            self.pending_draw_penalty_source = None
            if source is not None:
                for _ in range(count):
                    self.player_hands[source].append(self.draw_from_pile())
                sort_hand_cards(self.player_hands[source])
            self._advance_turn(1)
            return

        if card.kind == ACTION_SILENCE:
            victim = self._next_player_index(1)
            self.silence_remaining[victim] = 3
            self._advance_turn(1)
            return

        if card.kind == ACTION_DRAW_67:
            self._start_or_stack_draw_penalty(ACTION_DRAW_67)
            self._advance_turn(1)
            return

        if card.kind == ACTION_MOM_MAY_CRY:
            self._reduce_hand_to_seven(self.current_player)
            self._advance_turn(1)
            return

        if card.kind == ACTION_FLASHBANG:
            self._apply_flashbang(self.current_player)
            self._advance_turn(1)
            return

        self._advance_turn(1)

    def _reduce_hand_to_seven(self, player_id: int) -> None:
        hand = self.player_hands[player_id]
        if len(hand) <= 7:
            return

        keep_indices = set(self.rng.sample(range(len(hand)), 7))
        kept_cards = [card for index, card in enumerate(hand) if index in keep_indices]
        returned_cards = [card for index, card in enumerate(hand) if index not in keep_indices]
        self.player_hands[player_id] = kept_cards
        sort_hand_cards(self.player_hands[player_id])
        self.draw_pile.extend(returned_cards)
        self.rng.shuffle(self.draw_pile)

    def _apply_flashbang(self, player_id: int) -> None:
        self.flashbang_remaining[player_id] = self.flashbang_remaining.get(player_id, 0) + 1
        for other_player in range(self.num_players):
            if other_player == player_id:
                continue
            self.flashbang_remaining[other_player] = self.flashbang_remaining.get(other_player, 0) + 2

    def _start_or_stack_draw_penalty(self, kind: str) -> None:
        if kind == ACTION_DRAW_TWO:
            amount = 2
        elif kind == ACTION_DRAW_67:
            amount = 67
        else:
            amount = 4  # ACTION_WILD_DRAW_FOUR

        is_new = self.pending_draw_penalty_count == 0
        self.pending_draw_penalty_count += amount
        if is_new:
            self.pending_draw_penalty_source = self.current_player
            self.pending_draw_penalty_kind = kind
        elif kind == ACTION_WILD_DRAW_FOUR:
            self.pending_draw_penalty_kind = kind

    def _draw_pending_penalty(self, player_id: int) -> ActionResult:
        for _ in range(self.pending_draw_penalty_count):
            self.player_hands[player_id].append(self.draw_from_pile())

        drawn_count = self.pending_draw_penalty_count
        self.pending_draw_penalty_count = 0
        self.pending_draw_penalty_kind = None
        self.pending_draw_penalty_source = None
        self._advance_turn(1)
        self._sync_uno_calls()
        return ActionResult(True, f"Player {player_id + 1} drew {drawn_count} cards and lost the turn.")

    def _resolve_zero_direction(self, action: PlayerAction) -> ActionResult:
        effect_player = self.pending_effect_player
        if action.player_id != self.pending_effect_player:
            return ActionResult(False, "Only the player who played the 0 chooses the direction.")

        if action.chosen_direction not in (PASS_CLOCKWISE, PASS_COUNTER_CLOCKWISE):
            return ActionResult(False, "Choose clockwise or counter-clockwise.")

        self.hand_pass_direction = action.chosen_direction
        self._pass_all_hands(self.hand_pass_direction)
        self.pending_effect = None
        self.pending_effect_player = None
        self._sync_uno_calls()

        if effect_player is not None and len(self.player_hands[effect_player]) == 0:
            self.winner = effect_player

        self._advance_turn(1)
        return ActionResult(True, "Rule of 0 resolved: hands were passed.")

    def _resolve_seven_target(self, action: PlayerAction) -> ActionResult:
        effect_player = self.pending_effect_player
        if action.player_id != self.pending_effect_player:
            return ActionResult(False, "Only the player who played the 7 chooses the target.")

        if action.target_player_id is None or not (0 <= action.target_player_id < self.num_players):
            return ActionResult(False, "Choose a valid target player.")

        if action.target_player_id == action.player_id:
            return ActionResult(False, "You must choose another player.")

        self._swap_hands(action.player_id, action.target_player_id)
        self.pending_effect = None
        self.pending_effect_player = None
        self._sync_uno_calls()

        if effect_player is not None and len(self.player_hands[effect_player]) == 0:
            self.winner = effect_player

        self._advance_turn(1)
        return ActionResult(True, f"Rule of 7 resolved: Player {action.player_id + 1} swapped hands with Player {action.target_player_id + 1}.")

    def _handle_reaction(self, action: PlayerAction) -> ActionResult:
        if action.player_id in self.pending_reaction_players:
            return ActionResult(False, "That player already reacted.")

        self.pending_reaction_players.add(action.player_id)
        self.pending_reaction_times.append((action.player_id, action.timestamp_ms or 0))
        return ActionResult(True, f"Player {action.player_id + 1} reacted.")

    def _resolve_reaction_event(self) -> None:
        effect_player = self.pending_effect_player
        responders = {player_id for player_id, _ in self.pending_reaction_times}
        if len(responders) == self.num_players:
            punish_targets = [self.pending_reaction_times[-1][0]]
        else:
            punish_targets = [player_id for player_id in range(self.num_players) if player_id not in responders]

        for target in punish_targets:
            for _ in range(2):
                self.player_hands[target].append(self.draw_from_pile())
            sort_hand_cards(self.player_hands[target])
        self._sync_uno_calls()

        self.pending_effect = None
        self.pending_effect_player = None
        self.pending_reaction_started_at_ms = None
        self.pending_reaction_due_ms = None
        self.pending_reaction_players.clear()
        self.pending_reaction_times.clear()

        if effect_player is not None and len(self.player_hands[effect_player]) == 0:
            self.winner = effect_player

        self._advance_turn(1)

    def _next_player_index(self, steps: int) -> int:
        return (self.current_player + steps * self.turn_direction) % self.num_players

    def _advance_turn(self, steps: int) -> None:
        if self.active_flashbang_player == self.current_player:
            remaining = self.flashbang_remaining.get(self.current_player, 0) - 1
            if remaining > 0:
                self.flashbang_remaining[self.current_player] = remaining
            else:
                self.flashbang_remaining.pop(self.current_player, None)
            self.active_flashbang_player = None

        self.current_player = self._next_player_index(steps)
        for _ in range(self.num_players):
            # Pending draw penalties must be resolved by the current player, even if silenced.
            if self.pending_draw_penalty_count > 0:
                break
            if self.silence_remaining.get(self.current_player, 0) > 0:
                self.silence_remaining[self.current_player] -= 1
                if self.silence_remaining[self.current_player] == 0:
                    del self.silence_remaining[self.current_player]
                self.current_player = self._next_player_index(1)
            else:
                break
        if self.flashbang_remaining.get(self.current_player, 0) > 0:
            self.active_flashbang_player = self.current_player
        else:
            self.active_flashbang_player = None

    def _pass_all_hands(self, direction: int) -> None:
        new_hands: List[List[Card]] = [[] for _ in range(self.num_players)]
        for player_id, hand in enumerate(self.player_hands):
            target = (player_id + direction) % self.num_players
            new_hands[target] = hand
        self.player_hands = new_hands

    def _swap_hands(self, first_player: int, second_player: int) -> None:
        self.player_hands[first_player], self.player_hands[second_player] = (
            self.player_hands[second_player],
            self.player_hands[first_player],
        )

    def _is_forbidden_last_card(self, card: Card) -> bool:
        return card.kind in (
            ACTION_SKIP, ACTION_REVERSE, ACTION_DRAW_TWO, ACTION_WILD, ACTION_WILD_DRAW_FOUR,
            ACTION_COUNTER, ACTION_SILENCE, ACTION_DRAW_67, ACTION_FLASHBANG, ACTION_MOM_MAY_CRY,
        )

    def _sync_uno_calls(self) -> None:
        self.uno_called_players = {
            player_id
            for player_id in self.uno_called_players
            if 0 <= player_id < self.num_players and len(self.player_hands[player_id]) == 1
        }

    def choose_color_for_player(self, player_id: int) -> str:
        color_counts = {c: 0 for c in COLORS}
        for card in self.player_hands[player_id]:
            if card.color in color_counts:
                color_counts[card.color] += 1
        return max(color_counts, key=color_counts.get)

    def can_stack_draw_penalty(self, card: Card) -> bool:
        if self.pending_draw_penalty_count <= 0:
            return False
        if self.pending_draw_penalty_kind == ACTION_DRAW_67:
            return card.kind == ACTION_DRAW_67
        if self.pending_draw_penalty_kind == ACTION_WILD_DRAW_FOUR:
            return card.kind == ACTION_WILD_DRAW_FOUR
        return card.kind in (ACTION_DRAW_TWO, ACTION_WILD_DRAW_FOUR)

    def is_waiting_for_input(self) -> bool:
        return (
            self.pending_effect in {RULE_ZERO_DIRECTION, RULE_SEVEN_TARGET, RULE_REACTION}
            or self.pending_draw_decision_card is not None
        )

    def get_reaction_remaining_ms(self, now_ms: int) -> int:
        if self.pending_effect != RULE_REACTION or self.pending_reaction_due_ms is None:
            return 0
        return max(0, self.pending_reaction_due_ms - now_ms)

    def get_active_effect_label(self, now_ms: int) -> Optional[str]:
        if self.pending_effect == RULE_ZERO_DIRECTION:
            return "Rule of 0: choose hand pass direction"
        if self.pending_effect == RULE_SEVEN_TARGET:
            return "Rule of 7: choose a target player to swap hands with"
        if self.pending_effect == RULE_REACTION:
            return f"Rule of 8: reaction window {self.get_reaction_remaining_ms(now_ms) / 1000:.1f}s"
        if self.pending_draw_penalty_count > 0:
            if self.pending_draw_penalty_kind == ACTION_WILD_DRAW_FOUR:
                kind_str = "+4"
            elif self.pending_draw_penalty_kind == ACTION_DRAW_67:
                kind_str = "+67"
            else:
                kind_str = "+2"
            return f"Draw penalty pending: {self.pending_draw_penalty_count} cards ({kind_str} stack)"
        return None
