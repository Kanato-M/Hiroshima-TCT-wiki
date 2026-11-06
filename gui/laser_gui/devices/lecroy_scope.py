# -*- coding: utf-8 -*-
"""Teledyne LeCroy WavePro/WaveRunner オシロスコープの LAN 制御。

MAUI リモートコマンドを 2 種類のトランスポートで送信できる:
  1. VICP (port 1861, LeCroy 独自): このファイルにネイティブ実装。
     オシロの Remote 設定の既定値「TCPIP (VICP)」で使える。
     NI-VISA / VICP passport は不要。リソース名 "VICP::<SCOPE_IP>::INSTR"。
  2. VXI-11 (LXI): PyVISA 経由。オシロ側 Remote を「LXI (VXI11)」に
     した場合のみ。リソース名 "TCPIP0::<SCOPE_IP>::inst0::INSTR"。
接続時に指定方式で *IDN? 応答を検証し、失敗したらもう一方の方式へ
自動フォールバックする (「接続中なのに通信できない」状態を防ぐ)。

参照: Teledyne LeCroy "MAUI Oscilloscopes Remote Control and Automation Manual"
      / "VICP Protocol" (ヘッダ 8 バイト: operation, version=1, seq, 0, 長さ BE32)

主に使うコマンド:
  CHDR OFF            応答からコマンドヘッダを除去 (パース簡略化のため)
  TRMD AUTO|NORM|SINGLE|STOP     トリガーモード
  TRSE EDGE,SR,EX     エッジトリガーソース設定
  EX:TRLV 410MV       トリガーレベル
  EX:TRCP DC          トリガー結合
  TRDL -53NS          水平オフセット (トリガー遅延)
  Cn:TRA ON|OFF, Cn:VDIV, Cn:OFST, Cn:CPL D50  チャンネル設定
  TDIV 50NS           タイムベース
  SEQ ON,200          シーケンス(セグメント)モード
  CLSW                スイープ(アベレージング)クリア
  HCSU/SCDP           画面ハードコピー設定 / 画面イメージ取得 (ミラーリング用)
  *SAV n / *RCL n     本体パネル設定の保存/呼び出し
  VBS 'app....'       Automation (ActiveDSO相当) — 波形オートセーブ設定等
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from typing import Callable, Optional

LogFunc = Optional[Callable[[str, str], None]]


class _VicpTransport:
    """LeCroy VICP プロトコル (TCP port 1861) の最小実装。

    フレーム構造: 8バイトヘッダ + ペイロード。
      operation: DATA=0x80, REMOTE=0x40, EOI=0x01 の OR
      version:   0x01 固定
      sequence:  1〜255 の循環 (0 は不可)
      spare:     0
      length:    ペイロード長 (ビッグエンディアン 32bit)
    応答は EOI フラグが立つまで複数フレームを連結する。
    """

    DEFAULT_PORT = 1861
    OP_DATA, OP_REMOTE, OP_EOI = 0x80, 0x40, 0x01

    def __init__(self, host: str, port: int | None = None,
                 timeout_s: float = 10.0):
        self._sock = socket.create_connection(
            (host, port or self.DEFAULT_PORT), timeout=timeout_s)
        self._sock.settimeout(timeout_s)
        self._timeout_s = timeout_s
        self._seq = 0
        # 直前の受信がタイムアウトで途切れたか。True のままだと読み残しの
        # フレーム (画面PNG等) が次のクエリ応答に混入して文字化け・応答ズレ
        # を起こすため、次のコマンド送信前にバッファを読み捨てて再同期する。
        self._dirty = False

    def _next_seq(self) -> int:
        self._seq = self._seq % 255 + 1
        return self._seq

    def _drain(self, grace_s: float = 0.3) -> None:
        """受信バッファに残った古い応答フレームを読み捨てる (再同期)。

        grace_s=0 は到着済みデータのみ非ブロッキングで破棄する。
        """
        try:
            self._sock.settimeout(grace_s if grace_s > 0 else 0.0)
            while self._sock.recv(65536):
                pass
        except (TimeoutError, socket.timeout, BlockingIOError, OSError):
            pass
        finally:
            self._sock.settimeout(self._timeout_s)

    def write(self, cmd: str) -> None:
        if self._dirty:
            # タイムアウト直後: 遅延データの到着を少し待ってから読み捨てる
            self._drain(0.3)
            self._dirty = False
        else:
            # 通常時も到着済みの残骸があれば読み捨てる (非ブロッキング)
            self._drain(0.0)
        payload = (cmd + "\n").encode("ascii")
        header = struct.pack(
            ">BBBBI", self.OP_DATA | self.OP_REMOTE | self.OP_EOI,
            0x01, self._next_seq(), 0, len(payload))
        self._sock.sendall(header + payload)

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self._sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("VICP 接続が切断されました")
            buf += chunk
        return buf

    def read_raw(self) -> bytes:
        """EOI フレームまでのペイロードを連結して返す。"""
        try:
            data = b""
            while True:
                op, _ver, _seq, _sp, length = struct.unpack(
                    ">BBBBI", self._recv_exact(8))
                if length:
                    data += self._recv_exact(length)
                if op & self.OP_EOI:
                    return data
        except (TimeoutError, socket.timeout):
            # 読みかけのフレームが後から届くと応答がズレるため、
            # 次のコマンド送信前に読み捨てる (write 側で _drain)
            self._dirty = True
            raise TimeoutError(
                "VICP 応答タイムアウト (受信残データは次回コマンド時に"
                "破棄して再同期します)")

    def query(self, cmd: str) -> str:
        self.write(cmd)
        return self.read_raw().decode("ascii", errors="replace").strip()

    def close(self) -> None:
        self._sock.close()


class _VisaTransport:
    """PyVISA (VXI-11/LXI) 経由のトランスポート。"""

    def __init__(self, resource: str, timeout_ms: int):
        import pyvisa
        try:
            # NI-VISA 等のシステムバックエンドを優先し、無ければ pyvisa-py
            rm = pyvisa.ResourceManager()
        except (ValueError, OSError):
            rm = pyvisa.ResourceManager("@py")
        self._inst = rm.open_resource(resource)
        self._inst.timeout = timeout_ms

    def write(self, cmd: str) -> None:
        self._inst.write(cmd)

    def query(self, cmd: str) -> str:
        return self._inst.query(cmd).strip()

    def read_raw(self) -> bytes:
        return bytes(self._inst.read_raw())

    def close(self) -> None:
        self._inst.close()


class LecroyScope:
    """LeCroy MAUI オシロのラッパ (スレッドセーフ)。

    ミラーリングスレッドと操作コマンドが同一 VISA セッションを共有するため、
    全ての送受信を self._lock で直列化している。
    """

    CHANNELS = ("C1", "C2", "C3", "C4")
    TRIGGER_SOURCES = ("EX", "C1", "C2", "C3", "C4", "LINE")

    def __init__(self, resource: str, timeout_ms: int = 10000,
                 simulate: bool = False, log: LogFunc = None):
        self.resource = resource
        self.timeout_ms = timeout_ms
        self.simulate = simulate
        # シミュレーション時の振幅モデル: callable(ch) -> 振幅 [V]
        self.sim_amplitude = None
        self._log = log or (lambda d, t: None)
        self._transport = None
        self._lock = threading.Lock()
        self.connected = False
        # True の間は画面ミラーを一時停止する (get_screen_png が空を返す)。
        # シーケンス取得中の SCDP は応答が遅くタイムアウト→応答ズレの
        # 原因になるため、自動スキャン実行中に ScanWorker が立てる。
        self.mirror_paused = False
        self._sim_t0 = time.monotonic()
        self._sim_saved = 0   # シミュレーション時の保存ファイル数 (検証用)

    # ------------------------------------------------------------------
    # 接続管理
    # ------------------------------------------------------------------
    @staticmethod
    def _parse_resource(resource: str) -> tuple[str, str, int | None]:
        """リソース名を (方式, ホスト, ポート) に分解する。

        例: "VICP::<SCOPE_IP>::INSTR"           → ("VICP", ip, None)
            "VICP::<SCOPE_IP>::1861::INSTR"     → ("VICP", ip, 1861)
            "TCPIP0::<SCOPE_IP>::inst0::INSTR"  → ("VXI-11", ip, None)
        """
        parts = resource.split("::")
        kind = "VICP" if parts[0].upper().startswith("VICP") else "VXI-11"
        host = parts[1] if len(parts) > 1 else resource
        port = None
        if len(parts) > 2 and parts[2].isdigit():
            port = int(parts[2])
        return kind, host, port

    def _open_transport(self, kind: str, host: str, port: int | None):
        if kind == "VICP":
            return _VicpTransport(host, port, timeout_s=self.timeout_ms / 1000)
        return _VisaTransport(
            f"TCPIP0::{host}::inst0::INSTR", self.timeout_ms)

    def connect(self) -> None:
        """指定方式で接続し *IDN? 応答を検証する。

        失敗時はもう一方の方式 (VICP ⇔ VXI-11) を自動で試す。
        *IDN? に応答しない接続は「接続中なのに通信不能」になるため、
        成立させずエラーにする。
        """
        if self.simulate:
            self.connected = True
            self._log("INFO", f"[SIM] LeCroy 接続 ({self.resource})")
            return
        kind, host, port = self._parse_resource(self.resource.strip())
        order = ["VICP", "VXI-11"] if kind == "VICP" else ["VXI-11", "VICP"]
        errors = []
        for k in order:
            try:
                transport = self._open_transport(k, host, port)
            except Exception as exc:
                errors.append(f"[{k}] 接続失敗: {exc}")
                self._log("WARN", errors[-1])
                continue
            try:
                # 応答からヘッダを外して数値パースを容易にする
                transport.write("CHDR OFF")
                idn = transport.query("*IDN?")
            except Exception as exc:
                transport.close()
                errors.append(f"[{k}] *IDN? 応答なし: {exc}")
                self._log("WARN", errors[-1])
                continue
            if not idn:
                transport.close()
                errors.append(f"[{k}] *IDN? が空応答")
                self._log("WARN", errors[-1])
                continue
            if "LECROY" not in idn.upper():
                self._log("WARN", f"*IDN? 応答が LeCroy ではありません: {idn}")
            self._transport = transport
            self.connected = True
            self._log("INFO", f"LeCroy 接続 ({k}): {idn}")
            return
        raise RuntimeError(
            "LeCroy と通信できません:\n" + "\n".join(errors) + "\n"
            "確認: (1) オシロ側 Utilities → Remote の制御方式 "
            "(既定 TCPIP(VICP)=port 1861 / LXI(VXI11)) と IP 設定 "
            "(2) PC と同一セグメント (<LAB_SUBNET>.x, サブネットマスク 255.255.255.0) か "
            "(3) 他のPC/アプリが接続を占有していないか")

    def disconnect(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
        self.connected = False
        self._log("INFO", "LeCroy 切断")

    # ------------------------------------------------------------------
    # 低レベル送受信
    # ------------------------------------------------------------------
    def write(self, cmd: str) -> None:
        with self._lock:
            self._log("TX", cmd)
            if self.simulate:
                time.sleep(0.01)
                return
            if self._transport is None:
                raise RuntimeError("LeCroy が接続されていません")
            self._transport.write(cmd)

    def query(self, cmd: str) -> str:
        with self._lock:
            self._log("TX", cmd)
            if self.simulate:
                time.sleep(0.01)
                resp = self._simulate_query(cmd)
            else:
                if self._transport is None:
                    raise RuntimeError("LeCroy が接続されていません")
                resp = self._transport.query(cmd)
            self._log("RX", resp)
            return resp

    def vbs(self, statement: str) -> None:
        """Automation コマンド実行 (例: app.SaveRecall.Waveform.SaveFile)。"""
        self.write(f"VBS '{statement}'")

    def vbs_query(self, expression: str) -> str:
        return self.query(f"VBS? 'return={expression}'")

    # ------------------------------------------------------------------
    # 画面ミラーリング
    # ------------------------------------------------------------------
    def get_screen_png(self) -> bytes:
        """画面ハードコピー (PNG) を取得する。約1秒周期のミラー表示用。

        mirror_paused 中 (自動スキャンの取得中) は空バイト列を返し、
        ミラースレッドはエラーにせず次の周期まで待つ。
        """
        if self.mirror_paused:
            return b""
        with self._lock:
            if self.simulate:
                return self._simulate_screen()
            if self._transport is None:
                raise RuntimeError("LeCroy が接続されていません")
            # DEST,REMOTE でファイルではなくリモートホストへ転送させる
            self._transport.write(
                "HCSU DEV,PNG,FORMAT,LANDSCAPE,BCKG,BLACK,"
                "DEST,REMOTE,PORT,NET,AREA,DSOWINDOW")
            self._transport.write("SCDP")
            return self._transport.read_raw()

    # ------------------------------------------------------------------
    # トリガー / チャンネル / タイムベース
    # ------------------------------------------------------------------
    def set_trigger_mode(self, mode: str) -> None:
        """mode: AUTO / NORM / SINGLE / STOP"""
        self.write(f"TRMD {mode}")

    def set_edge_trigger(self, source: str, level: str, slope: str,
                         coupling: str | None = None) -> None:
        """エッジトリガー設定。

        source: EX / C1..C4, level: '100MV' 等, slope: POS / NEG,
        coupling: DC / AC / HFREJ / LFREJ (None なら変更しない)。
        レーザーの TRIG OUT を EXT に入れる標準構成では
        source=EX, coupling=DC, slope=POS, level=410MV。
        """
        self.write(f"TRSE EDGE,SR,{source},HT,OFF")
        self.write(f"{source}:TRLV {level}")
        self.write(f"{source}:TRSL {slope}")
        if coupling:
            self.write(f"{source}:TRCP {coupling}")

    def set_channel(self, ch: str, on: bool | None = None,
                    vdiv: str | None = None, offset: str | None = None,
                    coupling: str | None = None) -> None:
        """チャンネル設定 (None の項目は変更しない)。

        coupling は AC-LGAD 高速信号では反射防止のため 'D50' (DC 50Ω) を使う。
        """
        if on is not None:
            self.write(f"{ch}:TRA {'ON' if on else 'OFF'}")
        if vdiv:
            self.write(f"{ch}:VDIV {vdiv}")
        if offset:
            self.write(f"{ch}:OFST {offset}")
        if coupling:
            self.write(f"{ch}:CPL {coupling}")

    def set_channel_average(self, ch: str, sweeps: int) -> None:
        """チャンネルのプリプロセッシング平均 (Averaging) を設定する。

        app.Acquisition.Cx.AverageSweeps を書き換える。1 で平均なし
        (OFF 相当)。積算のリセットは clear_sweeps (CLSW)。
        """
        self.vbs(f'app.Acquisition.{ch}.AverageSweeps = {max(1, int(sweeps))}')

    def set_averaging_off(self) -> None:
        """全チャンネルのプリプロセッシング平均を OFF (AverageSweeps=1) にする。

        アベレージングが ON のままだと SINGLE/シーケンス取得の完了判定
        (TRMD? の STOP 待ち) が機能しない (2026-07 実機確認: リアルタイム
        波形が必須)。標準設定の一括適用と自動スキャン開始時に必ず呼ぶ。
        """
        for ch in self.CHANNELS:
            self.set_channel_average(ch, 1)

    def set_tdiv(self, tdiv: str) -> None:
        self.write(f"TDIV {tdiv}")

    def set_trigger_delay(self, delay: str) -> None:
        """水平オフセット (トリガー遅延)。

        例: '-53NS' → 1 ns/div で表示範囲が +48 ns 〜 +58 ns になる
        (標準測定条件。AC-LGAD 信号はレーザートリガーの約 52 ns 後)。
        """
        self.write(f"TRDL {delay}")

    def set_sequence(self, on: bool, segments: int = 200) -> None:
        """シーケンスモード (1トリガー=1セグメントの高速連続取得)。"""
        if on:
            self.write(f"SEQ ON,{segments}")
        else:
            self.write("SEQ OFF")

    def clear_sweeps(self) -> None:
        """アベレージング等の積算をリセットする。"""
        self.write("CLSW")

    def arm_single(self) -> None:
        """SINGLE トリガーで武装する (シーケンス設定時は全セグメント取得)。"""
        self.write("TRMD SINGLE")

    def measure_pava(self, ch: str, param: str = "PKPK") -> tuple[float, str]:
        """波形パラメータ測定 (PAVA?) の値と状態を返す。

        param: PKPK / AMPL / MAX / MIN 等 (MAUI リモートマニュアル参照)。
        応答例 (CHDR OFF): "PKPK,206.9E-3 V,OK"。値がパースできない場合は
        NaN を返す。状態 (OK/AV/NP...) は呼び出し側で判断する
        (NP = 波形なし)。Z オートフォーカスの振幅読み出しに使う。
        """
        resp = self.query(f"{ch}:PAVA? {param}")
        parts = [p.strip() for p in resp.split(",")]
        value = float("nan")
        for p in parts:
            token = p.split()[0] if p else ""
            try:
                value = float(token)
                break
            except ValueError:
                continue
        state = parts[-1] if parts else ""
        return value, state

    def acquisition_stopped(self) -> bool:
        """取得が完了 (STOP 状態) したかを返す。

        SINGLE 取得はセグメント/スイープ完了後に自動で STOP になるため、
        これをポーリングして取得完了を判定する (自動スキャン用)。
        """
        return "STOP" in self.query("TRMD?").upper()

    # ------------------------------------------------------------------
    # 波形保存 / セットアップ保存
    # ------------------------------------------------------------------
    def ensure_directory(self, directory: str) -> None:
        """オシロ本体側に保存先フォルダを (親階層も含めて) 作成する。

        日付ベースの保存先 (<base>\\<年>\\<YYYYMMDD>\\scan_...) は初回は
        存在しないため、保存設定の前にこれで掘っておく。既存でもエラーに
        しない (On Error Resume Next)。
        """
        parts = directory.replace("/", "\\").split("\\")
        path = parts[0]  # ドライブ ("C:") から順に1階層ずつ作る
        for part in parts[1:]:
            if not part:
                continue
            path = f"{path}\\{part}"
            self.vbs(
                'On Error Resume Next: '
                'CreateObject("Scripting.FileSystemObject")'
                f'.CreateFolder("{path}")')

    def configure_waveform_save(self, directory: str, prefix: str,
                                source: str = "AllDisplayed",
                                fmt: str = "Binary",
                                counter: bool = True) -> None:
        """波形ファイル保存の設定 (オシロ本体のローカルディスクへ保存)。

        fmt: 'Binary' (.trc) / 'ASCII' / 'MATLAB' など。
        source: 'AllDisplayed' または 'C1'..'C4'。
        保存先フォルダは存在しなければ自動作成する。
        counter: オシロ内蔵の連番サフィックス (--00000) を使うか。
          ★自動スキャンでは False にして save_waveform_now(title=...) で
          PC 側から連番を明示すること。内蔵カウンタが増えず同名上書きで
          全点のデータを失う事象が実機で発生した (2026-09-07: 2 スキャン
          とも CX--scan--00000.trc しか残らなかった)★
        """
        self.ensure_directory(directory)
        d = directory.replace("\\", "\\\\")
        self.vbs('app.SaveRecall.Waveform.SaveTo = "File"')
        self.vbs(f'app.SaveRecall.Waveform.WaveformDir = "{d}"')
        self.vbs(f'app.SaveRecall.Waveform.TraceTitle = "{prefix}"')
        self.vbs(f'app.SaveRecall.Waveform.WaveFormat = "{fmt}"')
        self.vbs(f'app.SaveRecall.Waveform.SaveSource = "{source}"')
        self.vbs('app.SaveRecall.Waveform.EnableCounterSuffix = '
                 f'{"True" if counter else "False"}')

    def save_waveform_now(self, title: str | None = None) -> None:
        """現在の設定で波形を1回保存する。

        title を指定すると保存直前に TraceTitle を書き換える。
        自動スキャンは点ごとに title="scan--00042" 形式で連番を明示し、
        ファイル名 (例 C2--scan--00042.trc) を PC 側で決定的にする
        (オシロ内蔵カウンタへの依存をなくす)。
        """
        if title is not None:
            self.vbs(f'app.SaveRecall.Waveform.TraceTitle = "{title}"')
        self.vbs("app.SaveRecall.Waveform.SaveFile")
        if self.simulate:
            self._sim_saved += 1

    def count_saved_files(self, directory: str) -> int:
        """オシロ側の保存先フォルダ内のファイル数を返す (保存検証用)。

        自動スキャン中に「保存したはずのファイルが増えているか」を
        点ごとに確認するために使う。確認できない場合は -1 を返す
        (呼び出し側は検証をスキップする)。
        """
        if self.simulate:
            return self._sim_saved
        try:
            resp = self.vbs_query(
                'CreateObject("Scripting.FileSystemObject")'
                f'.GetFolder("{directory}").Files.Count')
            # CHDR OFF なら数値のみだが、念のため末尾トークンを取る
            return int(float(resp.split()[-1]))
        except Exception as exc:
            self._log("WARN", f"保存ファイル数の確認に失敗: {exc}")
            return -1

    def set_autosave(self, mode: str) -> None:
        """トリガー毎の自動保存。mode: 'Off' / 'Fill' / 'Wrap'。

        Fill: ディスクが埋まったら停止 / Wrap: 古いファイルを上書き。
        """
        self.vbs(f'app.SaveRecall.Waveform.AutoSave = "{mode}"')

    def save_panel_setup(self, slot: int) -> None:
        """本体の内部メモリ (1〜6) へパネル設定を保存。"""
        self.write(f"*SAV {slot}")

    def recall_panel_setup(self, slot: int) -> None:
        self.write(f"*RCL {slot}")

    def stop_acquisition(self) -> None:
        """非常停止用: 取得を止め、オートセーブも切る。"""
        try:
            self.set_autosave("Off")
            self.set_trigger_mode("STOP")
        except Exception as exc:
            self._log("ERR", f"オシロ停止失敗: {exc}")

    # ------------------------------------------------------------------
    # シミュレーション
    # ------------------------------------------------------------------
    def _simulate_query(self, cmd: str) -> str:
        if cmd.startswith("*IDN?"):
            return "LECROY,WAVEPRO-SIM,00000,1.0"
        if cmd.upper().startswith("TRMD?"):
            # シミュレーションでは即座に取得完了扱いにする (スキャン動作確認用)
            return "STOP"
        if "PAVA?" in cmd.upper():
            # 疑似振幅。sim_amplitude (devices/sim_world.py) が接続されて
            # いればステージ位置・レーザー状態に応じた値を返し、無ければ
            # 従来どおり 200 mV ± ノイズ
            import random
            if self.sim_amplitude is not None:
                amp = self.sim_amplitude(cmd.split(":")[0].strip().upper())
            else:
                amp = 0.2 + random.uniform(-0.01, 0.01)
            return f"PKPK,{amp:.4E} V,OK"
        return "0"

    def _simulate_screen(self) -> bytes:
        """疑似的なオシロ画面 (PNG) を生成する。GUI 動作確認用。"""
        import numpy as np
        import cv2
        h, w = 480, 800
        img = np.zeros((h, w, 3), dtype=np.uint8)
        # グリッド (8x10 div)
        for i in range(1, 10):
            cv2.line(img, (i * w // 10, 0), (i * w // 10, h), (40, 40, 40), 1)
        for i in range(1, 8):
            cv2.line(img, (0, i * h // 8), (w, i * h // 8), (40, 40, 40), 1)
        # LGAD 応答風の負パルス波形 (時間とともに位相を揺らす)
        t = np.linspace(0, 1, w)
        jitter = 0.02 * np.sin(time.monotonic() - self._sim_t0)
        colors = [(0, 255, 255), (255, 100, 255), (100, 150, 255), (100, 255, 100)]
        for k, color in enumerate(colors):
            center = 0.45 + jitter
            amp = (0.30 - 0.06 * k)
            y = -amp * np.exp(-((t - center) / 0.015) ** 2)
            ypix = (h * (0.35 - y * 1.2)).astype(int)
            pts = np.stack([np.arange(w), ypix], axis=1).reshape(-1, 1, 2)
            cv2.polylines(img, [pts], False, color, 1, cv2.LINE_AA)
        cv2.putText(img, "LeCroy WavePro (SIMULATION)", (16, 28),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1, cv2.LINE_AA)
        ok, buf = cv2.imencode(".png", img)
        return buf.tobytes()
