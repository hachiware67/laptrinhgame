# Custom UNO Online (BTL4)
Institution: Ho Chi Minh City University of Technology (HCMUT)

> A Python/Pygame UNO project with local play, LAN multiplayer, host-authoritative validation, AI backfill, configurable house rules (0/7/8), and a custom Mixi extension pack.

## 1) Introduction

- **Genre**: Card / Party / Strategy
- **Language / Engine**: Python + Pygame
- **Modes**:
  - Local match (2 or 4 players, AI auto-plays non-local seats)
  - LAN multiplayer room (host + clients, AI fills empty seats at start)
- **Project goals**:
  - Build a full UNO loop with modern UI flow (title -> settings -> match -> end)
  - Implement custom house rules and extension cards cleanly in a game manager
  - Synchronize gameplay and animations across LAN clients with host authority

## 2) Features

- [x] Complete screen flow: Title, Instructions, Main Settings, Game Settings, Extension Packs, Multiplayer Lobby/Room, Playing, End.
- [x] Local gameplay with keyboard + mouse controls.
- [x] Host-authoritative LAN multiplayer (room discovery + direct invite join).
- [x] AI backfill when match starts and room is not full.
- [x] Rule toggles: Rule 0, Rule 7, Rule 8 (+ configurable reaction timer).
- [x] Two-player Reverse behavior toggle (`Reverse` or `Skip` style).
- [x] UNO call validation and penalty visuals (green/red full-screen flashes).
- [x] Mixi extension pack cards with custom effects and SFX.
- [x] Unit tests for display behavior, game rules, multiplayer security, and LAN connection logic.

## 3) System Requirements

- Python: **3.12+**
- OS: Windows / macOS / Linux
- Dependencies: see [requirements.txt](requirements.txt)

## 4) Installation

