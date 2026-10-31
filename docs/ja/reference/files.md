# 出力ファイル一覧

GUI が作るファイルの一覧です。波形はオシロ本体に、記録と状態は PC の `logs/` に分かれて保存されます。

- `<scope_save_dir>` の既定は `C:\Users\LeCroyUser\Desktop\data`（オシロ本体）
- `<transfer_dest_dir>` の既定は GUI フォルダの隣の `data`（PC）
- `<workspace>` は `data` フォルダの親

| 場所 | ファイル | 内容 |
| --- | --- | --- |
| オシロ | `<scope_save_dir>\<年>\<YYYYMMDD>\scan_<日時>_<タグ>\C<n>--<prefix>--<連番>.trc` | 自動スキャンの波形。1 点・1 CH ごとに 1 ファイル |
| オシロ | `<scope_save_dir>\<年>\<YYYYMMDD>\manual\` | オシロパネル [保存] タブからの手動保存 |
| PC | `logs/scan_<日時>.csv` | スキャンログ（下記） |
| PC | `logs/tuneopt_<日時>.csv` | TUNE 最適化のカーブ（phase、CH ごとの振幅・合計・ばらつき）。ヘッダーに周波数、目標振幅、到達した TUNE |
| PC | `logs/autofocus_<日時>.csv` | [Z フォーカス] の掃引カーブ |
| PC | `logs/alignment_<日時>.csv` | 原点出し・フル較正の全掃引の生データ |
| PC | `logs/sensor_calibration_<センサーID>_<日時>.json` | フル較正と係数の手動入力の履歴 |
| PC | `logs/transfer_<日時>.csv` | データ回収の記録（パス、サイズ、状態、削除の有無） |
| PC | `logs/gui_YYYYMMDD.log` | 通信ログ（全コマンドと応答） |
| PC | `logs/sensors.json` | センサー辞書 |
| PC | `logs/stage_frame.json` | 論理原点の機械座標（R:W を跨ぐ追跡） |
| PC | `captures/snap_<日時>.png` | カメラのスナップショット |
| PC | `<transfer_dest_dir>\<年>\<YYYYMMDD>\scan_<日時>_<タグ>\` | 回収した波形 |
| PC | `<workspace>\results\<スキャン名>\` | 簡易解析の図、`summary.csv`、`summary.txt`、`numbers.json`、`mean_waveforms.root` |
| PC | `<スキャンフォルダ>.root` | `.trc` をまとめた ROOT ファイル（[ROOT 変換も行う] のとき） |

シミュレーションモードでは `logs/` の代わりに `logs_sim/` が使われます。

## 波形ファイルの名前

`C2--scan--00042.trc` のように、**CH**、**prefix**（既定 `scan`）、**5 桁の連番** からなります。
連番は GUI が 0 から振り、スキャンログの `index` 列と必ず一致します。

スキャンごとのフォルダ名 `scan_<日時>_<タグ>` の日時は、PC 側のスキャンログ `scan_<日時>.csv` と同じなので、両者はこれで対応づけます。
タグはモードと軸を表し、`1dY`（Y の 1D）、`2dYX`（高速軸 Y、低速軸 X の 2D）、`1dYtune`、`tune` のようになります。

## スキャンログ `logs/scan_<日時>.csv` { #scan-log }

先頭の 4 行が `#` で始まるコメント、5 行目が列名で、6 行目から 1 点 1 行のデータが続きます。

| 行 | 書式 | 内容 |
| --- | --- | --- |
| 1 | `# AC-LGAD 自動スキャン記録 <開始日時>` | 見出し |
| 2 | `# mode= total_points= segments= settle_s= prefix= save_dir=` | モード（1d、2d、tune、1d_tune）、総点数、セグメント数、整定時間、prefix、オシロ側の保存先 |
| 3 | `# sensor= z_follow= z_points_um=[..]` | センサー ID、Z 追従の方式（`coef_origin`、`coef_current`、`points`、`off`）、実測 Z |
| 4 | `# tilt_correct= rot_correct= dzdx= dzdy= dydx= base_pos_um=(x,y,z)` | Z 追従と回転追従の有無、傾き係数 [µm/µm]、開始時のステージ位置 |
| 5 | `index,x_um,y_um,z_um,tune_pct,status,timestamp` | 列名。`tune_pct` は TUNE を掃引するモードでだけ値が入る |

- カンマを含む 4 行目は、CSV の規則で行全体が二重引用符で囲まれます。
- `x_um`、`y_um`、`z_um` は **傾き補正を適用した後** の論理座標、つまり実際にステージへ指示した目標位置です。
  補正前の公称座標が要るときは、4 行目の係数と基準位置を使って補正を外します（簡易解析はこれを自動で行う）。
- `status` は `ok`、`timeout`（時間内に取得が終わらなかった点）、`aborted` のいずれかです。
