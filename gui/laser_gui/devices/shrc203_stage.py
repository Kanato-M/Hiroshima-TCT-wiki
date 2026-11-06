# -*- coding: utf-8 -*-
"""OptoSigma SHRC-203 3軸ステージコントローラの制御。

SHOT シリーズ互換のテキストコマンドを使用する
(SHRC-203 通信コマンドリファレンス参照):
  H:W          全軸 機械原点復帰
  R:W          全軸 論理原点リセット (現在位置を 0 とする)
  M:n±Pxxxx    軸 n の相対移動量セット (パルス単位) → G: で実行
  A:n±Pxxxx    軸 n の絶対位置セット (パルス単位) → G: で実行
  G:           移動実行
  L:W / L:E    減速停止 / 非常停止
  Q:           現在位置クエリ → "  1000,   2000,      0,K,K,K,R" 形式
  !:           状態クエリ → "R" (Ready) / "B" (Busy)
  D:nSsFfRr    軸 n の速度設定 (S:初速, F:最高速 [pps], R:加減速 [ms])

機械座標の追跡 (2026-10 追加):
  R:W (論理原点リセット) を実行するとコントローラの座標は 0 に戻り、
  機械原点からの位置が分からなくなる。そこで「論理原点の機械座標」
  (machine_offset_um) をこのクラスが追跡する:
    H:W (機械原点復帰) → オフセット = (0,0,0) (座標 = 機械座標)
    R:W               → オフセット += R:W 直前の座標
  値は frame_path の JSON に保存し、GUI を再起動しても引き継ぐ
  (コントローラが電源を保持している限り有効)。JS-301 から M-ORG したり
  コントローラを再起動した場合は GUI から H:W をやり直すこと。

接続は Ethernet (TCP, 既定ポート23) または RS-232C。終端は CRLF。

注意: パルス→µm の換算はステージ機種とマイクロステップ設定に依存する。
GuiConfig.stage_um_per_pulse を実機で必ず校正すること。
"""

from __future__ import annotations

import datetime
import json
import socket
import threading
import time
from pathlib import Path
from typing import Callable, Optional

LogFunc = Optional[Callable[[str, str], None]]

AXIS_NAMES = ("X", "Y", "Z")  # 軸番号 1, 2, 3 に対応


