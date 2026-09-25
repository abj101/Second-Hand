"""Games the bench may open. Product code does not import this list."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TestGame:
    name: str
    repo: str | None
    license: str | None


# STS2 is a local window, not a public repo on this list.
TEST_GAMES: tuple[TestGame, ...] = (
    TestGame("Tetris", "https://github.com/jakesgordon/javascript-tetris", "MIT"),
    TestGame("Snake", "https://github.com/patorjk/JavaScript-Snake", "MIT"),
    TestGame("Pacman", "https://github.com/daleharvey/pacman", "WTFPL"),
    TestGame("Javascript Racer", "https://github.com/jakesgordon/javascript-racer", "MIT"),
    TestGame("Space Invaders", "https://github.com/StrykerKKD/SpaceInvaders", "MIT"),
    TestGame("Sokoban", "https://github.com/taniarascia/sokoban", "MIT"),
    TestGame("Infinite Mario", "https://github.com/robertkleffner/mariohtml5", "Unlicense"),
    TestGame("Floppy Bird", "https://github.com/nebez/floppybird", "Apache-2.0"),
    TestGame("Breakout", "https://github.com/jakesgordon/javascript-breakout", "MIT"),
    TestGame("2048", "https://github.com/gabrielecirulli/2048", "MIT"),
    TestGame("STS2", None, None),
)


def by_name(name: str) -> TestGame:
    for game in TEST_GAMES:
        if game.name == name:
            return game
    raise KeyError(name)
