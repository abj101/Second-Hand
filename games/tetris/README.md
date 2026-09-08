# Tetris

A simple, well-designed Tetris game built for the web. Faithful to classic mechanics with a modern UI, modular architecture, and no framework lock-in.

![Tetris](https://img.shields.io/badge/TypeScript-5.x-blue) ![Vite](https://img.shields.io/badge/Vite-5.x-646CFF) ![License](https://img.shields.io/badge/license-MIT-green)

## Features

- **Classic gameplay** — All 7 tetrominoes (I, O, T, S, Z, J, L), rotation (CW/CCW), soft drop, hard drop, hold piece
- **Scoring & levels** — 100/300/500/800 per 1/2/3/4 lines; level increases every 10 lines with faster fall speed
- **Modern UI** — Clean layout, readable typography, responsive design, ghost piece preview; controls and Music toggle in the left panel to keep the layout compact and avoid scroll
- **Modular code** — Game logic, renderer, and input are separate; easy to test and extend
- **Keyboard-first** — Full keyboard support; no install required, runs in the browser
- **Sound** — Optional Web Audio effects for line clear and hard drop; looping background music when playing; Music toggle to turn BGM off (no external files)

## Quick Start

### Prerequisites

- [Node.js](https://nodejs.org/) 18+ and npm

### Install & Run

```bash
# Clone the repository
git clone https://github.com/your-username/tetris.git
cd tetris

# Install dependencies
npm install

# Start development server
npm run dev
```

Open [http://localhost:5173](http://localhost:5173) in your browser.

### Build for Production

```bash
npm run build
npm run preview   # Optional: preview the production build
```

Output is in the `dist/` folder. Serve it with any static host (e.g. GitHub Pages, Netlify, Vercel).

## Controls

| Key | Action |
|-----|--------|
| **Enter** | Start game (or restart from game over) |
| **← / →** | Move left / right |
| **↑ / W** | Rotate clockwise |
| **A** | Rotate counter-clockwise |
| **↓ / S** | Soft drop |
| **Space** | Hard drop |
| **C** | Hold piece |
| **P** | Pause / Resume |
| **R** | Restart |

## Project Structure

```
tetris/
├── index.html
├── src/
│   ├── main.ts        # Entry: wires modules, game loop
│   ├── game.ts        # Game state & logic (reducer)
│   ├── tetrominoes.ts # Piece shapes, colors, rotation
│   ├── renderer.ts    # Canvas drawing (no game rules)
│   ├── input.ts       # Keyboard → actions
│   ├── audio.ts       # Web Audio effects (line clear, hard drop)
│   └── constants.ts   # Grid size, speeds, scoring
├── styles/
│   └── main.css       # Layout & theming
├── docs/              # Architecture & design notes
├── package.json
├── tsconfig.json
└── vite.config.ts
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for design and module boundaries.

## Tech Stack

- **Language:** TypeScript
- **Build:** [Vite](https://vitejs.dev/)
- **Rendering:** HTML5 Canvas 2D
- **Styling:** Plain CSS (variables, no framework)

## Documentation

- [Architecture](docs/ARCHITECTURE.md) — Module responsibilities and data flow
- [PRD](PRD.md) — Product requirements and success criteria

## License

MIT. See [LICENSE](LICENSE) for details.
