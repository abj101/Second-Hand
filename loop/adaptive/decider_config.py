"""CLI and profile constants for the native Decider policy worker."""

from __future__ import annotations

import argparse

from loop.adaptive.decider_policy import (
    DECIDER_08B_MODEL_ID,
    DECIDER_08B_MODEL_REVISION,
    DECIDER_2B_MODEL_ID,
    DECIDER_2B_MODEL_REVISION,
)

DEFAULT_INFERENCE_TIMEOUT_S = 30.0
DEFAULT_LAYOUT = "state_first"
DEFAULT_INDEPENDENT = True
DEFAULT_USE_GRAPHS = False
DEFAULT_DTYPE = "bfloat16"

DECIDER_PROFILES: dict[str, tuple[str, str]] = {
    "0.8b": (DECIDER_08B_MODEL_ID, DECIDER_08B_MODEL_REVISION),
    "2b": (DECIDER_2B_MODEL_ID, DECIDER_2B_MODEL_REVISION),
}


def add_decider_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("decider (native policy worker)")
    group.add_argument(
        "--decider-model",
        choices=tuple(DECIDER_PROFILES),
        default="0.8b",
        help="pinned Hugging Face model profile (not wired until shadow loop)",
    )
    group.add_argument(
        "--decider-timeout",
        type=float,
        default=DEFAULT_INFERENCE_TIMEOUT_S,
        metavar="SECONDS",
        help="reaction inference deadline; late or timed-out turns are unavailable",
    )
    group.add_argument(
        "--decider-graphs",
        action="store_true",
        help="enable Decider compiled CUDA graphs after parity gates (default eager)",
    )