class Shrc203Stage:
    """SHRC-203 のテキストコマンドラッパ (スレッドセーフ)。"""

    def __init__(self, host: str = "<STAGE_IP>", port: int = 9004,
                 use_serial: bool = False, serial_port: str = "COM5",
                 baudrate: int = 9600,
                 um_per_pulse: tuple[float, float, float] = (1.0, 1.0, 1.0),
                 timeout: float = 1.0, simulate: bool = False,
                 log: LogFunc = None, frame_path: str | Path | None = None):
        self.host = host
        self.port = port
        self.use_serial = use_serial
        self.serial_port = serial_port
        self.baudrate = baudrate
        self.um_per_pulse = um_per_pulse
        self.timeout = timeout
        self.simulate = simulate
        self._log = log or (lambda d, t: None)
        self._sock: Optional[socket.socket] = None
        self._ser = None
        self._rxbuf = b""
        self._lock = threading.Lock()
        self.connected = False
        # シミュレーション用の現在位置 [pulse] (_sim_abs は R:W の影響を
        # 受けない機械座標。sim_world が「真の位置」として参照する)
        self._sim_pos = [0, 0, 0]
        self._sim_abs = [0, 0, 0]
        self._sim_busy_until = 0.0
        self.sim_move_s = 0.3   # シミュレーションの 1 移動あたり所要時間
        # 論理原点の機械座標 [µm] (None = 不明)。モジュール docstring 参照
        self.frame_path = Path(frame_path) if frame_path else None
        self.machine_offset_um: Optional[list[float]] = None
        self._homing = False
        self._load_frame()

    # ------------------------------------------------------------------
    # 機械座標の追跡
    # ------------------------------------------------------------------
    def _load_frame(self) -> None:
        # シミュレーションは毎回機械原点 (0,0,0) から始まるので読まない
        if self.simulate or self.frame_path is None \
                or not self.frame_path.exists():
            return
        try:
            data = json.loads(self.frame_path.read_text(encoding="utf-8"))
            off = data.get("machine_offset_um")
            if off is not None and len(off) == 3:
                self.machine_offset_um = [float(v) for v in off]
        except Exception:
            self.machine_offset_um = None

    def _save_frame(self) -> None:
        if self.frame_path is None:
            return
        try:
            self.frame_path.parent.mkdir(parents=True, exist_ok=True)
            self.frame_path.write_text(json.dumps({
                "machine_offset_um": self.machine_offset_um,
                "updated_at": datetime.datetime.now().isoformat(
                    timespec="seconds"),
            }, indent=2), encoding="utf-8")
        except Exception as exc:
            self._log("WARN", f"機械座標オフセットの保存に失敗: {exc}")

    def machine_from_logical(self, pos_um) -> Optional[list[float]]:
        """論理座標 → 機械座標 [µm] (追跡できていなければ None)。"""
        if self.machine_offset_um is None:
            return None
        return [o + p for o, p in zip(self.machine_offset_um, pos_um)]

    def logical_from_machine(self, pos_um) -> Optional[list[float]]:
        """機械座標 → 現在の論理座標 [µm] (追跡できていなければ None)。"""
        if self.machine_offset_um is None:
            return None
        return [p - o for o, p in zip(self.machine_offset_um, pos_um)]

    # ------------------------------------------------------------------
    # 接続管理
    # ------------------------------------------------------------------
    def connect(self) -> None:
        if self.simulate:
            self.connected = True
            self._log("INFO", "[SIM] SHRC-203 接続")
            return
        if self.use_serial:
            import serial
            self._ser = serial.Serial(
                self.serial_port, self.baudrate, bytesize=8, parity="N",
                stopbits=1, timeout=self.timeout, rtscts=True,
            )
            self._log("INFO", f"SHRC-203 接続 (RS-232C {self.serial_port})")
        else:
            hints = (
                "ping が通るのに接続できない場合の確認: "
                "(1) コントローラ本体メニューの Ethernet 設定でポート番号を確認 "
                "(接続欄に <STAGE_IP>:<ポート> で指定可。XPort系なら 10001) "
                "(2) 本体側で Ethernet (リモート) モードが有効か "
                "(3) 他のPC/前回セッションが接続を占有していないか "
                "(TCP は1接続のみ。コントローラ再起動で解放) "
                "(4) ping も通らない場合: 本体の IP/サブネットマスク/ゲートウェイ "
                "(<STAGE_IP> / 255.255.255.0 / <GATEWAY_IP>) と PC 側のマスク "
                "(255.255.255.0) を確認し、本体の Ethernet 設定変更後は電源を "
                "入れ直す ")
            try:
                # コントローラのTCPスタックは応答が遅いことがあるため、
                # I/Oタイムアウトとは別に接続待ちは最低5秒とる
                self._sock = socket.create_connection(
                    (self.host, self.port),
                    timeout=max(self.timeout, 5.0))
            except socket.timeout:
                raise RuntimeError(
                    f"SHRC-203 へのTCP接続がタイムアウトしました "
                    f"({self.host}:{self.port})。ポート {self.port} が"
                    f"応答していません。{hints}")
            except OSError as exc:
                raise RuntimeError(
                    f"SHRC-203 へのTCP接続に失敗しました "
                    f"({self.host}:{self.port}): {exc}。{hints}")
            self._sock.settimeout(self.timeout)
            self._log("INFO", f"SHRC-203 接続 (TCP {self.host}:{self.port})")
        self.connected = True
        # 通信確認: 状態クエリに応答が返るか (TCPが繋がってもコマンドモードや
        # デリミタが違うと無応答になり「接続中なのに操作不能」になるため)
        resp = self.send("!:")
        if not resp:
            self.disconnect()
            raise RuntimeError(
                "SHRC-203 とTCP接続はできましたが、状態クエリ (!:) に応答が"
                "ありません。コントローラの通信設定を確認してください: "
                "(1) コマンドモードが SHOT 互換か (2) デリミタが CRLF か "
                "(3) ポート番号が制御用か (設定用telnetポートに繋がっている可能性)")

    def disconnect(self) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._ser is not None:
            self._ser.close()
            self._ser = None
        self.connected = False
        self._log("INFO", "SHRC-203 切断")

    # ------------------------------------------------------------------
    # 低レベル送受信
    # ------------------------------------------------------------------
    def send(self, cmd: str) -> str:
        """コマンドを送信し 1 行 (CRLF まで) の応答を返す。

        SHOT互換モードでは設定系コマンドは "OK"/"NG" を返す。
        応答が無い設定の場合もあるためタイムアウトは空文字で返す。
        """
        cmd = cmd.strip()
        with self._lock:
            self._log("TX", cmd)
            if self.simulate:
                resp = self._simulate_response(cmd)
            else:
                data = (cmd + "\r\n").encode("ascii")
                if self._ser is not None:
                    self._ser.reset_input_buffer()
                    self._ser.write(data)
                    raw = self._ser.read_until(b"\r\n")
                    resp = raw.decode("ascii", errors="replace").strip()
                elif self._sock is not None:
                    # 前コマンドの遅延応答が残っていると以降の応答が1つずつ
                    # ズレる (実機ログで A:W に Q: の応答が返る事象を確認)。
                    # 送信前に到着済みの残骸を読み捨てて同期を取り直す。
                    self._rxbuf = b""
                    try:
                        self._sock.settimeout(0.0)
                        while self._sock.recv(4096):
                            pass
                    except (BlockingIOError, TimeoutError,
                            socket.timeout, OSError):
                        pass
                    finally:
                        self._sock.settimeout(self.timeout)
                    self._sock.sendall(data)
                    resp = self._read_line_socket()
                else:
                    raise RuntimeError("SHRC-203 が接続されていません")
            self._log("RX", resp if resp else "(no response)")
            return resp

    @staticmethod
    def _strip_telnet_iac(data: bytes) -> bytes:
        """telnet の IAC 制御シーケンス (0xFF + 2バイト) を除去する。

        ポート23 が telnet サーバーの場合、接続直後にオプション交渉
        バイトが混入して応答パースを壊すため取り除く。
        """
        out = bytearray()
        i = 0
        while i < len(data):
            if data[i] == 0xFF and i + 2 < len(data):
                i += 3
            elif data[i] == 0xFF:
                break  # シーケンス途中: 次の受信で続きが来る
            else:
                out.append(data[i])
                i += 1
        return bytes(out)

    def _read_line_socket(self) -> str:
        """ソケットから空でない 1 行を読む (タイムアウト時は途中まで返す)。

        終端は CRLF が標準だが、機器設定により CR のみ / LF のみの場合も
        あるためいずれも行終端として扱う (CRLF 待ちで毎回タイムアウト
        するのを防ぐ)。

        空行は読み飛ばす: 直前の応答の CR と LF が別パケットで届くと、
        送信前の読み捨てをすり抜けた LF だけが次の応答より先に到着し、
        それを「空の 1 行」として即座に返してしまっていた (2026-10-08、
        2D スキャン中に GUI の位置ポーリングとワーカーの Q: が連続した
        瞬間に 0 ms で「Q: 応答なし」となりスキャンが停止)。
        """
        deadline = time.monotonic() + self.timeout
        while True:
            # 前回応答の残り (CR単独終端の後のLF等) や遅延 LF を捨てる
            self._rxbuf = self._rxbuf.lstrip(b"\r\n")
            for term in (b"\r\n", b"\r", b"\n"):
                if term in self._rxbuf:
                    line, _, self._rxbuf = self._rxbuf.partition(term)
                    if line.strip():
                        return line.decode("ascii", errors="replace").strip()
                    break      # 空行: 捨てて続きを読む
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                self._sock.settimeout(max(0.05, remaining))
                chunk = self._sock.recv(4096)
            except socket.timeout:
                break
            finally:
                self._sock.settimeout(self.timeout)
            if not chunk:
                break
            self._rxbuf += self._strip_telnet_iac(chunk)
        line, self._rxbuf = self._rxbuf, b""
        return line.decode("ascii", errors="replace").strip()

    # ------------------------------------------------------------------
    # 座標変換
    # ------------------------------------------------------------------
    def um_to_pulse(self, axis: int, um: float) -> int:
        """µm → パルス変換 (axis: 0=X,1=Y,2=Z)。"""
        return round(um / self.um_per_pulse[axis])

    def pulse_to_um(self, axis: int, pulse: int) -> float:
        return pulse * self.um_per_pulse[axis]

    # ------------------------------------------------------------------
    # 高レベル操作
    # ------------------------------------------------------------------
    def home_all(self) -> str:
        """全軸の機械原点復帰 (起動直後に必須。JS-301 の M-ORG に相当)。

        完了するとコントローラ座標 = 機械座標 (オフセット 0) になる。
        復帰動作の途中で停止された場合は追跡不能 (None) に落とす。
        """
        resp = self.send("H:W")
        self._homing = True
        self.machine_offset_um = [0.0, 0.0, 0.0]
        self._save_frame()
        return resp

    def zero_all(self) -> str:
        """現在位置を論理原点 (0) にリセット。

        R:W 直前の座標を読み、論理原点の機械座標 (machine_offset_um) に
        積算する (機械座標が追跡できている場合のみ)。
        """
        pos = None
        if self.machine_offset_um is not None:
            try:
                pos, _ = self.get_position_um()
            except Exception:
                pos = None
        resp = self.send("R:W")
        if pos is not None:
            self.machine_offset_um = [
                o + p for o, p in zip(self.machine_offset_um, pos)]
        else:
            self.machine_offset_um = None
        self._save_frame()
        return resp

    def move_relative_um(self, axis: int, um: float) -> str:
        """軸 axis (0=X,1=Y,2=Z) を um だけ相対移動して実行。"""
        pulses = self.um_to_pulse(axis, um)
        return self.move_relative_pulse(axis, pulses)

    def move_relative_pulse(self, axis: int, pulses: int) -> str:
        sign = "+" if pulses >= 0 else "-"
        self.send(f"M:{axis + 1}{sign}P{abs(pulses)}")
        return self.send("G:")

    def move_absolute_um(self, x_um: float, y_um: float, z_um: float) -> str:
        """3軸同時の絶対位置移動 (µm 指定)。"""
        parts = []
        for i, um in enumerate((x_um, y_um, z_um)):
            p = self.um_to_pulse(i, um)
            sign = "+" if p >= 0 else "-"
            parts.append(f"{sign}P{abs(p)}")
        self.send("A:W" + "".join(parts))
        return self.send("G:")

    def move_to_um_relative(self, x_um: float, y_um: float, z_um: float) -> str:
        """目標位置 (µm, 論理座標) へ相対移動 (M:W) で到達する。

        現在位置を Q: で読み、目標パルスとの差分を全軸まとめて相対移動
        させる。自動スキャンでは絶対位置移動 (A:W) ではなくこちらを使う
        (2026-07 実機方針: 論理原点 (例: C1 エッジ=0,0) を R:W で決めて
        おき、以降は相対移動で不具合なく動作する)。差分は毎回実位置から
        計算するため丸め誤差は蓄積しない。
        移動中に呼ぶと途中の位置から差分を計算してしまうため、Busy の
        間はエラーにする (手動操作の連打対策)。
        """
        cur, busy = self.get_position_pulse()
        if busy:
            raise RuntimeError("ステージ移動中です。停止してから再実行して"
                               "ください")
        parts = []
        for i, um in enumerate((x_um, y_um, z_um)):
            d = self.um_to_pulse(i, um) - cur[i]
            sign = "+" if d >= 0 else "-"
            parts.append(f"{sign}P{abs(d)}")
        self.send("M:W" + "".join(parts))
        return self.send("G:")

    def _homing_interrupted(self) -> None:
        # 原点復帰の途中で止めると座標が機械原点基準にならない
        if self._homing:
            self._homing = False
            self.machine_offset_um = None
            self._save_frame()

    def stop(self) -> str:
        """全軸 減速停止。"""
        self._homing_interrupted()
        return self.send("L:W")

    def emergency_stop(self) -> None:
        """非常停止 (即時停止)。例外で中断させない。"""
        self._homing_interrupted()
        try:
            self.send("L:E")
        except Exception as exc:
            self._log("ERR", f"ステージ非常停止失敗: {exc}")

    def set_speed(self, slow_pps: int, fast_pps: int, accel_ms: int) -> None:
        """全軸に同一の速度パラメータを設定する。"""
        for n in (1, 2, 3):
            self.send(f"D:{n}S{slow_pps}F{fast_pps}R{accel_ms}")

    def get_position_pulse(self) -> tuple[list[int], bool]:
        """現在位置 [pulse] と Busy 状態を返す。

        Q: 応答例 "   1000,   -200,      0,K,K,K,R"
        末尾の R/B が Ready/Busy を示す。
        """
        # 無応答や別コマンドの遅延応答 ("OK" 等) が混ざった場合は数回
        # 読み直す (通信の一瞬の乱れで長時間スキャンを止めないため)
        last_err = "応答なし"
        for attempt in range(3):
            resp = self.send("Q:")
            fields = [f.strip() for f in resp.split(",")]
            try:
                if not resp:
                    raise ValueError("応答なし")
                positions = []
                for f in fields[:3]:
                    # 符号と数値の間に空白が入る形式 ("-   200") に対応
                    positions.append(int(f.replace(" ", "")))
                if len(fields) < 4:
                    raise ValueError("形式不正")
                break
            except ValueError as exc:
                last_err = f"{exc}: {resp!r}" if resp else "応答なし"
                self._log("WARN", f"Q: の応答が不正 ({last_err})、"
                                  f"再試行 {attempt + 1}/3")
                time.sleep(0.1)
        else:
            raise RuntimeError(f"Q: {last_err}")
        busy = any(f == "B" for f in fields[3:])
        if not busy:
            self._homing = False   # 原点復帰が完了した
        return positions, busy

    def get_position_um(self) -> tuple[list[float], bool]:
        pulses, busy = self.get_position_pulse()
        return [self.pulse_to_um(i, p) for i, p in enumerate(pulses)], busy

    def is_busy(self) -> bool:
        """Busy なら True。応答が "R" (Ready) のときだけ False を返す。

        無応答や別コマンドの遅延応答 (Q: の座標行など) を Ready と
        誤判定すると移動完了前に取得を始めてしまうため、Ready が明示
        されない限り Busy 扱いにする (wait_ready が次の周期で再確認)。
        """
        resp = self.send("!:")
        return not resp.startswith("R")

    def wait_ready(self, timeout: float = 60.0, poll_s: float = 0.1,
                   abort_check=None) -> None:
        """移動完了 (Ready) までブロックして待つ (自動スキャン用)。

        abort_check: callable() -> bool を渡すと True で即座に中断する。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if abort_check is not None and abort_check():
                return
            if not self.is_busy():
                return
            time.sleep(poll_s)
        raise TimeoutError(f"ステージ移動が {timeout} 秒以内に完了しませんでした")

    # ------------------------------------------------------------------
    # シミュレーション応答
    # ------------------------------------------------------------------
    def _simulate_response(self, cmd: str) -> str:
        time.sleep(0.01)
        busy = time.monotonic() < self._sim_busy_until

        if cmd == "Q:":
            state = "B" if busy else "R"
            pos = ",".join(f"{p:10d}" for p in self._sim_pos)
            return f"{pos},K,K,K,{state}"
        if cmd == "!:":
            return "B" if busy else "R"
        if cmd.startswith("M:"):
            self._sim_pending = ("M", cmd[2:])
            return "OK"
        if cmd.startswith("A:"):
            self._sim_pending = ("A", cmd[2:])
            return "OK"
        if cmd == "G:":
            kind, spec = getattr(self, "_sim_pending", (None, ""))
            if kind:
                self._sim_apply_move(kind, spec)
                self._sim_busy_until = time.monotonic() + self.sim_move_s
            return "OK"
        if cmd == "H:W":
            self._sim_pos = [0, 0, 0]
            self._sim_abs = [0, 0, 0]
            self._sim_busy_until = time.monotonic() + 3 * self.sim_move_s
            return "OK"
        if cmd == "R:W":
            self._sim_pos = [0, 0, 0]
            return "OK"
        if cmd.startswith(("L:", "D:")):
            return "OK"
        return "NG"

    def _sim_apply_move(self, kind: str, spec: str) -> None:
        """M:/A: コマンド文字列を解釈してシミュレーション位置を更新する。"""
        import re
        if spec.startswith("W"):
            # 全軸指定 A:W+P100-P200+P0
            terms = [(i, sign, num) for i, (sign, num) in
                     enumerate(re.findall(r"([+-])P(\d+)", spec)[:3])]
        else:
            m = re.match(r"(\d)([+-])P(\d+)", spec)
            terms = [(int(m.group(1)) - 1, m.group(2), m.group(3))] if m \
                else []
        for axis, sign, num in terms:
            val = int(num) * (1 if sign == "+" else -1)
            delta = val - self._sim_pos[axis] if kind == "A" else val
            self._sim_pos[axis] += delta
            self._sim_abs[axis] += delta
