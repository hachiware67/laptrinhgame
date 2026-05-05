from dataclasses import dataclass

import pygame

from scripts.assets import asset_path
from scripts.screens import AudioSettings, TitleScreen
from scripts.sprites import CardSpriteAtlas

DEFAULT_SCREEN_SIZE = (1920, 1080)
MIN_SCREEN_SIZE = (1100, 720)
DISPLAY_MODE_WINDOWED = "windowed"
DISPLAY_MODE_FULLSCREEN = "fullscreen"
WINDOWED_SCREEN_MARGIN = (80, 80)


def fit_window_size_to_desktop(
    desktop_size: tuple[int, int],
    preferred_size: tuple[int, int] = DEFAULT_SCREEN_SIZE,
) -> tuple[int, int]:
    desktop_w = max(1, int(desktop_size[0]))
    desktop_h = max(1, int(desktop_size[1]))
    preferred_w = max(1, int(preferred_size[0]))
    preferred_h = max(1, int(preferred_size[1]))

    if preferred_w <= desktop_w - WINDOWED_SCREEN_MARGIN[0] and preferred_h <= desktop_h - WINDOWED_SCREEN_MARGIN[1]:
        return (preferred_w, preferred_h)

    available_w = max(1, desktop_w - WINDOWED_SCREEN_MARGIN[0])
    available_h = max(1, desktop_h - WINDOWED_SCREEN_MARGIN[1])
    preferred_ratio = preferred_w / preferred_h

    fitted_w = min(preferred_w, available_w)
    fitted_h = int(round(fitted_w / preferred_ratio))
    if fitted_h > available_h:
        fitted_h = min(preferred_h, available_h)
        fitted_w = int(round(fitted_h * preferred_ratio))

    return (min(fitted_w, desktop_w), min(fitted_h, desktop_h))


def _clamp_screen_size(
    size: tuple[int, int],
    desktop_size: tuple[int, int] | None = None,
) -> tuple[int, int]:
    min_w, min_h = MIN_SCREEN_SIZE
    width = max(min_w, int(size[0]))
    height = max(min_h, int(size[1]))

    if desktop_size is None:
        return (width, height)

    desktop_w = max(1, int(desktop_size[0]))
    desktop_h = max(1, int(desktop_size[1]))
    if width <= desktop_w - WINDOWED_SCREEN_MARGIN[0] and height <= desktop_h - WINDOWED_SCREEN_MARGIN[1]:
        return (width, height)

    return fit_window_size_to_desktop(desktop_size, preferred_size=(width, height))


def _get_desktop_size() -> tuple[int, int]:
    info = pygame.display.Info()
    return (int(info.current_w), int(info.current_h))


@dataclass
class DisplayModeState:
    windowed_size: tuple[int, int] = DEFAULT_SCREEN_SIZE
    desktop_size: tuple[int, int] | None = None
    is_fullscreen: bool = False

    @property
    def mode(self) -> str:
        return DISPLAY_MODE_FULLSCREEN if self.is_fullscreen else DISPLAY_MODE_WINDOWED

    def remember_windowed_size(self, size: tuple[int, int]) -> None:
        self.windowed_size = _clamp_screen_size(size, self.desktop_size)

    def remember_os_windowed_size(self, size: tuple[int, int]) -> None:
        self.windowed_size = (max(1, int(size[0])), max(1, int(size[1])))

    def refresh_desktop_size(self) -> None:
        self.desktop_size = _get_desktop_size()
        self.windowed_size = _clamp_screen_size(self.windowed_size, self.desktop_size)


def _apply_display_mode(
    requested_mode: str,
    screen: pygame.Surface,
    display_state: DisplayModeState,
) -> pygame.Surface:
    if requested_mode == display_state.mode:
        return screen

    if requested_mode == DISPLAY_MODE_FULLSCREEN:
        display_state.remember_windowed_size(screen.get_size())
        display_state.is_fullscreen = True
        return pygame.display.set_mode((0, 0), pygame.FULLSCREEN)

    if requested_mode == DISPLAY_MODE_WINDOWED:
        display_state.refresh_desktop_size()
        display_state.is_fullscreen = False
        return pygame.display.set_mode(display_state.windowed_size, pygame.RESIZABLE)

    return screen


def main() -> None:
    pygame.init()
    try:
        pygame.mixer.init()
        mixer_ready = True
    except pygame.error:
        mixer_ready = False
    pygame.display.set_caption("UNO tay`")
    desktop_size = _get_desktop_size()
    display_state = DisplayModeState(
        windowed_size=fit_window_size_to_desktop(desktop_size),
        desktop_size=desktop_size,
    )
    screen = pygame.display.set_mode(display_state.windowed_size, pygame.RESIZABLE)
    clock = pygame.time.Clock()

    atlas_path = asset_path("sprites", "PC _ Computer - UNO - Cards - Cards (Classic).png")
    atlas = CardSpriteAtlas(atlas_path)
    audio_settings = AudioSettings()
    current_bgm_track = None

    current_screen = TitleScreen(atlas, audio_settings)
    bgm_playing = False
    last_music_mix: float | None = None

    running = True
    while running:
        now = pygame.time.get_ticks()
        if mixer_ready:
            current_mix = audio_settings.music_mix()
            if last_music_mix is None or abs(current_mix - last_music_mix) >= 0.01:
                pygame.mixer.music.set_volume(current_mix)
                last_music_mix = current_mix

        desired_track = None
        if mixer_ready and current_screen.wants_bgm:
            desired_track = current_screen.bgm_track
            if desired_track is not None and not desired_track.exists():
                desired_track = None

        if desired_track != current_bgm_track:
            if bgm_playing:
                pygame.mixer.music.stop()
                bgm_playing = False
            current_bgm_track = None
            if desired_track is not None:
                try:
                    pygame.mixer.music.load(str(desired_track))
                    current_bgm_track = desired_track
                except pygame.error:
                    current_bgm_track = None

        if current_bgm_track is not None:
            if not bgm_playing:
                pygame.mixer.music.play(-1)
                bgm_playing = True
        elif bgm_playing:
            pygame.mixer.music.stop()
            bgm_playing = False

        events = pygame.event.get()
        for event in events:
            if event.type == pygame.VIDEORESIZE and not display_state.is_fullscreen:
                display_state.remember_os_windowed_size((event.w, event.h))
                if pygame.display.get_surface() is not None:
                    screen = pygame.display.get_surface()

        result = current_screen.handle_events(events, screen, now)
        if not result.running:
            running = False
            continue
        if result.display_mode is not None:
            screen = _apply_display_mode(result.display_mode, screen, display_state)
        if result.next_screen is not None:
            current_screen = result.next_screen

        next_screen = current_screen.update(screen, now)
        if next_screen is not None:
            current_screen = next_screen

        current_screen.draw(screen, now)
        pygame.display.flip()
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()
