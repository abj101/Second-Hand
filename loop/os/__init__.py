"""Minimal screen capture and input boundary used by the adaptive runtime."""

from loop.os.capture import EncodedFrame, FrameEncoder, encode_image
from loop.os.window import CaptureProvenance, WindowTarget, find_window, grab_client_capture

__all__ = [
    "CaptureProvenance",
    "EncodedFrame",
    "FrameEncoder",
    "WindowTarget",
    "encode_image",
    "find_window",
    "grab_client_capture",
]
