# analysis — 取得データ簡易解析パッケージ

laser_gui_v4 で取得したスキャンデータ (LeCroy シーケンス .trc) の簡易解析と、単一 ROOT ファイルへの変換を行う。

2026-10-05 に、解析ノート **matome** (`laser_test\analysis\results\mip\combined\matome`) の結果を
同じ定義で再現できるように作り直した。スキャンの種類 (1dX / 1dY / 1dZ / 2dYX / tune / 1d_tune / pixel) ごとに
できる解析が違うので、**種類ごとに解析コードを分けている**。

2026-10-16 更新: (1) **ピクセル型センサー** (2x2 ch 読み出し) の種類 `pixel` を追加 (スキャンログの `sensor_type=pixel` で
自動判別)。(2) **2dYX でテンプレートフィット解析を一気通貫で実行**し、論文図 01–09 (`results\paper_plots_20261013` と同じ
内容) を `results\<スキャン名>\paper_plots\` に作る (`macro\template_timing\tt_pipeline.py`。.trc 直読みで ROOT 変換不要)。

- **コード**はこのフォルダ (`laser_gui_v4\analysis\`) に置く
- **データ・出力はリポジトリの外** (英語版 GUI とデータを共有するため):
  - 入力: `<workspace>\data\<年>\<YYYYMMDD>\scan_<日時>_<タグ>\C?--scan--NNNNN.trc`
    とスキャンログ `scan_<日時>.csv` (データフォルダの `logs\`、`laser_gui_v4\logs\` の順に自動検索)
  - 解析結果 (図・CSV・summary・numbers.json): `<workspace>\results\<スキャン名>\`
  - ROOT ファイル: `<スキャンフォルダ>.root` (スキャンフォルダの隣)

依存: numpy, matplotlib, uproot (ROOT 変換と ROOT 出力のみ。無くても簡易解析・テンプレートフィット解析は動く)。
scipy 不要 (ガウスフィットは自前)。テンプレートフィット解析とピクセル解析のコードは `<workspace>\macro\template_timing\`
(日本語版・英語版 GUI で共有) にあり、無ければ簡易解析の図だけになる。

> 数値はすべて解析途中 (preliminary) の内部値。時間分解能はレーザー TRIG OUT 基準で、共通の床
> σ_ref ≈ 12.5 ps を含む。

## 使い方 (GUI)

制御 GUI (`python main.py`) の **「解析」タブ**から実行できる (gui/analysis_panel.py)。
スキャン一覧から選択 → オプション設定 → 「解析開始」。`run_analysis.py` がサブプロセスとして走るので
測定をブロックせず、中断・進捗バー・ログ表示・完了後の図プレビュー・結果フォルダを開く、が使える。
種類は自動判別する (「モード」で手動指定も可)。
「テンプレートフィット解析 (論文図 01–09 / ピクセル pix01–06) も行う」(既定 ON) を外すと簡易解析の図だけになる (`--no-template`)。

## 使い方 (コマンドライン)

```bat
cd laser_gui_v4\analysis

:: 種類を自動判別して解析 (スキャン名でもパスでも可)
python run_analysis.py scan_20260826_155255_1dX
python run_analysis.py ..\..\data\2026\20260824\scan_20260824_195826_1dY

:: 2026-09-07 より前のスキャンは X の原点が違うので、センサー座標へのオフセットを渡す
python run_analysis.py scan_20260824_202544_2dYX --x-off -632

:: 解析 + ROOT 変換 (生波形入り。<scan_dir>.root に保存)
python run_analysis.py scan_20260826_152932_2dYX --root

:: キャッシュ (reduced.npz) を作り直す (テンプレートフィット解析も全段階やり直す)
python run_analysis.py scan_... --force

:: 簡易解析の図だけ (テンプレートフィット解析を省く)
python run_analysis.py scan_... --no-template

