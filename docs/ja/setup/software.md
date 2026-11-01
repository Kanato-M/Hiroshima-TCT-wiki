# 制御 PC とソフトウェア

測定はすべて制御 PC 上の GUI **laser_gui**（AC-LGAD レーザースキャン制御 GUI）で行います。
このページでは、GUI の導入、機器側の初期設定、起動方法を説明します。

## 制御 PC

| 項目 | 内容 |
| --- | --- |
| ユーザー名・パスワード | [内部情報](../internal.md#accounts) を見る（パスワードは問い合わせ先に聞く） |
| GUI フォルダ | `~/workspace/laser_gui/`（日本語 UI 版）、`~/workspace/laser_gui_en/`（英語 UI 版） |
| コードの入手先 | 最新版は GitHub で公開する予定（未公開） |
| Python | 最新版 |

## GUI の版 { #versions }

この Wiki は **2026-11-01 版** に合わせて書いています。
2026-11-01 に、制御 PC の GUI フォルダ名を `laser_gui_v4` / `laser_gui_v4_en` から **`laser_gui` / `laser_gui_en`** に変えました。古いメモの `laser_gui_v4` は読み替えてください。

| フォルダ | 内容 |
| --- | --- |
| `laser_gui` | 日本語 UI 版 |
| `laser_gui_en` | 英語 UI 版。**2026-11-01 版で日本語版と同じ仕様にそろえた**（機能・既定値は同一で、画面の文言だけが英語）。日本語が読めない人はこちらを使う |

主な改修の履歴：

| 版 | 主な変更 |
| --- | --- |
| 2026-11-01 | レーザーの標準条件（1 MHz、TUNE manual 50 % = MIP 相当）を既定値にし、レーザーパネルに [★ 標準設定を適用 ★] ボタンを追加。英語版を日本語版と同じ仕様にそろえた |
| 2026-10-16（v4） | ピクセル型センサーに対応（センサーの種類、ピクセルの原点出し・フル較正・スキャン範囲・解析）。2dYX のテンプレートフィット解析（論文図）を [解析] タブから実行できるようにした |
| 2026-10（v2） | センサー辞書、原点の自動登録、原点出しの精密仕上げ+検証、Z 追従の 3 方式、TUNE 最適化の方式選択など |

この Wiki の画面写真は、GUI 付属の `docs/make_screenshots.py` がシミュレーションモードで自動生成したものです（2026-11-01 版）。
波形とカメラ画像は疑似データで、接続先などの内部情報は塗りつぶしています。

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

## 機器側の初期設定（初回のみ） { #device-setup }

装置を新しく組んだとき・機器を交換したときにだけ必要な設定です。通常の測定では済んでいます。

1. **PiLAS DX**：USB 接続には FTDI VCP ドライバが必要です（PiLAS Product Guide 3.3 節）。
   デバイスマネージャで COM 番号を確認します（制御 PC では **COM5**）。
2. **LeCroy オシロスコープ**：オシロ本体の Utilities → Utilities Setup → Remote で制御方式を確認します。
    - **TCPIP (VICP)**（既定）なら `VICP::<オシロの IP>::INSTR`
    - **LXI (VXI11)** なら `TCPIP0::<オシロの IP>::inst0::INSTR`

    どちらでも、接続時にもう一方へ自動フォールバックします。IP は [内部情報](../internal.md#network) にあります。
3. **LeCroy の SSH サーバ**：データ回収はオシロ側 Windows の OpenSSH サーバ (sshd) に接続します。
   2026-09 時点で設定済みです。
4. **SHRC-203**：本体メニューで Ethernet を有効化し、IP を設定します（[内部情報](../internal.md#network)）。通信モードは SHOT 互換コマンド、
   ポート番号は本体設定に合わせます。
5. **制御 PC**：機器側 LAN の IP アドレス・ゲートウェイを設定します（[内部情報](../internal.md#network)）。
   **サブネットマスクは 255.255.255.0** にします。255.255.255.255 にすると、同じセグメントの機器にも直接届かずルーター経由になり、
   ステージ（SHRC-203）が応答しません。SHRC-203 本体の IP・マスク・ゲートウェイを変えたときは、電源を入れ直すと有効になります。

## 環境ごとの設定 `config_local.json` { #config }

COM ポート番号やオシロ側の保存先など PC ごとに違う設定は、`config.py` を書き換えずに、
同じフォルダの `config_local.json` に書きます。書いた項目だけが上書きされます。

```json
{
  "laser_port": "COM5",
  "stage_um_per_pulse": [2.0, 2.0, 2.0],
  "stage_speed_pps": [1000, 10000, 200],
  "scope_save_dir": "C:\\Users\\LeCroyUser\\Desktop\\data",
  "scope_smb_user": "<SSH ユーザー>",
  "transfer_dest_dir": "..\\data"
}
```

| キー | 既定値 | 内容 |
| --- | --- | --- |
| `scope_resource` | `VICP::<オシロの IP>::INSTR` | オシロの接続先 |
| `scope_save_dir` | `C:\Users\LeCroyUser\Desktop\data` | オシロ本体側の波形保存ベースフォルダ |
| `scope_smb_user` | `<SSH ユーザー>` | データ回収の SSH ユーザー名 |
| `transfer_dest_dir` | `..\data` | PC 側の回収先（GUI フォルダからの相対パス可） |
| `stage_host` / `stage_port` | `<ステージの IP>` / `<ポート>` | ステージの接続先 |
| `stage_um_per_pulse` | `[2.0, 2.0, 2.0]` | 1 パルスあたりの移動量 [µm]（X, Y, Z）。実機確認済み |
| `stage_speed_pps` | `[1000, 10000, 200]` | 起動速度 [pps]、最高速度 [pps]、加減速時間 [ms]。上げすぎると脱調の恐れ |
| `laser_port` | `COM5` | レーザーの COM ポート |
| `camera_index` | `0` | カメラのデバイス番号 |
| `scope_trig_delay` | `-53NS` | 標準の水平オフセット |
| `laser_freq_hz` | `1000000` | レーザーの標準の繰り返し周波数（[★ 標準設定を適用 ★] で送る値） |
| `laser_tune_auto` / `laser_tune_percent` | `false` / `50.0` | 標準の TUNE（manual 50 % = MIP 相当） |
| `stage_screen_dir` | `[1, -1]` | ジョグの向き。カメラ画像の（右, 上）へ動かすステージ軸の符号。カメラやステージの向きを変えたら直す |

!!! warning "パスワードはファイルに書かない"
    データ回収の SSH パスワードは保存されず、転送のたびに GUI で入力します。パスワードは装置の管理者に聞いてください。

## 起動

ログと状態ファイルの保存先 `logs/` は **起動したディレクトリからの相対パス** なので、必ず GUI のフォルダで起動します。

制御 PC ではターミナルを開いて、次のように起動します（英語 UI 版は `laser_gui_en`）。

```bash
cd workspace/laser_gui
python ./main.py              # 実機モード
python ./main.py --simulate   # 全機器をシミュレーション
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

制御 PC の `~/workspace/` は次のようになっています。

```text
workspace/
├── laser_gui/           # GUI（日本語 UI 版）
├── laser_gui_en/        # GUI（英語 UI 版）
├── data/                # オシロから回収した波形 (<年>/<YYYYMMDD>/scan_...)
├── macro/               # テンプレートフィット解析・ピクセル解析 (template_timing/)
└── results/             # 解析結果 (<スキャン名>/)
```

GUI フォルダの中身は次のとおりです（英語 UI 版も同じ構成）。

```text
laser_gui/
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

PC 側の回収データ（`..\data`）、解析結果（`..\results`）、テンプレートフィット解析のコード（`..\macro`）は GUI フォルダの **外**（隣）に置かれます。
日本語版と英語版の GUI でデータを共有するためです。詳しくは [出力ファイル一覧](../reference/files.md)。

回収したデータは NAS にバックアップします（[データ回収と簡易解析](../measurement/data.md#backup)）。

## 測定ログ

機器へ送ったコマンドと応答はすべて GUI 左下の通信ログに表示され、`logs/gui_YYYYMMDD.log` にも残ります。
トラブルのときはまずこのファイルを見てください。
