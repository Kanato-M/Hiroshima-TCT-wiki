# 波形解析

!!! info "広島の装置のデータ"
    広島の装置の波形は LeCroy のシーケンス形式 `.trc`（1 点・1 CH ごとに 2000 セグメント）で保存されます。
    取得直後の確認は GUI の [解析] タブで行えます（[データ回収と簡易解析](../measurement/data.md#analysis)）。
    自分で読むときは GUI 付属の `analysis/trc_reader.py`、または [ROOT 変換も行う] で作った ROOT ファイルを使ってください。
    到達時刻の原点には各セグメントのトリガー時刻（TRIGTIME。ROOT では `trig_offset`）を使い、
    ヘッダーの `horiz_offset` は使いません（[時間の基準](../setup/hardware.md#time-reference)）。

このページは一般的な波形解析の考え方とサンプルコードです。

## 基本的な流れ

```mermaid
flowchart LR
    A[生波形] --> B[ベースライン差し引き]
    B --> C[平均化]
    C --> D[特徴量の抽出<br/>振幅・電荷・時間]
    D --> E[スキャン変数ごとに<br/>プロット・フィット]
```

## 前処理

### ベースライン差し引き

トリガー前（レーザー照射前）の区間の平均値をベースラインとして差し引きます。
同じ区間の標準偏差がノイズ（RMS）の目安になります。

### 平均化

レーザーは毎回ほぼ同じ信号を作るので、\(N\) 本の波形を平均するとランダムノイズは \(1/\sqrt{N}\) 倍に減ります。

!!! warning "平均化の注意点"
    - トリガーのジッターが大きいと、平均した波形の立ち上がりがなまります。
    - 1 本ずつの波形のばらつき（ジッター、ゲインの揺らぎ）を調べたいときは、平均せずに個別の波形を保存してください。

## 主な特徴量

| 特徴量 | 定義 | 用途 |
| --- | --- | --- |
| 振幅 | ベースラインからの最大値 | 信号の大きさ、S/N |
| 収集電荷 | 時間窓内の積分 \(Q = \frac{1}{GR}\int V\,dt\) | 全空乏化電圧、ゲイン、CCE |
| 立ち上がり時間 | 振幅の 10% から 90% までの時間 | センサー・読み出しの速さ |
| CFD 時刻 | 振幅の一定割合（例：50%）を横切る時刻 | タイミング |
| S/N | 振幅 / ノイズ RMS | 信号の質 |

**ジッター** はノイズと信号の傾きから見積もることができます。

\[
\sigma_t \approx \frac{\sigma_\text{noise}}{|dV/dt|}
\]

!!! tip "電荷収集効率 (CCE)"
    照射済みセンサーの収集電荷を、同じ条件で測った未照射センサーの収集電荷で割ったものを
    電荷収集効率 (Charge Collection Efficiency, CCE) と呼びます。
    レーザー強度のふらつきを補正するため、基準センサーで強度をモニターすることがあります。

## Python サンプルコード

1 本の波形（時間 `t`、電圧 `v` の配列）から上の特徴量を求める例です。
正極性の信号を仮定しています。負極性の場合は `v` の符号を反転してから使ってください。

```python
import numpy as np


def analyze_waveform(t, v, t_signal=(0.0, 10e-9), n_baseline=200,
                     gain=1.0, r_load=50.0, cfd_fraction=0.5):
    """1 本の波形から基本的な量を求める。

    t : 時間 [s] (1 次元配列)
    v : 電圧 [V] (1 次元配列, 信号は正極性を仮定)
    t_signal : 電荷を積分する時間窓 [s]
    n_baseline : ベースラインに使う先頭のサンプル数
    gain : アンプの利得 (電圧比, 例: 40 dB なら 100)
    """
    baseline = np.mean(v[:n_baseline])
    noise = np.std(v[:n_baseline])
    v = v - baseline

    i_max = np.argmax(v)
    amplitude = v[i_max]

    # 収集電荷: Q = ∫V dt / (G R)
    window = (t >= t_signal[0]) & (t <= t_signal[1])
    charge = np.trapezoid(v[window], t[window]) / (gain * r_load)

    def crossing_time(level):
        """立ち上がりで level を横切る時刻を線形補間で求める。"""
        idx = np.where(v[:i_max + 1] < level)[0]
        if len(idx) == 0:
            return np.nan
        j = idx[-1]
        return t[j] + (level - v[j]) * (t[j + 1] - t[j]) / (v[j + 1] - v[j])

    t10 = crossing_time(0.1 * amplitude)
    t90 = crossing_time(0.9 * amplitude)

    return {
        "baseline": baseline,
        "noise_rms": noise,
        "amplitude": amplitude,
        "snr": amplitude / noise if noise > 0 else np.inf,
        "charge": charge,
        "rise_time": t90 - t10,
        "t_cfd": crossing_time(cfd_fraction * amplitude),
    }
```

!!! note
    `np.trapezoid` は NumPy 2.0 以降の関数です。古いバージョンでは `np.trapz` を使ってください。

### 使い方の例

```python
import numpy as np
import matplotlib.pyplot as plt

# 例: CSV (1 列目: 時間 [s], 2 列目: 電圧 [V]) を読み込む
data = np.loadtxt("waveform.csv", delimiter=",")
t, v = data[:, 0], data[:, 1]

result = analyze_waveform(t, v, t_signal=(0.0, 10e-9), gain=100)
print(f"amplitude = {result['amplitude'] * 1e3:.1f} mV")
print(f"charge    = {result['charge'] * 1e15:.2f} fC")
print(f"rise time = {result['rise_time'] * 1e12:.0f} ps")

plt.plot(t * 1e9, v * 1e3)
plt.xlabel("Time [ns]")
plt.ylabel("Voltage [mV]")
plt.show()
```

## バイアススキャンの解析例

収集電荷をバイアス電圧に対してプロットし、電荷が飽和しはじめる電圧から全空乏化電圧を求めます。
よく使われるのは、立ち上がり部分と飽和部分をそれぞれ直線でフィットし、その交点をとる方法です。

## フォーカススキャンの解析例

```python
from scipy.optimize import curve_fit
from scipy.special import erf


def edge(x, a, x0, sigma, c):
    return 0.5 * a * (1 + erf((x - x0) / (np.sqrt(2) * sigma))) + c

# x: 位置 [µm], q: 各位置の収集電荷
popt, pcov = curve_fit(edge, x, q, p0=[q.max(), x.mean(), 5.0, 0.0])
sigma = abs(popt[2])
print(f"sigma = {sigma:.2f} um, FWHM = {2.355 * sigma:.2f} um")
```

これを焦点方向 \(z\) の各位置で行い、\(\sigma\) が最小になる \(z\) を求めます。
エッジの向きによって \(\sigma\) が負になることがあるので、絶対値をとっています。
