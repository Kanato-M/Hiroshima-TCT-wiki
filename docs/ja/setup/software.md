# 制御 PC とソフトウェア

測定はすべて制御 PC 上の GUI **laser_gui**（AC-LGAD レーザースキャン制御 GUI）で行います。
このページでは、GUI の導入、機器側の初期設定、起動方法を説明します。

## 制御 PC

| 項目 | 内容 |
| --- | --- |
| ユーザー名 | `epic`（パスワードは問い合わせ先に聞く） |
| GUI フォルダ | `~/workspace/laser_gui_v4/`（日本語 UI 版）、`~/workspace/laser_gui_v4_en/`（英語 UI 版） |
| コードの入手先 | 最新版は GitHub で公開する予定（未公開） |

!!! question "要確認"
    使っている Python 環境（Anaconda / venv など）と、普段の起動方法（コマンドプロンプト、ショートカットなど）。

## GUI の版

| フォルダ | 内容 |
| --- | --- |
| `laser_gui_v4` | 日本語 UI 版 |
| `laser_gui_v4_en` | 英語 UI 版。基本的な機能と数値は同じで、画面の文言が英語。**日本語が読めない人はこちらを使う** |

!!! warning "英語 UI 版は TUNE 最適化が旧仕様"
    2026-10 時点の手元のコードでは、英語 UI 版の TUNE 最適化には [方式]（単一 CH / 隣接 2ch 合計）・許容誤差・精密掃引がなく、
    単一 CH の粗掃引だけです（[TUNE 最適化](../measurement/tune.md)）。隣接 2ch 合計（MIP 相当）で合わせるときは日本語 UI 版を使ってください。

この Wiki の画面写真は、解析ノート matome 作成時（2026-10 改修版）にシミュレーションモードで撮影したものです。
現行版とは一部の表示（TUNE 最適化の [方式] 欄など）が違います。現行版の画面写真は後日差し替える予定です。

## 必要なソフトウェア

制御用の Windows PC（Python 3.10 以上）で、GUI のフォルダに移動して依存パッケージを入れます（初回のみ）。

```bat
pip install -r requirements.txt
```

`requirements.txt` の内容：

| パッケージ | 用途 |
| --- | --- |
| PySide6 | GUI フレームワーク |
| pyvisa, pyvisa-py | オシロスコープ制御（VXI-11 を使う場合） |
| pyserial | PiLAS DX（USB 仮想 COM）、SHRC-203（RS-232C 使用時） |
| opencv-python | フォーカスカメラ |
| numpy | 数値計算 |
| paramiko | データ回収（オシロ → PC、SSH/SFTP） |
| matplotlib, uproot | 簡易解析（図の出力、`.trc` → ROOT 変換） |

!!! tip "NI-VISA は不要"
    LeCroy の VICP プロトコルは `devices/lecroy_scope.py` に純 Python で実装されているので、
    NI-VISA や LeCroy VICP passport は要りません。

## 機器側の初期設定（初回のみ）

装置を新しく組んだとき・機器を交換したときにだけ必要な設定です。通常の測定では済んでいます。

1. **PiLAS DX**：USB 接続には FTDI VCP ドライバが必要です（PiLAS Product Guide 3.3 節）。
   デバイスマネージャで COM 番号を確認します（制御 PC では **COM5**）。
2. **LeCroy オシロスコープ**：オシロ本体の Utilities → Utilities Setup → Remote で制御方式を確認します。
    - **TCPIP (VICP)**（既定）なら `VICP::192.168.0.2::INSTR`
    - **LXI (VXI11)** なら `TCPIP0::192.168.0.2::inst0::INSTR`

    どちらでも、接続時にもう一方へ自動フォールバックします。IP は 192.168.0.2 です。
3. **LeCroy の SSH サーバ**：データ回収はオシロ側 Windows の OpenSSH サーバ (sshd) に接続します。
   2026-09 時点で設定済みです。
4. **SHRC-203**：本体メニューで Ethernet を有効化し、IP 192.168.0.3 を設定します。通信モードは SHOT 互換コマンド、
   ポート番号は本体設定に合わせます（実機は **9004**）。
5. **制御 PC**：IP アドレスを **192.168.0.1** に設定します。

## 環境ごとの設定 `config_local.json` { #config }

COM ポート番号やオシロ側の保存先など PC ごとに違う設定は、`config.py` を書き換えずに、
同じフォルダの `config_local.json` に書きます。書いた項目だけが上書きされます。

```json
{
  "laser_port": "COM5",
  "stage_um_per_pulse": [2.0, 2.0, 2.0],
  "stage_speed_pps": [1000, 10000, 200],
  "scope_save_dir": "C:\\Users\\LeCroyUser\\Desktop\\data",
  "scope_smb_user": "lcrydmin",
  "transfer_dest_dir": "..\\data"
}
```

