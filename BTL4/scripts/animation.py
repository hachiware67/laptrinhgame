from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypeVar

import pygame

from scripts.cards import Card

T = TypeVar("T", int, float)


def lerp(start: T, end: T, amount: float) -> float:
    amount = max(0.0, min(1.0, amount))
    return float(start) + (float(end) - float(start)) * amount


def lerp_point(
    start: tuple[float, float],
    end: tuple[float, float],
    amount: float,
) -> tuple[float, float]:
    return (lerp(start[0], end[0], amount), lerp(start[1], end[1], amount))


def smooth_factor(dt: float, speed: float) -> float:
    if dt <= 0.0:
        return 0.0
    if speed <= 0.0:
        return 1.0
    return max(0.0, min(1.0, dt * speed))


def transform_card_surface(surface: pygame.Surface, rotation: float, scale: float) -> pygame.Surface:
    if scale != 1.0 or rotation != 0.0:
        return pygame.transform.rotozoom(surface, -rotation, scale)
    return surface


@dataclass
class ActiveCard:
    card: Card
    owner_id: int
    kind: str
    current_pos: tuple[float, float]
    target_pos: tuple[float, float]
    current_rotation: float
    target_rotation: float
    current_scale: float = 1.0
    target_scale: float = 1.0
    travel_speed: float = 7.5
    rotation_speed: float = 7.5
    scale_speed: float = 8.5
    reveal_hand_card: bool = False
    start_pos: tuple[float, float] | None = None
    start_rotation: float | None = None
    start_scale: float | None = None
    duration: float = 0.24
    elapsed: float = 0.0

    def __post_init__(self) -> None:
        if self.start_pos is None:
            self.start_pos = self.current_pos
        if self.start_rotation is None:
            self.start_rotation = self.current_rotation
        if self.start_scale is None:
            self.start_scale = self.current_scale

    @property
    def progress(self) -> float:
        if self.duration <= 0.0:
            return 1.0
        return max(0.0, min(1.0, self.elapsed / self.duration))

    @property
    def flip_progress(self) -> float:
        if self.kind != "play_flip":
            return self.progress
        if self.progress <= 0.72:
            return 0.0
        return max(0.0, min(1.0, (self.progress - 0.72) / 0.28))

    def update(self, dt: float) -> bool:
        if self.kind in ("play", "draw", "play_flip"):
            self.elapsed += max(0.0, dt)
            t = self.progress
            move_phase = t if self.kind != "play_flip" else min(1.0, t / 0.72)
            # Ease out quickly so played cards feel snappier and more responsive.
            move_t = 1.0 - pow(1.0 - move_phase, 3)

            assert self.start_pos is not None
            assert self.start_rotation is not None
            assert self.start_scale is not None
            self.current_pos = lerp_point(self.start_pos, self.target_pos, move_t)
            self.current_rotation = lerp(self.start_rotation, self.target_rotation, move_t)

            if self.kind in ("play", "play_flip"):
                pop = 0.16 * math.sin(math.pi * t)
                self.current_scale = lerp(self.start_scale, self.target_scale, move_t) + pop
            else:
                self.current_scale = lerp(self.start_scale, self.target_scale, move_t)

            return t >= 1.0

        travel = smooth_factor(dt, self.travel_speed)
        rotate = smooth_factor(dt, self.rotation_speed)
        scale = smooth_factor(dt, self.scale_speed)

        self.current_pos = lerp_point(self.current_pos, self.target_pos, travel)
        self.current_rotation = lerp(self.current_rotation, self.target_rotation, rotate)
        self.current_scale = lerp(self.current_scale, self.target_scale, scale)

        reached_x = abs(self.current_pos[0] - self.target_pos[0]) < 1.0
        reached_y = abs(self.current_pos[1] - self.target_pos[1]) < 1.0
        reached_rot = abs(self.current_rotation - self.target_rotation) < 0.5
        reached_scale = abs(self.current_scale - self.target_scale) < 0.01
        return reached_x and reached_y and reached_rot and reached_scale