:: テンプレートフィット解析だけを直接 (結果は results\<スキャン名>\paper_plots\)
cd ..\..\macro\template_timing
python tt_pipeline.py scan_20261007_000750_2dYX --workers 12
python tt_pixel.py scan_20261015_193055_2dYX --workers 12
```

主なオプション (`--help` 参照):

| オプション | 既定 | 内容 |
|---|---|---|
| `--mode` | 自動 | `1dX` `1dY` `1dZ` `2dYX` `tune` `1d_tune` `pixel` を強制 |
| `--no-template` / `--force-template` | — | 2dYX / pixel のテンプレートフィット解析を省く / 全段階やり直す (`--force` でもやり直す) |
| `--x-off` / `--y-off` | 0 | ログ座標 → センサー座標 (X = 0 が読み出し側の外枠エッジ) のオフセット [µm] |
| `--pitch` | 500 | ストリップピッチ [µm] |
| `--body-x LO HI` | −9900 −700 | 「本体」の X 範囲 [µm]。長さ方向のフィットと代表値に使う (範囲に列が無ければ全列) |
| `--hit-thr` / `--noise-max` / `--tot-thr` | 8 / 4 / 10 mV | hit の下限、品質カットのノイズ上限、ToT 閾値 |
| `--n-boot` | 30 | MPV 誤差のブートストラップ回数 (matome は 100。MPV の値は回数によらない) |
| `--workers` | 8 | 縮約の並列数 |
| `--log` / `--results` | 自動 | ログ CSV・出力先の指定 |

## スキャンの種類と解析項目

matome の解析項目: 1 振幅・電荷 / 2 立ち上がり時間 / 3 ジッター / 4 位置分解能 / 5 時間分解能 /
6 伝搬時間・伝搬速度 / 7 振幅と伝搬。

| 種類 | コード | できる項目 | 出力図 |
|---|---|---|---|
| **1dX** (ストリップに沿う) | `analyze_1dx.py` | 1, 2, 3, 5, 6 (長さ方向: Elmore 遅延 ρc、振幅・立ち上がり・σ_t の傾き) | fig1, fig2, fig3, fig5, fig6 |
| **1dY** (ストリップを横切る) | `analyze_1dy.py` | 1–5, 6 (横方向 v_eff), 7 | fig1–fig5, fig6_7 |
| **1dZ** (フォーカス) | `analyze_1dz.py` | 最適 Z (調整用。解析項目ではない) | fig1_focus, `best_z.txt` |
| **2dYX** (X × Y) | `analyze_2dyx.py` | 1–7 すべて (マップ、ギャップ内、長さ方向) | fig1–fig5, fig6_7 |
| **tune** (位置固定で強度掃引) | `analyze_tune.py` | 振幅依存: MIP 相当 (波高 60–75 mV) の TUNE、1/A 則、σ_t 対振幅 | fig1, fig2, fig3, fig5, fig8 |
| **1d_tune** (位置 × 強度) | `analyze_1d_tune.py` | 1/A 則 (matome の参照値の導出元)、σ_t 対振幅、位置分解能・実効ピッチの強度依存 | fig1, fig2, fig2b, fig3, fig4, fig5 |
| **pixel** (ピクセル 2x2 ch の 2D) | `analyze_pixel.py` → `tt_pixel.py` | 振幅マップ (A_lead, A_total, A_sub, A_lead+A_sub, A_lead/A_total)、振幅分布、立ち上がり、ジッター、2/3/4 ピクセル合成の時間分解能 (位置ごとの最良)、2/3/4 ピクセル重心の位置分解能 (軸ごとの最良)、伝搬 | fig1_pixel_amplitude, fig2_pixel_timing, pix01–06, `pixel\` |

位置分解能と横方向の伝搬速度は Y 方向に走査しないと求まらない (1dX では出ない)。長さ方向の Elmore 遅延は
X 方向の走査が必要 (1dY では出ない)。図の番号は解析項目の番号に合わせてある。

全種類共通の出力:
- `fig_waveforms.png`: 最大振幅点の平均波形・生波形・振幅分布
- `summary.txt`: スキャン条件と主要数値 (キー名は matome の `numbers.json` と同じ。例 `a5_sigma_band_ps`)
- `numbers.json`: 主要数値 (value, err, unit, note)
- `summary.csv`: 点ごとの全量 (ch 別・イベント単位・ペア)
- `mean_waveforms.root`: 点ごとの平均波形と特徴量

### 各図の内容

**2dYX** (`analyze_2dyx.py`)
- fig1 振幅・電荷: A_lead / A_lead+A_sub / Q_tot のマップ、ギャップ 1 つに折り畳んだプロファイル (A_total は参考)、長さ方向
- fig2 立ち上がり: 10–90 % / 40–60 % のマップ、ストリップからの距離依存、長さ方向
- fig3 ジッター: マップ、**Y × イベント毎ジッターの 2D 分布 + 行ごとの MPV**、ギャップ内 (leading / sub-leading)、振幅依存と 1/A 則
- fig4 位置分解能: η 較正曲線、実効ピッチの長さ方向依存、位置分解能 (test) のマップ、**Y × 位置分解能の 2D + MPV** (ノイズ期待値を併記)
- fig5 時間分解能: σ_lead / σ_2strip のマップ、**Y × σ の 2D + MPV**、ギャップ内、長さ方向、振幅依存
- fig6_7 伝搬: 長さ方向の ToA と Elmore フィット、残差、ΔT(u)、v_eff の長さ方向依存、単一 ch の遅延、遅延と振幅 (τ ln(A0/A))

**2dYX のテンプレートフィット解析** (`macro\template_timing\tt_pipeline.py`、2026-10-16): 簡易解析のあとに自動で走り、
`results\<スキャン名>\paper_plots\<スキャン名>\` に論文図 01–09 (PNG + PDF) と `numbers.json`、`paper_plots\key_numbers.csv` を作る。
GUI の図一覧には `paper_NN_*.png` として出る。手順と所要時間: ① `tt_stage1.py` .trc から点ごとのパルステンプレートをフィット
(ToA = フィット関数の CFD50、振幅 = フィットの A; 12 並列で 16000 点 × 2000 セグメント ≈ 30 分) → `timing_template_cfd50\`
② `tt_analyze.py` 時間分解能 (leading / 2 ストリップ各手法 / 位置ごとの最適手法) ③ `tt_poscent.py` 較正なし振幅重心 (A / A² 重み) の
位置分解能 ④ `tt_amp_fluct.py` 振幅分布のガウス平均・σ とノイズの床 ⑤ `tt_paper.py` 図 01–09。2 回目以降は ①〜④ を省いて数分。
- 01 振幅 (A_lead, A_sub, A_lead+A_sub, A_all, 比の σ, 電荷) / 02 振幅分布 / 03 立ち上がり 10–90 %・40–60 % / 04 ジッター /
  05 位置分解能 (重心 A・A² 重み、σ と偏り) / 06 時間分解能 (leading、A² 重み、最適、Δt/√2、振幅依存) / 07 伝搬 vs 位置 /
  08 伝搬 vs 振幅 / 09 2D マップ (振幅、時間分解能、位置分解能)。方法の詳細は `results\paper_plots_20261013\README.md`。

**pixel** (`analyze_pixel.py` + `tt_pixel.py`): パッド (無信号の 100 µm 角) を自動検出して 500 µm 格子に揃え、4 ch をピクセル
中心に割り当てる。時間はイベントごとに振幅順の上位 1/2/3/4 ピクセルを A² 重みで合成 (ch ごとの時刻オフセットをセル内で引いてから)、
位置ごとに近傍プールで最良を採用。位置は上位 2/3/4 ピクセルの振幅重心 (A / A² 重み、較正なし) の x, y 残差 σ。出力は
`results\<スキャン名>\pix01–06_*.png` と `pixel\` (cells.csv, numbers.json, README.md, simple\ = r プロファイル・対角線カット・Y スライス)。

**1dY** (`analyze_1dy.py`): 同じ項目を 1 本の走査で。fig3 は Y × イベント毎ジッターの 2D + MPV (2 定義)、
fig4 は η 曲線と実効ピッチ・位置分解能 (test / in-situ / ノイズ期待値)・残差分布・バイアス、
fig5 は ch 別 σ・σ_lead / σ_Δt/√2 / σ_2strip・振幅依存・床の自己推定。

**1dX** (`analyze_1dx.py`): fig6 が Elmore フィット (ρc, L)・残差・見かけの速度。fig1/2/5 は本体での直線フィットの傾きを出す。

**tune / 1d_tune**: fig3 の 1/A 則は、matome の参照値 (781/A + 5.4 ps) を求めたときと同じ手順
(ch ごとに A ≥ 20 mV を 1/A について重みなしで直線フィットし ch で平均)。

## 解析の規約 (matome と同じ)

| 項目 | 規約 |
|---|---|
| ベースライン・ノイズ | 先頭 20 % のサンプルの平均・標準偏差 |
| 振幅 / 電荷 | ベースライン差引後の最大サンプル / ピーク −3〜+4 ns のゲート積分 (50 Ω) |
| ToA | CFD 50 % の線形補間交差 (ピークから後ろ向きに探す)。原点はセグメントごとの TRIGTIME |
| 立ち上がり / ジッター | 10–90 % と 40–60 % / σ_noise ÷ (dV/dt) (傾きは 40–60 %、10–90 %、CFD50 局所の 3 定義) |
| 品質カット / hit | ノイズ < max(4 mV, 3×全体基準)、飽和・多重パルスなし / 振幅 > max(8 mV, 5×全体基準) |
| 代表値 / 幅 | MPV (KDE ピーク、誤差はブートストラップ) / ±1.5σ コアガウス |
| leading / sub-leading | イベントごとに振幅の大きい順の 1 番目 / 2 番目。「全振幅」は A_lead + A_sub (A_total は無信号 ch の正バイアスを含むので参考) |
| ストリップ脇 / 本体 | ストリップ中心から 25–75 µm で hit 率 ≥ 0.95 / `--body-x` の範囲 |
| 位置分解能 | η = A_B/(A_A+A_B) の較正曲線の逆引き。偶数番イベントで較正し奇数番で評価 (test) |
| 時間分解能 | σ_lead (TRIG OUT 基準)、σ_Δt/√2 (床が相殺)、σ_2strip (A² 重み平均) |
| 伝搬 | 長さ方向: ToA = t0 + ρc(L d − d²/2)、d = −X。横方向: ΔT(u) の傾き = −2/v_eff |

ストリップ中心は振幅プロファイルの半値交点の中点から求める (IR は金属を通らないので中心がへこむ)。
走査範囲の端で求まらない ch はピッチから補う。

## 再現の確認 (2026-10-05)

`scan_20260824_202544_2dYX --x-off -632` (matome の BP) を本パッケージで解析した値と matome の値:

| 量 | 簡易解析 | matome (BP) |
|---|---|---|
| A_lead (ストリップ脇) [mV] | 71.67 | 71.67 |
| A_lead+A_sub のギャップ中央での変化 [%] | −12.07 | −12.07 |
| 立ち上がり 10–90 % (脇) [ps] | 622.9 | 622.9 |
| ジッター 40–60 % (脇) [ps] | 14.66 | 14.66 |
| 実効ピッチ (本体) [µm] | 680.6 | 680.6 |
| 位置分解能 test / ノイズ期待値 [µm] | 11.89 / 10.12 | 11.89 / 10.12 |
| 単ストリップ σ_t (脇) [ps] | 24.01 | 24.01 |
| σ_lead / σ_2strip (ギャップ中央) [ps] | 38.54 / 29.94 | 38.54 / 29.94 |
| Elmore ρc (4 ch 平均) [ps/mm²] | 3.205 | 3.205 |
| v_eff [µm/ps] | 0.916 | 0.919 |
| 遅延 = τ ln(A0/A) の τ [ps] | 319 | 325 |

点ごとの縮約量は matome のキャッシュと中央値で 1e-5 以下の差 (float32 の読み込みによる)。v_eff と τ の 1–2 % の差は
ストリップ中心の求め方 (matome は別解析の中心線を読む) とビンの取り方による。
`scan_20260907_213928_2dYX` (matome の FS、オフセットなし) でも同様に再現する: A_lead 64.8 (matome 64.6) mV、
立ち上がり 764 (763) ps、位置分解能 12.89 (12.90) µm、σ_t 30.7 (30.9) ps、ρc 2.14 (2.12) ps/mm²、v_eff 0.866 (0.868) µm/ps。
`scan_20260707_005415_1dYtune` の 1/A 則は 792/A + 5.3 ps (matome の参照値 781/A + 5.4 ps)。

## 処理の流れと所要時間

1. **scan_data.py** — スキャンフォルダとログ CSV を対応付け (ch・接頭辞の自動検出、追従補正を外した公称座標)
2. **reduce_scan.py** — 事前パス (約 24 点) で全体ノイズ基準と極性を決め、全点を並列に縮約して
   `results\<スキャン名>\reduced.npz` にキャッシュする (ch 別・イベント単位・ペアの量、η ヒストグラム、
   イベント毎ジッター分布、平均波形)。per-segment の値は保存しない
3. **analyze_*.py** — 種類別の図と数値 (reduced.npz だけを読む)
4. **meanwave_root.py** — 平均波形と特徴量を `mean_waveforms.root` に書く (`--no-meanwave-root` で省略)
5. **trc_to_root.py** (任意, `--root`) — 生波形 + 特徴量を単一 ROOT ファイルへ

時間がかかるのは縮約だけで、8 並列で 1 点あたり約 0.03 秒:

| スキャン | 点数 | 縮約 | 図 |
|---|---|---|---|
| 1dY | 101 | 約 12 秒 | 約 3 秒 |
| 1dX (2 ch) | 501 | 約 20 秒 | 約 5 秒 |
| 1dYtune | 610 | 約 25 秒 | 約 5 秒 |
| 2dYX (BP) | 2080 | 約 70 秒 | 約 15 秒 |
| 2dYX (FS) | 14451 | 約 10 分 | 約 30 秒 |

2 回目以降は reduced.npz を使うので図の時間だけで済む。設定 (閾値など) を変えると自動で作り直す。
波形のテンプレートフィットは 2dYX / pixel では `macro\template_timing` のパイプラインとして自動で続けて走る
(上記。省くには `--no-template`)。細分平均波形、leading edge 再解析、端の拡大解析などは `laser_test\analysis\macro\` の本解析を使う。

## ファイル

| ファイル | 内容 |
|---|---|
| `run_analysis.py` | エントリポイント。種類の自動判別と振り分け |
| `analyze_1dx.py` `analyze_1dy.py` `analyze_1dz.py` `analyze_2dyx.py` `analyze_tune.py` `analyze_1d_tune.py` `analyze_pixel.py` | 種類ごとの解析 (2dYX / pixel はテンプレートフィット解析のサブプロセスも起動) |
| `template_pipeline.py` | `macro\template_timing\tt_pipeline.py` (ストリップ) / `tt_pixel.py` (ピクセル) をサブプロセスで呼ぶ |
| `features.py` | セグメントの特徴量抽出と 1 測定点の縮約 (規約の実装) |
| `reduce_scan.py` | 全点の並列縮約とキャッシュ |
| `position.py` | η による位置再構成と位置分解能 |
| `stats.py` | コアガウスフィット、重み付きフィット、Elmore フィット (numpy のみ) |
| `mpv.py` | MPV 推定 (`analysis\src\aclgad_tct\mpv.py` の同期コピー) |
| `plot_common.py` | ROOT スタイル、reduced.npz のアクセサ、幾何 (ストリップ中心・ギャップ)、2D + MPV の図、数値の保存 |
| `scan_data.py` `trc_reader.py` | スキャンフォルダ・ログの読み込み、.trc リーダ |
| `meanwave_root.py` `trc_to_root.py` | ROOT 出力 |

## 平均波形 ROOT (results\<スキャン名>\mean_waveforms.root)

reduced.npz の点ごと・ch ごとの平均波形 (全セグメント平均、ベースライン差し引き済み **mV**) を書き出したもの。
.trc を読まないので数秒で出る。

- `meanwave` ツリー (1 エントリ = 1 測定点): `index, ix/iy/iz/itune` (格子番号), 座標, `t_ns[nsamp]`,
  `c1..c4[nsamp]` (平均波形 mV), `baseline_mv[nch]`, ch 別特徴量 (`amp_mpv_mV, toa_mu_ps, sigma_core_ps, ...`),
  イベント単位の量 (`ev_amp_lead_mpv_mV, ev_q_sum_mpv_pC, ev_sigma_lead_ps, ...`)
- `mw/<CH>/mw_<CH>_X-080_Y+040` : 点ごとの平均波形 TH1D (x: time ns, y: mV。座標タグは公称座標 µm、T は TUNE %)
- `meta/*`: 単位・格子中心・ch 順・量の名前

```cpp
TFile f("mean_waveforms.root");
((TH1D*)f.Get("mw/C3/mw_C3_X-080_Y+040"))->Draw();
meanwave->Draw("c3:t_ns", "ix==6 && iy==7", "l");
```

## ROOT ファイルの中身 (--root, <scan_dir>.root)

- `wave` ツリー (1 エントリ = 1 セグメント): `index, seg, x_um..zn_um, tune_pct, ok, trig_time[nch],
  trig_offset[nch], c1..c4[nsamp]` (生 ADC)
- `points` ツリー (1 エントリ = 1 点): 座標・`gain/offset` 較正・ch 別特徴量 (`features.QUANT` と同じ名前)
- `meta/*`: 較正式・ch 順・スキャンログヘッダ

電圧復元: `V = gain[ch]*raw - offset[ch]`

> **ToA の注意**: シーケンスモードではヘッダ `horiz_offset` を時刻原点に使うと偽ジッター (σ≈29 ps) が乗る。
> 必ず per-segment の `trig_offset` ブランチを使うこと (2026-07 実測で確定、`trc_reader.py` 参照)。

## 注意

- **座標**: 2026-09-07 以降の GUI のログ座標はセンサー座標 (X = 0 が読み出し側の外枠エッジ)。それより前の
  スキャンは `--x-off` で合わせる (例: 2026-08-24 の 2dYX は −632)。合わせないと「本体」の範囲がずれる。
- **オシロ側で平均した波形** (1 ファイル 1 セグメント) は分布が作れないので、MPV・ジッター・σ は出ない
  (振幅などは中央値で代用し、警告を出す)。
- **2D スキャン**は X と Y の組 (2dYX / 2dXY) のみ対応。
- 旧形式の reduced.npz (2026-10-05 より前) は自動で作り直す。
- 英語版 GUI (`laser_gui_v4_en\analysis`) は本フォルダの全訳ミラー。変更したら両方に入れること
  (`macro\template_timing` は共有で、メッセージは日本語)。
- **uproot が使えない環境** (Windows の Smart App Control が有効だと awkward の DLL がブロックされる) でも、
  ROOT 変換・ROOT 出力以外はすべて動く (テンプレートフィット解析は .trc を直接読む)。