| キー | 既定値 | 内容 |
| --- | --- | --- |
| `scope_resource` | `VICP::192.168.0.2::INSTR` | オシロの接続先 |
| `scope_save_dir` | `C:\Users\LeCroyUser\Desktop\data` | オシロ本体側の波形保存ベースフォルダ |
| `scope_smb_user` | `lcrydmin` | データ回収の SSH ユーザー名 |
| `transfer_dest_dir` | `..\data` | PC 側の回収先（GUI フォルダからの相対パス可） |
| `stage_host` / `stage_port` | `192.168.0.3` / `9004` | ステージの接続先 |
| `stage_um_per_pulse` | `[2.0, 2.0, 2.0]` | 1 パルスあたりの移動量 [µm]（X, Y, Z）。**Z は未確認** |
| `stage_speed_pps` | `[1000, 10000, 200]` | 起動速度 [pps]、最高速度 [pps]、加減速時間 [ms]。上げすぎると脱調の恐れ |
| `laser_port` | `COM5` | レーザーの COM ポート |
| `camera_index` | `0` | カメラのデバイス番号 |
| `scope_trig_delay` | `-53NS` | 標準の水平オフセット |
| `stage_screen_dir` | `[1, -1]` | ジョグの向き。カメラ画像の（右, 上）へ動かすステージ軸の符号。カメラやステージの向きを変えたら直す |

!!! warning "パスワードはファイルに書かない"
    データ回収の SSH パスワードは保存されず、転送のたびに GUI で入力します。パスワードは装置の管理者に聞いてください。

## 起動

ログと状態ファイルの保存先 `logs/` は **起動したディレクトリからの相対パス** なので、必ず GUI のフォルダで起動します。

```bat
cd %USERPROFILE%\workspace\laser_gui_v4
python main.py              :: 実機モード
python main.py --simulate   :: 全機器をシミュレーション
```

起動すると、モニターの利用可能領域に収まるようにウィンドウが開きます。画面構成は [GUI の画面構成](../measurement/gui-overview.md) を見てください。

## シミュレーションモードで練習する { #simulate }

`python main.py --simulate` で起動すると、全機器が疑似デバイスになり、ウィンドウのタイトルに `[シミュレーションモード]` と出ます。

- 疑似センサー応答（`devices/sim_world.py`）を持ち、電極上で信号ゼロ・合焦で振幅最小・傾いたセンサー・TUNE 依存を再現します。
- 原点出し・フル較正・Z フォーカス・TUNE 最適化・スキャンを最後まで試せます。
- 状態ファイルは `logs_sim/` に保存され、実機用の `logs/` は変わりません。

練習の順番：

1. 各パネルで [接続]（カメラは [開始]）を押す。
2. ステージパネル [操作] タブの [機械原点復帰 (H:W)]。
3. [位置登録] タブの [原点出し開始位置へ]。
4. レーザーパネルで [発振 ON (ld=1)]。
5. [アライメント] タブの [原点出し (Z→Y→X→R:W)]。
6. 自動スキャンパネルで 1D 位置スキャンを実行。

この後の [測定の全体の流れ](../measurement/procedure.md) を、まずシミュレーションモードで一通り試すことを勧めます。

## フォルダ構成

```text
laser_gui_v4/
├── main.py              # エントリポイント
├── config.py            # 設定 (config_local.json で上書き)
├── sensors.py           # センサー / DUT プロファイルの登録辞書
├── scan_geometry.py     # 標準センサーの実測座標と傾き係数 (2026-08-24)
├── devices/             # 機器ドライバ (simulate モード内蔵)
│   ├── pilas_laser.py   #   PiLAS DX
│   ├── shrc203_stage.py #   SHRC-203 + 機械座標の追跡
│   ├── lecroy_scope.py  #   LeCroy (SCPI + VBS、画面取得)
│   ├── scope_scp.py     #   データ回収 (SSH/SFTP)
│   ├── focus_camera.py  #   USB カメラ
│   └── sim_world.py     #   --simulate 用の疑似センサー応答
├── gui/                 # PySide6 パネル群
├── logs/                # 通信ログ・スキャン記録・状態ファイル
├── captures/            # カメラのスナップショット
└── analysis/            # 取得データの簡易解析 (run_analysis.py)
```

PC 側の回収データ（`..\data`）と解析結果（`..\results`）は GUI フォルダの **外**（隣）に置かれます。
日本語版と英語版の GUI でデータを共有するためです。詳しくは [出力ファイル一覧](../reference/files.md)。

回収したデータは NAS にバックアップします（[データ回収と簡易解析](../measurement/data.md#backup)）。

## 測定ログ

機器へ送ったコマンドと応答はすべて GUI 左下の通信ログに表示され、`logs/gui_YYYYMMDD.log` にも残ります。
トラブルのときはまずこのファイルを見てください。
