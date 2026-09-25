"""CLI profile for the trainable Qwen3.5-0.8B reaction policy."""

from __future__ import annotations

import argparse

from loop.adaptive.qwen_policy import QWEN35_08B_MODEL_ID, QWEN35_08B_REVISION

DEFAULT_INFERENCE_TIMEOUT_S = 30.0
DEFAULT_LAYOUT = "state_first"
DEFAULT_INDEPENDENT = True
DEFAULT_USE_GRAPHS = False
DEFAULT_DTYPE = "bfloat16"

DECIDER_PROFILES: dict[str, tuple[str, str]] = {
    "0.8b": (QWEN35_08B_MODEL_ID, QWEN35_08B_REVISION),
}


def add_decider_args(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("decider (native policy worker)")
    group.add_argument(
        "--decider-model",
        choices=tuple(DECIDER_PROFILES),
        default="0.8b",
        help="pinned Qwen3.5-0.8B reaction profile",
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
