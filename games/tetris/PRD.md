# Tetris — Product Requirements Document

## 1. Overview

**Product:** A simple, well-designed Tetris game that feels polished and is easy to play on desktop and optionally mobile.

**Goals:**
- Faithful to classic Tetris mechanics (rotation, line clear, scoring, levels).
- **Modular architecture:** Clear separation of concerns (game logic, rendering, input, state) so modules can be tested, swapped, or extended independently.
- **Modern-looking UI:** Contemporary visual style (typography, spacing, colors, subtle depth) that feels current and polished—not dated or cluttered.
- Clean, readable UI with smooth controls and clear feedback.
- Minimal dependencies and straightforward architecture for maintainability.
- Playable in the browser with no install required.

**Non-goals (v1):** Multiplayer, leaderboards, custom themes, or complex animations.

---

## 2. Core Features

### 2.1 Gameplay
- **Tetrominoes:** I, O, T, S, Z, J, L — standard 7 pieces, standard colors.
- **Controls:** Move left/right, rotate (CW/CCW), soft drop, hard drop. Optional: hold piece.
- **Board:** 10×20 visible playfield; pieces spawn at top center.
- **Line clear:** Full rows disappear; lines above fall. No “line clear” animation required for v1 (instant is fine).
- **Scoring:** Classic or similar (e.g., 100/300/500/800 per 1/2/3/4 lines). Level increases every N lines; speed increases with level.
- **Game over:** When a new piece cannot spawn (overlaps existing blocks).

### 2.2 UX Requirements
- **Start/Pause/Restart:** Clear buttons or shortcuts (e.g., Enter, P, R).
- **Next piece:** Always visible (small preview).
- **Score & level:** Always visible (current score, level, lines cleared).
- **Feedback:** Brief visual/audio cue on line clear and game over. Optional background music when playing; user can turn music off via a Music toggle (sound effects remain).
- **Responsive:** Playable at a fixed logical resolution (e.g., 10×20 grid) that scales to fit the viewport.

### 2.3 Design Principles
- **Modern aesthetic:** Current, clean look—e.g. rounded corners, consistent spacing, a restrained color palette, and optional subtle shadows or gradients. Avoid “retro pixel” unless explicitly chosen as the art direction.
- **Readability:** High contrast between blocks and background; distinct piece colors.
- **Simplicity:** No clutter; focus on the board and essential info.
- **Responsiveness:** Input feels immediate (no perceptible input lag).
- **Modularity:** Game logic, renderer, and input are separate modules with clear interfaces; no tight coupling so pieces can be replaced or extended (e.g. new input sources, different renderer) without rewriting the core.
- **Accessibility:** Keyboard-first; consider reduced motion or high-contrast option later.

---

## 3. Technology Recommendations

### 3.1 Recommended Stack (Web)

| Layer        | Technology        | Rationale |
|-------------|-------------------|-----------|
| **Rendering** | HTML5 Canvas 2D | Simple, no framework lock-in; ideal for a 2D grid. Performant enough for Tetris. |
| **Language**  | TypeScript (or JavaScript) | Types improve maintainability; tooling is standard. |
| **Build**     | Vite             | Fast dev server, minimal config, native ESM. |
| **Styling**   | CSS (no framework) | Small surface area; custom CSS keeps bundle tiny and layout predictable. |

**Why not WebGL/Three.js:** Overkill for a 2D grid; Canvas 2D is simpler and sufficient.

**Why not React/Vue/Svelte:** For a single-game screen with one canvas and a few UI elements, vanilla TS + Canvas keeps the project small and the mental model clear. A framework can be added later if you add menus, settings, or routing.

### 3.2 Alternative Stacks

- **React + Canvas:** Use if you already use React and want components for score, next piece, buttons. Render the board in one `<canvas>` inside a React component.
- **Svelte + Canvas:** Good middle ground: reactive state with minimal boilerplate, still one canvas for the game grid.
- **Electron + Canvas:** Same web stack wrapped in Electron if you want a desktop app with install.

### 3.3 Suggested Project Structure (Modular)

Code is split by responsibility so each module has a single concern and can evolve independently:

```
tetris/
├── index.html
├── src/
│   ├── main.ts           # Entry only: wires modules, init canvas and game loop
│   ├── game.ts           # Game state (board, piece, score, level) — pure logic where possible
│   ├── tetrominoes.ts    # Piece shapes, colors, rotation (data + helpers)
│   ├── renderer.ts       # Draw board, piece, next, UI — no game rules
│   ├── input.ts          # Keyboard (and later touch) — emits actions, no direct state mutation
│   └── constants.ts      # Grid size, speeds, scoring — single source of truth
├── styles/
│   └── main.css          # Modern look: CSS variables, spacing scale, typography
├── package.json
├── tsconfig.json
└── vite.config.ts
```

---

## 4. Out of Scope (v1)

- ~~Sound effects / music (can add in v2).~~ *Added in v1: optional Web Audio effects for line clear and hard drop; looping background music when playing; Music toggle to turn BGM off (guideline: keep sound optional and user-controllable).*
- Mobile touch controls (can add in v2).
- Persisted high scores (localStorage can be added easily later).
- Animations (e.g., line clear flash, particle effects).
- Theming (dark/light is enough via CSS variables).

---

## 5. Success Criteria

- [x] All 7 tetrominoes spawn and rotate correctly (including wall kicks optional for v1).
- [x] Lines clear and score/level update correctly.
- [x] Game over triggers when spawn is blocked.
- [x] Pause and restart work without reload.
- [x] Layout is readable and scales on different window sizes.
- [x] No obvious input lag; hard drop and movement feel responsive.
- [x] Codebase is modular (game, renderer, input clearly separated; constants centralized).
- [x] UI looks modern (consistent typography, spacing, and visual style).

---

## 6. Phases

| Phase | Deliverable |
|-------|-------------|
| **1. Core** | Grid, piece spawn/move/rotate, collision, line clear, score/level, game over. |
| **2. Polish** | Next piece, hold (optional), pause/restart, basic styling. |
| **3. Extra** | Sound, simple line-clear feedback, high score (e.g., localStorage). |

---

*PRD version: 1.1 — Simple, well-designed Tetris (web). Modular, modern-looking.*
