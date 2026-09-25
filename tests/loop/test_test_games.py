"""The bench game list is the one we are willing to open."""

from benchmarks.test_games import TEST_GAMES


def test_test_games_are_the_allowed_list() -> None:
    assert [(g.name, g.repo, g.license) for g in TEST_GAMES] == [
        ("Tetris", "https://github.com/jakesgordon/javascript-tetris", "MIT"),
        ("Snake", "https://github.com/patorjk/JavaScript-Snake", "MIT"),
        ("Pacman", "https://github.com/daleharvey/pacman", "WTFPL"),
        ("Javascript Racer", "https://github.com/jakesgordon/javascript-racer", "MIT"),
        ("Space Invaders", "https://github.com/StrykerKKD/SpaceInvaders", "MIT"),
        ("Sokoban", "https://github.com/taniarascia/sokoban", "MIT"),
        ("Infinite Mario", "https://github.com/robertkleffner/mariohtml5", "Unlicense"),
        ("Floppy Bird", "https://github.com/nebez/floppybird", "Apache-2.0"),
        ("Breakout", "https://github.com/jakesgordon/javascript-breakout", "MIT"),
        ("2048", "https://github.com/gabrielecirulli/2048", "MIT"),
        ("STS2", None, None),
    ]
