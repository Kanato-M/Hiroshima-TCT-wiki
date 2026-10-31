# ソフトウェア

## 役割分担

| ソフトウェア | 役割 |
| --- | --- |
| DAQ（データ取得） | ステージ・電源・オシロスコープを制御し、スキャンを自動化して波形を保存する |
| 解析 | 保存した波形から振幅・電荷・時間情報を抽出し、プロットする |

## DAQ（要記入）

!!! note "TODO"
    研究室で使用している DAQ ソフトウェアについて記入してください。

    - ソフトウェア名・リポジトリの場所：**TODO**
    - 起動方法：**TODO**
    - 出力ファイル形式（例：バイナリ、CSV、ROOT、HDF5）：**TODO**
    - 設定ファイルの書き方：**TODO**

### 計測器制御の例（Python + PyVISA）

多くの計測器は SCPI コマンドで制御できます。以下は高圧電源の電圧を設定する最小例です。
コマンドは機種ごとに異なるので、必ずマニュアルで確認してください。

```python
import pyvisa

rm = pyvisa.ResourceManager()
hv = rm.open_resource("GPIB0::24::INSTR")  # アドレスは環境に合わせて変更
print(hv.query("*IDN?"))

hv.write(":SOUR:FUNC VOLT")
hv.write(":SENS:CURR:PROT 10e-6")  # コンプライアンス 10 µA
hv.write(":SOUR:VOLT:LEV -100")    # バイアス電圧 [V]
hv.write(":OUTP ON")
print(hv.query(":READ?"))
```

!!! warning
    電圧を一気に変えず、必ず段階的に上げ下げする処理を入れてください。

## 解析環境

Python で解析する場合の推奨パッケージ：

```bash
pip install numpy scipy matplotlib pandas
# ROOT ファイルを読む場合
pip install uproot awkward
# HDF5 を読む場合
pip install h5py
```

解析の具体例は [波形解析](../analysis/waveform.md) を参照してください。

## データの管理

- ファイル名には **日付・センサー ID・温度・バイアス電圧・スキャンの種類** を含めると後で困りません。
    - 例：`2026-10-31_SensorA_-20C_bias-scan.h5`
- 測定条件（レーザー強度、アンプ、温度、湿度など）はデータと同じ場所にメタデータとして保存してください。
- 測定ログ（電子ログブックなど）に、ファイル名と測定の目的・気付いたことを記録してください。
