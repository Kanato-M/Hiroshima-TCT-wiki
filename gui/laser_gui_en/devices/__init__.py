# -*- coding: utf-8 -*-
"""Instrument driver layer.

Provides synchronous device wrappers that are independent of the GUI.
Every class also works without hardware when simulate=True
(simulation mode for development and GUI checks).

Pass a callable(direction: str, text: str) as the log argument to stream
all TX/RX traffic to the GUI log panel. direction is "TX"/"RX"/"INFO"/"ERR".
"""

from .pilas_laser import PilasLaser
from .shrc203_stage import Shrc203Stage
from .lecroy_scope import LecroyScope
from .focus_camera import FocusCamera
from .scope_files import ScopeFileMirror

__all__ = ["PilasLaser", "Shrc203Stage", "LecroyScope", "FocusCamera",
           "ScopeFileMirror"]