```bash
# from BTL4/
python -m venv .venv

# Windows (PowerShell)
.\.venv\Scripts\Activate.ps1

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## 5) Run The Game

```bash
python main.py
```

## 6) Controls

### Menus

- **Mouse Left Click**: Press buttons / select options
- **Esc**:
  - Exit title screen
  - Go back from sub-screens
  - Leave room in multiplayer room view
- **Tab** (Multiplayer forms): switch focused input field
- **Backspace**: delete character in focused text field

### In Match

- **Mouse Left Click**:
  - Select/play card
  - Draw from pile
  - Press UNO / Sort / React / choice buttons
- **Left / Right Arrow**: move hand selection
- **Enter / Space**: play selected card
- **D**: draw card (draw-for-decision flow)
- **S**: sort hand
- **U**: call UNO
- **Esc**: open/close pause menu

### Pause Menu

- **Up/Down** or **W/S**: move selection
- **Enter/Space**: confirm
- **Esc**: resume

## 7) Gameplay Rules

### Core UNO

- Match by **color**, **number**, or **action kind**.
- Wild cards can be played anytime and require choosing a color.
- If no legal move exists, draw one card:
  - If drawn card is legal, choose to **play** or **keep**.
  - If not legal, keep it and end turn.

### Win Restriction

- You **cannot** finish on action cards:
  - Skip, Reverse, +2, Wild, +4
  - Mixi extension actions

### Draw Stacking

- +2 stack allows next player to respond with +2 or +4.
- If chain is started by +4, only +4 can continue the stack.
- First player who cannot continue draws full penalty and loses turn.

### House Rules

- **Rule 0**: playing number 0 lets current player choose hand-pass direction (clockwise/counter-clockwise).
- **Rule 7**: playing number 7 lets current player choose a target player and swap hands.
- **Rule 8**: playing number 8 starts a timed reaction event:
  - If everyone reacts in time: last reactor draws 2.
  - Otherwise: every non-reactor draws 2.

### UNO Call Logic

- UNO can be called only when:
  - It is your turn.
  - You have exactly 2 cards.
  - At least one of those cards is legal to play.
- If you reach 1 card without valid UNO call, you draw 2 penalty cards.

## 8) Mixi Extension Pack

When enabled in **Extension Packs**, these cards are added:

- **Mixi Airstrike (`+67`)**:
  - Adds `+67` draw penalty.
  - Only `+67` stacks with `+67`.
- **Dogs Will Pay (`counter`)**:
  - Playable only during +2/+4 stack.
  - Cancels stack and redirects draw amount to the original stack source.
- **Faker's Silence (`silence`)**:
  - Next player is skipped for 3 turns.
- **Mom Physics May Cry (`mom_may_cry`)**:
  - Reduces your hand to 7 random cards; rest are shuffled back to draw pile.
- **Mixi Smile (`flashbang`)**:
  - Face-down effect: self gets 1 affected turn, every other player gets 2.

## 9) Multiplayer (LAN)

### Lobby/Room Flow

1. Open **Multiplayer**.
2. Enter player name.
3. Either:
  - Join a discovered room from lobby list, or
  - Join by invite string format: `ROOMCODE@IP:PORT`, or
  - Create room -> configure game settings -> host match.
4. Host presses **Start Match**.
5. Empty seats are auto-filled with AI up to room capacity (2 or 4).

### Networking Model

- Host machine runs authoritative game state.
- Clients send intended actions (`submit_action`).
- Host validates turn ownership, legality, pending effects, and win constraints.
- Host broadcasts `match_sync` packets so clients stay visually synchronized.

### Security Notes

- Client traffic is treated as untrusted input.
- Server issues its own session tokens (does not trust client-supplied token).
- Host receive time is used for action timing.
- TCP line size is capped to reduce malformed packet abuse.
- LAN only; transport is not encrypted.

## 10) Main Settings & Display

- Audio sliders: Master / Music / SFX.
- Display mode toggle: **Windowed** / **Fullscreen**.
- Window sizing auto-fits desktop with minimum gameplay-friendly bounds.

## 11) Testing

Run these from `BTL4/`:

```bash
python -m unittest discover -v
python -m compileall main.py scripts tests
```

Test coverage includes:

- Rule toggles and gameplay rule regressions
- UNO call and penalty sync events
- Multiplayer security checks
- LAN invite parsing and room connection behavior
- Display/window sizing and settings layout constraints

## 12) Project Structure

```text
BTL4/
├─ main.py
├─ requirements.txt
├─ README.md
├─ task.md
├─ uno_spec.pdf
├─ assets/
│  ├─ bgm/
│  ├─ sfx/
│  ├─ sprites/
│  └─ enhance/
│     ├─ Lilita_One/
│     ├─ kenney_boardgame-pack/
│     └─ kenney_ui-pack/
├─ scripts/
│  ├─ __init__.py
│  ├─ ai.py
│  ├─ animation.py
│  ├─ assets.py
│  ├─ cards.py
│  ├─ deck.py
│  ├─ game_manager.py
│  ├─ multiplayer.py
│  ├─ screens.py
│  ├─ sprites.py
│  └─ ui.py
└─ tests/
   ├─ __init__.py
   ├─ test_display_mode.py
   ├─ test_game_manager_rules.py
   ├─ test_multiplayer_lan_connection.py
   ├─ test_multiplayer_security.py
   └─ test_multiplayer_visual_sync.py
```

## 13) Assets & References

- **Kenney UI Pack** (`assets/enhance/kenney_ui-pack`)
  - License: CC0
  - Source: www.kenney.nl
- **Kenney Boardgame Pack** (`assets/enhance/kenney_boardgame-pack`)
  - License: CC0
  - Source: www.kenney.nl
- **Lilita One Font** (`assets/enhance/Lilita_One`)
  - License: SIL Open Font License 1.1
  - Author: Juan Montoreano

- **UNO card atlas** (`assets/sprites/PC _ Computer - UNO - Cards - Cards (Classic).png`)
  - Source: [The Textures Resource](https://textures.spriters-resource.com/pc_computer/uno/asset/374167/)
- **Mixi extension card images** (`assets/sprites/counter.png`, `draw67.jpg`, `flashbang.webp`, `mom_may_cry.png`, `silence.jpg`)
  - Source: Courtesy to Do Mixi and Faker
- **Main menu / in-game BGM** (`assets/bgm/mainmenu.mp3`, `assets/bgm/domixi tay bac.mp3`)
  - Source: [Dân ca Thank Độ](https://www.youtube.com/watch?v=XhxnOCu0j7c)
- **SFX files** (`assets/sfx/*.mp3`)
  - Source: Courtesy to Do Mixi and Counter-Strike
