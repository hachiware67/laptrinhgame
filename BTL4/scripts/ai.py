import random
from dataclasses import dataclass
from typing import Optional

from scripts.cards import ACTION_COUNTER, Card
from scripts.game_manager import (
    ActionResult,
    PASS_CLOCKWISE,
    PASS_COUNTER_CLOCKWISE,
    PlayerAction,
    RULE_REACTION,
    RULE_SEVEN_TARGET,
    RULE_ZERO_DIRECTION,
    UnoGameManager,
)

ai_rng = random.Random()


@dataclass
class AITurnOutcome:
    message: str
    player_id: int
    action_type: str
    card: Optional[Card] = None
    result: Optional[ActionResult] = None


def get_best_card_index(game: UnoGameManager, player_id: int) -> int:
    """
    Select the best card to play using priority-based logic:
    1. Lowest number card matching current color
    2. Same number, different color
    3. Wild or Wild Draw Four
    Returns the index of the best legal card, or -1 if no legal moves.
    """
    pid = player_id
    hand = game.player_hands[pid]
    legal_indices = game.get_legal_card_indices(pid)

    if not legal_indices:
        return -1

    # Get the top discard card to match against
    top_card = game.top_discard
    best_idx = legal_indices[0]

    # Priority 0: Always deflect an incoming draw penalty with Counter
    if game.pending_draw_penalty_count > 0:
        counter_matches = [idx for idx in legal_indices if hand[idx].kind == ACTION_COUNTER]
        if counter_matches:
            return counter_matches[0]

    # Priority 1: Lowest number card matching current color
    color_matches = [idx for idx in legal_indices if hand[idx].color == top_card.color and hand[idx].kind == "number"]
    if color_matches:
        best_idx = min(color_matches, key=lambda idx: hand[idx].number)
        return best_idx

    # Priority 2: Same number, different color
    number_matches = [idx for idx in legal_indices if hand[idx].number == top_card.number and hand[idx].kind == "number"]
    if number_matches:
        best_idx = min(number_matches, key=lambda idx: hand[idx].number)
        return best_idx

    # Priority 3: Wild or Wild Draw Four
    wild_matches = [idx for idx in legal_indices if hand[idx].is_wild]
    if wild_matches:
        return wild_matches[0]

    # Fallback to first legal card
    return legal_indices[0]


def perform_ai_pending_effect(game: UnoGameManager) -> str:
    """Handle AI auto-response to Rule of 0 (direction choice) and Rule of 7 (target selection)."""
    if game.pending_effect == RULE_ZERO_DIRECTION and game.current_player != 0:
        direction = ai_rng.choice([PASS_CLOCKWISE, PASS_COUNTER_CLOCKWISE])
        result = game.submit_action(
            PlayerAction(
                player_id=game.current_player,
                action_type="choose_zero_direction",
                chosen_direction=direction,
            )
        )
        return result.message

    if game.pending_effect == RULE_SEVEN_TARGET and game.current_player != 0:
        targets = [pid for pid in range(game.num_players) if pid != game.current_player]
        if targets:
            target = ai_rng.choice(targets)
            result = game.submit_action(
                PlayerAction(
                    player_id=game.current_player,
                    action_type="choose_seven_target",
                    target_player_id=target,
                )
            )
            return result.message

    return ""


def perform_simple_ai_turn(game: UnoGameManager, now_ms: Optional[int] = None) -> AITurnOutcome:
    pid = game.current_player
    best_idx = get_best_card_index(game, pid)

    if best_idx >= 0:
        card = game.player_hands[pid][best_idx]
        chosen_color = game.choose_color_for_player(pid) if card.is_wild else None
        if len(game.player_hands[pid]) == 2:
            game.call_uno(pid)
        result = game.submit_action(
            PlayerAction(
                player_id=pid,
                action_type="play",
                card_index=best_idx,
                chosen_color=chosen_color,
                timestamp_ms=now_ms,
            )
        )
        if result.ok:
            return AITurnOutcome(result.message, pid, "play", card=card, result=result)
        return AITurnOutcome(result.message, pid, "error", result=result)

    result = game.submit_action(PlayerAction(player_id=pid, action_type="draw", timestamp_ms=now_ms))
    if result.ok:
        action_type = "draw_played" if result.played_card is not None and result.drew_card is not None else "draw"
        card = result.played_card if action_type == "draw_played" else result.drew_card
        return AITurnOutcome(result.message, pid, action_type, card=card, result=result)
    return AITurnOutcome(result.message, pid, "error", result=result)
