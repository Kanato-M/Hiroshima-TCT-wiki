# -*- coding: utf-8 -*-
"""計測機器ドライバ層。

GUI から独立した同期的なデバイスラッパを提供する。
各クラスは simulate=True でハードウェア無しでも動作する
(開発・GUI動作確認用のシミュレーションモード)。

log 引数には callable(direction: str, text: str) を渡すと
送受信内容がGUIのログパネルへ流れる。direction は "TX"/"RX"/"INFO"/"ERR"。
"""

from .pilas_laser import PilasLaser
from .shrc203_stage import Shrc203Stage
from .lecroy_scope import LecroyScope
from .focus_camera import FocusCamera
from .scope_files import ScopeFileMirror

__all__ = ["PilasLaser", "Shrc203Stage", "LecroyScope", "FocusCamera",
           "ScopeFileMirror"]
