# -*- coding: utf-8 -*-
"""NKT Photonics PiLAS DX (コントローラ EGI-D2-40) のシリアル制御。

通信仕様 (PILAS Product Guide 6章 "RS232 Commands v1.1.AEe"):
  - 19200 baud, 8bit, stopbit 1, パリティ無し
  - コマンド終端は CR (\\r)
  - 設定コマンドは "done" 応答、不正コマンドは "invalid command!" 等
  - クエリ (ld?, f? など) は人間可読の1行応答
  - mState? はシステム状態を空白区切りの数値列で返す (15項目)

USB接続の場合は FTDI VCP ドライバにより仮想COMポートとして見える。
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

LogFunc = Optional[Callable[[str, str], None]]

# mState? 応答の各フィールド定義 (位置順)。Product Guide の表に対応。
MSTATE_FIELDS = (
    "interlock",          # 1: インターロック (1=on, 0=off)
    "emission_pulsed",    # 2: パルス発振 (1=on)
    "emission_cw",        # 3: CW発振 (1=on)
    "trigger_source",     # 4: トリガーソース (0=internal, 1=ext.Adj, 2=ext.TTL)
    "trigger_edge",       # 5: トリガーエッジ (1=rising, 0=falling)
    "tune_mode",          # 6: TUNEモード (1=auto, 0=manual)
    "freq_fine",          # 7: 内部周波数モード (1=fine, 0=coarse)
    "tec_on",             # 8: レーザーダイオードTEC (1=on)
    "temp_ok",            # 9: LD温度状態 (1=ok, 0=bad)
    "trigger_level",      # 10: 測定トリガーレベル
    "head_temp_raw",      # 11: レーザーヘッド温度 (x100 ℃, 2393 -> 23.93℃)
    "ext_freq_hz",        # 12: 測定された外部トリガー周波数 [Hz]
    "int_freq_hz",        # 13: 内部発振周波数 [Hz]
    "tune_permil",        # 14: TUNE値 ×100 (5000 -> 50.00%)。キー名は歴史的
                          #     経緯で permil だが単位は 0.01%。実機ログで
                          #     tune=0650 (65.0%) → 6500 を確認 (2026-10-01)。
                          #     % 換算済みの値は get_status の tune_percent
    "cw_power_permil",    # 15: CWパワー (2200 -> 22.00%)
)

TRIGGER_SOURCE_NAMES = {0: "internal", 1: "ext. Adj", 2: "ext. TTL"}


class PilasLaser:
    """PiLAS DX レーザーのシリアルラッパ (スレッドセーフ)。"""

    def __init__(self, port: str, baudrate: int = 19200,
                 timeout: float = 0.4, simulate: bool = False,
                 log: LogFunc = None):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.simulate = simulate
        self._log = log or (lambda d, t: None)
        self._ser = None
        self._lock = threading.Lock()
        self.connected = False
        # シミュレーション用の内部状態 (実機の既定値に近い値)
        self._sim = {
            "interlock": 1, "emission_pulsed": 0, "emission_cw": 0,
            "trigger_source": 0, "trigger_edge": 1, "tune_mode": 0,
            "freq_fine": 0, "tec_on": 1, "temp_ok": 1, "trigger_level": 0,
            "head_temp_raw": 2393, "ext_freq_hz": 0,
            "int_freq_hz": 1_000_000, "tune_permil": 5000,
            "cw_power_permil": 0,
        }

    # ------------------------------------------------------------------
    # 接続管理
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self.simulate:
            self.connected = True
            self._log("INFO", f"[SIM] PiLAS 接続 ({self.port})")
            return
        import serial  # 遅延import: シミュレーション時は pyserial 不要
        self._ser = serial.Serial(
            self.port, self.baudrate, bytesize=8, parity="N",
            stopbits=1, timeout=self.timeout,
        )
        # 電源投入時にコントローラが吐く起動ステータスが残っていることが
        # あるので読み捨てておく
        self._ser.reset_input_buffer()
        self.connected = True
        self._log("INFO", f"PiLAS 接続 ({self.port}, {self.baudrate} baud)")

    def disconnect(self) -> None:
        if self._ser is not None:
            self._ser.close()
            self._ser = None
        self.connected = False
        self._log("INFO", "PiLAS 切断")

    # ------------------------------------------------------------------
    # 低レベル送受信
    # ------------------------------------------------------------------
    def send(self, cmd: str) -> str:
        """コマンドを送信し応答文字列を返す。

        応答は複数行のことがある (クエリ + done 等) ため、受信が
        途切れるまで読み続ける方式にしている。
        """
        cmd = cmd.strip()
        with self._lock:
            self._log("TX", cmd)
            if self.simulate:
                resp = self._simulate_response(cmd)
            else:
                if self._ser is None:
                    raise RuntimeError("PiLAS が接続されていません")
                self._ser.reset_input_buffer()
                self._ser.write((cmd + "\r").encode("ascii"))
                chunks = b""
                # timeout 区切りで受信バッファが空になるまで読む
                while True:
                    chunk = self._ser.read(256)
                    if not chunk:
                        break
                    chunks += chunk
                resp = chunks.decode("ascii", errors="replace").strip()
            self._log("RX", resp if resp else "(no response)")
            return resp

    # ------------------------------------------------------------------
    # 高レベル操作
    # ------------------------------------------------------------------
    def set_emission(self, on: bool) -> str:
        """パルスレーザー発振の ON/OFF (ld=1 / ld=0)。"""
        return self.send(f"ld={1 if on else 0}")

    def set_trigger_source(self, source: int) -> str:
        """トリガーソース設定 (0=internal, 1=ext.Adj, 2=ext.TTL)。"""
        return self.send(f"ts={source}")

    def set_trigger_edge(self, rising: bool) -> str:
        return self.send(f"te={1 if rising else 0}")

    def set_trigger_level_mv(self, level_mv: int) -> str:
        """ext.Adj 用トリガーレベル [-4800, +4800] mV。"""
        level_mv = max(-4800, min(4800, int(level_mv)))
        return self.send(f"tl={level_mv}")

    def set_frequency(self, hz: int) -> str:
        """内部発振周波数 [Hz] (25 Hz 〜 最大40 MHz)。"""
        hz = int(hz)
        return self.send(f"f={hz}")

    def set_tune_mode(self, auto: bool) -> str:
        return self.send(f"tm={1 if auto else 0}")

    def set_tune_percent(self, percent: float) -> str:
        """TUNE値設定。percent=50.0 -> 'tune=0500' (0〜100%)。"""
        permil = max(0, min(1000, round(percent * 10)))
        return self.send(f"tune={permil:04d}")

    def get_status(self) -> dict:
        """mState? を送ってパースした状態辞書を返す。

        追加キー: head_temp_c (℃換算), trigger_source_name
        """
        resp = self.send("mState?")
        parts = resp.split()
        values = []
        for token in parts:
            try:
                values.append(int(token))
            except ValueError:
                continue
        if len(values) < len(MSTATE_FIELDS):
            raise RuntimeError(f"mState? 応答の解析に失敗: {resp!r}")
        status = dict(zip(MSTATE_FIELDS, values))
        status["head_temp_c"] = status["head_temp_raw"] / 100.0
        status["tune_percent"] = status["tune_permil"] / 100.0
        status["trigger_source_name"] = TRIGGER_SOURCE_NAMES.get(
            status["trigger_source"], "?")
        return status

    def emergency_off(self) -> None:
        """非常停止: 発振を止める。"""
        try:
            self.send("ld=0")
        except Exception as exc:  # 停止操作は例外で中断させない
            self._log("ERR", f"レーザー停止失敗: {exc}")

    # ------------------------------------------------------------------
    # シミュレーション応答 (実機と同じ書式を返す)
    # ------------------------------------------------------------------
    def _simulate_response(self, cmd: str) -> str:
        time.sleep(0.02)
        s = self._sim
        if cmd == "mState?":
            order = list(MSTATE_FIELDS)
            return " ".join(str(s[k]) for k in order)
        if cmd == "ld?":
            return "pulsed laser emission: " + ("on" if s["emission_pulsed"] else "off")
        if cmd == "f?":
            return f"int. frequency: {s['int_freq_hz']} Hz"
        if cmd == "ts?":
            return "trigger source: " + TRIGGER_SOURCE_NAMES.get(s["trigger_source"], "?")
        if cmd == "tune?":
            return str(s["tune_permil"] // 100)
        if cmd == "version?":
            return "PiLAS DX simulator V11AEe"
        if "=" in cmd:
            key, _, val = cmd.partition("=")
            try:
                num = int(val)
            except ValueError:
                return "data format error!"
            mapping = {
                "ld": "emission_pulsed", "ts": "trigger_source",
                "te": "trigger_edge", "tm": "tune_mode",
                "f": "int_freq_hz", "tune": "tune_permil",
                "tl": "trigger_level", "cw": "emission_cw",
                "cwl": "cw_power_permil",
            }
            if key in mapping:
                # tune= は 0.1% 単位 (tune=0650 → 65.0%)、mState の
                # 報告値は 0.01% 単位 (6500) なので 10 倍して持つ
                s[mapping[key]] = num * 10 if key == "tune" else num
                return "done"
            return "invalid command!"
        return "invalid command!"
