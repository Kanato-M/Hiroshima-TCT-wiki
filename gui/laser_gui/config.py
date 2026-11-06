# -*- coding: utf-8 -*-
"""GUI 全体の設定。

接続先アドレス・ポートなどの環境依存パラメータをここに集約する。
実験室PCごとの差分は config_local.json (このファイルと同じディレクトリ) に
JSON で書けば、その項目だけ上書きされる。

例: config_local.json
{
    "laser_port": "COM4",
    "stage_um_per_pulse": [0.1, 0.1, 0.1]
}
"""

from __future__ import annotations

import datetime
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path


def _default_serial_port() -> str:
    # OSごとの典型的なシリアルポート名。実際のポートはデバイスマネージャ等で確認して
    # config_local.json で上書きすること。
    return "COM3" if sys.platform.startswith("win") else "/dev/ttyUSB0"


@dataclass
class GuiConfig:
    # ------------------------------------------------------------------
    # LeCroy WavePro オシロスコープ (LAN, SCPI)
    # ------------------------------------------------------------------
    # 既定は VICP (LeCroy 独自プロトコル, port 1861)。LeCroy の Remote 設定の
    # 既定値が「TCPIP (VICP)」のため、これが最も確実 (lecroy_scope.py に
    # ネイティブ実装済みで NI-VISA も VICP passport も不要)。
    # オシロ側を LXI (VXI-11) にしている場合は
    # "TCPIP0::<SCOPE_IP>::inst0::INSTR" を指定する (pyvisa 使用)。
    # 接続時にもう一方の方式へ自動フォールバックする。
    # 2026-10-23: 実験室 LAN を <OLD_SUBNET>.x から <LAB_SUBNET>.x へ変更
    # (PC <PC_IP> / オシロ <SCOPE_IP> / ステージ <STAGE_IP>,
    #  サブネットマスクは全機 255.255.255.0, ゲートウェイ <GATEWAY_IP>)。
    scope_resource: str = "VICP::<SCOPE_IP>::INSTR"
    scope_timeout_ms: int = 10000
    # オシロ本体側の波形保存ベースディレクトリ (オシロのローカルパス)。
    # 実際の保存先はこの下に日付フォルダを掘る: <base>\<年>\<YYYYMMDD>\
    #   手動保存     → ...\<YYYYMMDD>\manual
    #   自動スキャン → ...\<YYYYMMDD>\scan_<日時>_<モード> (スキャン毎に自動作成)
    scope_save_dir: str = "C:\\Users\\<SCOPE_WIN_USER>\\Desktop\\data"
    scope_simulate: bool = False
    # --- データ回収 (オシロ → PC) ---
    # 2026-09-07 に SSH/SFTP 方式へ仕様変更 (devices/scope_scp.py)。
    # scope_smb_user は SSH ログインのユーザー名としても使う (実機:
    # <SCOPE_USER>)。SSH パスワードは保存せず、転送のたびに GUI で入力する。
    # scope_smb_password / scope_smb_root は旧 SMB 方式
    # (devices/scope_files.py, 現在未使用) の名残り。
    scope_smb_user: str = "<SCOPE_USER>"
    scope_smb_password: str = ""
    # scope_save_dir そのものに対応する UNC パス。空なら管理共有経由
    # (\\<オシロIP>\c$\Users\...) を自動使用。管理共有が使えない機体
    # (アクセス拒否: エラー 5) では、オシロ側で scope_save_dir のフォルダを
    # 共有 (例: 共有名 data) し、"\\\\<SCOPE_IP>\\data" を指定する。
    scope_smb_root: str = ""
    # PC 側の回収先。laser_gui からの相対パスも可 (既定はリポジトリ隣の data)
    transfer_dest_dir: str = "..\\data"

    # ------------------------------------------------------------------
    # OptoSigma SHRC-203 3軸ステージコントローラ
    # ------------------------------------------------------------------
    stage_host: str = "<STAGE_IP>"
    stage_port: int = 9004        # SHRC-203 Ethernet ポート (2026-07 実機確認。本体設定に合わせる)
    stage_use_serial: bool = False
    stage_serial_port: str = field(default_factory=_default_serial_port)
    stage_baudrate: int = 9600
    # 1パルスあたりの移動量 [µm/pulse] (X, Y, Z)。
    # 2026-07 実機校正: GUI 100 µm 指定で実際は 200 µm 移動 (=2 µm/pulse)。
    # X/Y で確認済み。★Z 軸は別ステージの可能性があるため実機で要確認★
    stage_um_per_pulse: tuple[float, float, float] = (2.0, 2.0, 2.0)
    # 移動速度 (接続時に D: コマンドで全軸へ適用): (起動速度 S [pps],
    # 最高速度 F [pps], 加減速時間 R [ms])。台形駆動なので長距離は F まで
    # 加速し目標付近で自動減速する = 原点復帰後のセンサー位置への移動を
    # 高速化しつつ停止精度は変わらない。
    # 2 µm/pulse では F=10000 pps = 20 mm/s。
    # ★F を上げすぎると脱調 (位置ずれ) の恐れ。初回は必ず実移動を確認★
    stage_speed_pps: tuple[int, int, int] = (1000, 10000, 200)
    stage_simulate: bool = False

    # ------------------------------------------------------------------
    # NKT Photonics PiLAS DX レーザー (USB 仮想COMポート, 19200 8N1, CR終端)
    # ------------------------------------------------------------------
    # 解析用PCでの実測ポートは COM5 (2026-07 実機確認。当初情報の COM4 から訂正)
    laser_port: str = field(default_factory=lambda: (
        "COM5" if sys.platform.startswith("win") else "/dev/ttyUSB0"))
    laser_baudrate: int = 19200
    laser_simulate: bool = False
    # --- レーザー標準条件 (レーザーパネルの「★ 標準設定を適用 ★」で一括
    #     送信。入力欄の初期値にもなる。接続後は実機の現在値に同期) ---
    # 繰り返し周波数: 1 MHz (2026-11-01 に標準を TCT 推奨の 1.25 MHz から
    # 変更。オシロの水平オフセット -53 ns も 1 MHz 実測で決めた値)
    laser_freq_hz: int = 1_000_000
    # TUNE: manual 50 % = MIP 相当の出力 (隣接 2ch 合計をストリップ中間で
    # DESY ビームテスト 2026 の 64 mV に合わせた値。2026-10-19 の HV 依存
    # 測定も tune 50 % で実施)。auto にするとコントローラの周波数ごとの
    # 校正値になり MIP 相当ではなくなる
    laser_tune_auto: bool = False
    laser_tune_percent: float = 50.0

    # ------------------------------------------------------------------
    # 測定標準条件 (2026-07 AC-LGAD レーザー測定で確定した実機設定)
    # ------------------------------------------------------------------
    # オシロ既定: Trigger Ext/DC/positive/410 mV, 縦軸 50 mV/div +100 mV offset
    # (表示 -300〜+100 mV), 横軸 1 ns/div offset -53 ns (表示 +48〜+58 ns),
    # C1〜C4 DC50Ω
    # ★横軸オフセット (2026-10 変更: -4 ns → -53 ns)★
    #   AC-LGAD 信号はレーザートリガーの約 52 ns 後に来る (1 MHz で実測:
    #   オフセット -53 ns の位置に信号)。旧設定 -4 ns で見えていたのは、
    #   40 MHz (周期 25 ns) では 2 周期後のトリガーに対する同じ信号が
    #   52 − 2×25 = +2 ns に折り返して見えていたため。-53 ns にすれば
    #   繰り返し周波数に関係なく (TCT 推奨の 1.25 MHz でも 40 MHz でも)
    #   同じ窓に信号が入る。
    scope_trig_source: str = "EX"
    scope_trig_coupling: str = "DC"
    scope_trig_slope: str = "POS"
    scope_trig_level_mv: int = 410
    scope_tdiv: str = "1NS"
    scope_trig_delay: str = "-53NS"    # 水平オフセット (TRDL)
    scope_vdiv: str = "50MV"
    scope_ch_offset_mv: int = 100      # 縦軸オフセット (OFST)
    scope_coupling: str = "D50"        # DC 50Ω
    # 読み出しチャンネル対応 (オシロCH → センサーパッド) と形状は
    # センサープロファイル (sensors.py, <log_dir>/sensors.json) で管理する
    # AC-LGAD バイアス設定 (GUIからは制御しない参考値。ステータスバーに表示)
    hv_bias_v: float = -185.0
    lv_bias_v: float = 6.0
    # ジョグボタンの向き: カメラ画像の (右, 上) へ動かすステージ軸の符号
    # (X, Y)。本機は X+ が画像の右、Y+ が画像の下 (= 上へ動かすのは Y−)。
    # カメラやステージの取り付け向きを変えたら config_local.json で上書き
    stage_screen_dir: tuple[int, int] = (1, -1)

    # ------------------------------------------------------------------
    # フォーカスカメラ (USBカメラ, OpenCV)
    # ------------------------------------------------------------------
    camera_index: int = 0
    camera_simulate: bool = False

    # ------------------------------------------------------------------
    # ログ・スナップショット保存先 (制御PC側)
    # ------------------------------------------------------------------
    log_dir: str = "logs"
    capture_dir: str = "captures"

    def dated_save_dir(self) -> str:
        """日付ベースのオシロ側保存ディレクトリ (<base>\\<年>\\<YYYYMMDD>)。"""
        now = datetime.datetime.now()
        return f"{self.scope_save_dir}\\{now:%Y}\\{now:%Y%m%d}"

    @classmethod
    def load(cls, path: str | Path | None = None) -> "GuiConfig":
        """既定値に config_local.json (存在すれば) の内容を上書きして返す。"""
        cfg = cls()
        if path is None:
            path = Path(__file__).parent / "config_local.json"
        path = Path(path)
        if path.exists():
            overrides = json.loads(path.read_text(encoding="utf-8"))
            for key, value in overrides.items():
                if hasattr(cfg, key):
                    # tuple 項目は JSON ではリストになるので変換する
                    if isinstance(getattr(cfg, key), tuple):
                        value = tuple(value)
                    setattr(cfg, key, value)
        cfg._warn_placeholders(path)
        return cfg

    def _warn_placeholders(self, path: Path) -> None:
        """公開版の既定値 (<SCOPE_IP> などのプレースホルダ) が残っていれば警告する。

        IP アドレス・ユーザー名などの実機の値は公開リポジトリに置かず、
        config_local.json (git 管理外) に書く。書き方は
        config_local.example.json と Wiki の内部向けページを参照。
        """
        left = [k for k, v in asdict(self).items()
                if isinstance(v, str) and re.search(r"<[A-Z_]+>", v)]
        if left:
            print(f"[config] {path} に実機の値が設定されていません "
                  f"(config_local.example.json を参照): {', '.join(left)}",
                  file=sys.stderr, flush=True)

    def force_simulate(self) -> None:
        """全デバイスをシミュレーションモードにする (ハード無しでのGUI確認用)。"""
        self.scope_simulate = True
        self.stage_simulate = True
        self.laser_simulate = True
        self.camera_simulate = True
        # シミュレーションの較正結果・センサー登録・ログで実機の状態
        # (logs/sensors.json 等) を汚さないよう保存先を分ける
        self.log_dir = "logs_sim"

    def dump(self) -> str:
        return json.dumps(asdict(self), indent=2, ensure_ascii=False)
