# -*- coding: utf-8 -*-
"""センサー / DUT プロファイルの編集ダイアログ。

ボードやセンサーを載せ替えたときに、形状 (ピッチ・電極幅・ストリップ長)
と読み出し対応 (オシロ CH ↔ パッド・電極位置) を GUI から登録・切り替え
できるようにする (sensors.py)。既存センサーを開いて「別名で保存」すれば
複製から新しいセンサーを作れる。

センサーの種類 (2026-10-16 追加):
  ストリップ: 従来どおり (ピッチ・電極幅・外枠オフセット・露出長、CH ↔ 電極中心 Y)
  ピクセル:   ピクセルピッチ・ボンディングパッド辺長、CH ↔ ピクセルの (列, 行)。
              基準 CH が (0, 0) で、論理原点はそのパッドの +X/+Y 側の角
              (2026-10-15 のスキャンと同じ座標)。原点出し・フル較正・既定スキャン範囲・
              「ピクセルへ移動」はこの形状から決まる (sensors.py の docstring)

スキャン既定範囲とフル較正の測定点は「形状から自動設定」で形状の値から
導出できる (sensors.derive_scan / derive_align)。個別に手で直してもよい。
"""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog,
                               QDoubleSpinBox, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout)

import sensors


def _spin(value: float, lo: float = -1e6, hi: float = 1e6,
          dec: int = 1) -> QDoubleSpinBox:
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(dec)
    s.setValue(float(value))
    return s


def _ispin(value: int, lo: int = -20, hi: int = 20) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(int(value))
    return s


class SensorDialog(QDialog):
    def __init__(self, registry: sensors.SensorRegistry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.profile = copy.deepcopy(registry.current())
        self.setWindowTitle("センサー / DUT の編集")
        self.setMinimumWidth(600)
        self._build_ui()
        self._load(self.profile)

    # ------------------------------------------------------------------
    def _build_ui(self):
        layout = QVBoxLayout(self)

        top = QGridLayout()
        top.addWidget(QLabel("名前:"), 0, 0)
        self.name_edit = QLineEdit()
        top.addWidget(self.name_edit, 0, 1)
        top.addWidget(QLabel("メモ:"), 1, 0)
        self.note_edit = QLineEdit()
        self.note_edit.setPlaceholderText("ボード名・ダイ ID・注意点など")
        top.addWidget(self.note_edit, 1, 1)
        top.addWidget(QLabel("種類:"), 2, 0)
        self.type_combo = QComboBox()
        for key, label in sensors.SENSOR_TYPES:
            self.type_combo.addItem(label, key)
        self.type_combo.setToolTip(
            "ストリップ: 電極中心 Y を CH ごとに登録。原点 = 外枠エッジ X=0・基準電極中心 Y=0\n"
            "ピクセル: ピクセルの (列, 行) を CH ごとに登録。原点 = 基準パッドの +X/+Y 側の角\n"
            "原点出し・フル較正・既定スキャン範囲・解析の種類 (2dYX / pixel) が切り替わる")
        self.type_combo.currentIndexChanged.connect(self._type_changed)
        top.addWidget(self.type_combo, 2, 1)
        layout.addLayout(top)

        # --- 形状 (ストリップ) ---
        self.geo_box = QGroupBox("形状 [µm] (論理原点 = 外枠エッジ X=0)")
        g = QGridLayout(self.geo_box)
        self.pitch = _spin(500, 1, 1e5)
        self.width = _spin(50, 1, 1e5)
        self.x_off = _spin(632, 0, 1e5)
        self.length = _spin(9318, 1, 1e6)
        self.gap = _spin(98, 0, 1e5)
        for i, (label, w, tip) in enumerate((
                ("ストリップピッチ", self.pitch, "隣接電極中心の間隔"),
                ("電極 (金属) 幅", self.width,
                 "電極直上では信号が消える。ディップ検出とプローブ位置の基準"),
                ("外枠エッジ → 露出開始", self.x_off,
                 "外枠 (GND) エッジからストリップ露出部の読み出し側端までの\n"
                 "距離 (標準センサーは ⓪+①+② = 632 µm)。原点出しは信号\n"
                 "エッジからこの距離だけ外側を X=0 にする"),
                ("ストリップ露出長", self.length,
                 "レーザーが当たるストリップ露出部の長さ (スキャン領域)"),
                ("端ギャップ", self.gap,
                 "ストリップ反対側の端と隣接領域のギャップ"))):
            lab = QLabel(label + ":")
            lab.setToolTip(tip)
            w.setToolTip(tip)
            g.addWidget(lab, i // 2, (i % 2) * 2)
            g.addWidget(w, i // 2, (i % 2) * 2 + 1)
        layout.addWidget(self.geo_box)

        # --- 形状 (ピクセル) ---
        self.pix_box = QGroupBox("ピクセル形状 [µm] (論理原点 = 基準 CH のパッドの +X/+Y 側の角)")
        g = QGridLayout(self.pix_box)
        self.pix_pitch = _spin(500, 1, 1e5)
        self.pad = _spin(100, 1, 1e5)
        for i, (label, w, tip) in enumerate((
                ("ピクセルピッチ", self.pix_pitch, "隣接ピクセル中心 (= パッド中心) の間隔"),
                ("パッド辺長", self.pad,
                 "ボンディングパッド (正方形、金属 = 無信号) の一辺。原点出しの\n"
                 "物差し (Y・X のディップ幅)。2026-10-15 の pixel#3 は 100 µm"))):
            lab = QLabel(label + ":")
            lab.setToolTip(tip)
            w.setToolTip(tip)
            g.addWidget(lab, 0, i * 2)
            g.addWidget(w, 0, i * 2 + 1)
        g.addWidget(QLabel("ピクセル中心 = (−パッド/2 − 列×ピッチ, −パッド/2 − 行×ピッチ)。"
                           "列は −X 方向、行は −Y 方向に増える (基準 CH = 列 0, 行 0)"), 1, 0, 1, 4)
        layout.addWidget(self.pix_box)

        # --- 読み出し ---
        ro = QGroupBox("読み出し (オシロ CH ↔ 電極)")
        g = QGridLayout(ro)
        self.ro_header = {}
        for col, text in enumerate(("CH", "使用", "パッド名",
                                    "電極中心 Y [µm]", "列", "行")):
            lab = QLabel(text)
            g.addWidget(lab, 0, col)
            self.ro_header[text] = lab
        self.ro_rows = {}
        for row, ch in enumerate(sensors.CHANNELS, start=1):
            use = QCheckBox()
            pad = QLineEdit()
            y = _spin(0.0)
            y.setToolTip("基準 CH の電極中心を 0 とした Y 位置\n"
                         "(標準配置は CH 番号が増えるほど Y−)")
            col_spin = _ispin(0)
            row_spin = _ispin(0)
            col_spin.setToolTip("ピクセルの列 (−X 方向に +1)")
            row_spin.setToolTip("ピクセルの行 (−Y 方向に +1)")
            g.addWidget(QLabel(ch), row, 0)
            g.addWidget(use, row, 1)
            g.addWidget(pad, row, 2)
            g.addWidget(y, row, 3)
            g.addWidget(col_spin, row, 4)
            g.addWidget(row_spin, row, 5)
            self.ro_rows[ch] = (use, pad, y, col_spin, row_spin)
        g.addWidget(QLabel("基準 CH:"), 5, 0)
        self.ref_combo = QComboBox()
        self.ref_combo.addItems(sensors.CHANNELS)
        self.ref_combo.setToolTip(
            "原点出しで電極中心 (Y=0) / パッドの角 (原点) を決める CH")
        g.addWidget(self.ref_combo, 5, 1, 1, 2)
        self.fill_btn = QPushButton("ピッチから Y を等間隔に設定")
        self.fill_btn.clicked.connect(self._fill_readout_y)
        g.addWidget(self.fill_btn, 5, 3)
        self.fill_pix_btn = QPushButton("2x2 の標準配置にする")
        self.fill_pix_btn.setToolTip("C1 (0,0), C2 (1,0) = −X 隣, C3 (0,1) = −Y 隣, C4 (1,1) = 対角\n"
                                     "(2026-10-15 の pixel#3 スキャンで実測した配置)")
        self.fill_pix_btn.clicked.connect(self._fill_pixel_layout)
        g.addWidget(self.fill_pix_btn, 5, 4, 1, 2)
        layout.addWidget(ro)

        # --- スキャン既定範囲・較正測定点 ---
        sc = QGroupBox("スキャン既定範囲 / フル較正の測定点 [µm]")
        g = QGridLayout(sc)
        for col, text in ((1, "開始"), (2, "終了"), (3, "ステップ")):
            g.addWidget(QLabel(text), 0, col)
        self.scan_spins = {}
        for row, (label, prefix) in enumerate(
                (("高速軸 Y (横断)", "fast"), ("低速軸 X (長手)", "slow")),
                start=1):
            g.addWidget(QLabel(label + ":"), row, 0)
            for col, key in enumerate(("start", "stop", "step"), start=1):
                lo = 0.1 if key == "step" else -1e6
                spin = _spin(0.0 if key != "step" else 10.0, lo)
                g.addWidget(spin, row, col)
                self.scan_spins[f"{prefix}_{key}"] = spin
        self.x_points_label = QLabel("較正 X 測定点:")
        g.addWidget(self.x_points_label, 3, 0)
        self.x_points_edit = QLineEdit()
        self.x_points_edit.setToolTip(
            "フル較正で電極中心 Y とフォーカス Z を測る X (カンマ区切り)。\n"
            "dY/dX・dZ/dX のフィットに使う")
        g.addWidget(self.x_points_edit, 3, 1, 1, 3)
        self.y_points_label = QLabel("較正 Y 測定点:")
        g.addWidget(self.y_points_label, 4, 0)
        self.y_points_edit = QLineEdit()
        self.y_points_edit.setToolTip(
            "フル較正で測る電極の Y と、その電極を読む CH。\n"
            "「Y:CH」をカンマ区切りで (例: -500:C2, -1500:C4)。\n"
            "dZ/dY のフィットとピッチ確認に使う")
        g.addWidget(self.y_points_edit, 4, 1, 1, 3)
        self.pads_label = QLabel("較正パッド (自動):")
        g.addWidget(self.pads_label, 3, 0)
        self.pads_text = QLabel("")
        self.pads_text.setWordWrap(True)
        self.pads_text.setToolTip("フル較正で測る基準以外の読み出しピクセルのパッド中心 (その CH で読む)。\n"
                                  "読み出し表の列・行から自動で決まる")
        g.addWidget(self.pads_text, 3, 1, 2, 3)
        self.y_probe = _spin(80, 0, 1e4)
        self.x_probe = _spin(50, 0, 1e4)
        self.y_half = _spin(150, 10, 1e4)
        self.probe_labels = []
        for col, (label, w, tip) in enumerate((
                ("Z 測定の Y 離隔", self.y_probe,
                 "Z フォーカスを行う位置の電極中心 / パッド中心からの Y オフセット\n"
                 "(電極横 / パッド横のシリコン上。電極半幅・パッド半幅より十分大きく)"),
                ("X 端検出の Y 離隔", self.x_probe,
                 "X エッジ検出を行う位置の電極中心からの Y オフセット\n"
                 "(ボンドパッドの Y 幅に収まる電極寄りの位置。ピクセルでは使わない)"),
                ("Y 掃引半幅", self.y_half,
                 "電極 / パッドのディップ検出の Y 掃引範囲 (±)。粗寄せ誤差より大きく、\n"
                 "隣の電極 / パッドが入らない程度に"))):
            lab = QLabel(label + ":")
            lab.setToolTip(tip)
            w.setToolTip(tip)
            g.addWidget(lab, 5 + col, 0)
            g.addWidget(w, 5 + col, 1)
            self.probe_labels.append(lab)
        auto_btn = QPushButton("形状から自動設定")
        auto_btn.setToolTip("上の形状・読み出しの値からスキャン範囲と\n"
                            "較正測定点を計算し直す")
        auto_btn.clicked.connect(self._auto_derive)
        g.addWidget(auto_btn, 5, 2, 1, 2)
        layout.addWidget(sc)

        # --- 較正・原点の状態 ---
        self.state_label = QLabel("")
        self.state_label.setWordWrap(True)
        self.state_label.setStyleSheet("color: #546e7a;")
        layout.addWidget(self.state_label)

        btns = QHBoxLayout()
        save_btn = QPushButton("保存")
        save_btn.setDefault(True)
        save_btn.clicked.connect(lambda: self._save(as_new=False))
        new_btn = QPushButton("別名で保存 (新規センサー)")
        new_btn.setToolTip("入力内容を新しいセンサーとして登録する。\n"
                           "傾き係数・登録原点・登録位置は引き継がない")
        new_btn.clicked.connect(lambda: self._save(as_new=True))
        del_btn = QPushButton("このセンサーを削除")
        del_btn.clicked.connect(self._delete)
        cancel_btn = QPushButton("キャンセル")
        cancel_btn.clicked.connect(self.reject)
        btns.addWidget(save_btn)
        btns.addWidget(new_btn)
        btns.addWidget(del_btn)
        btns.addStretch(1)
        btns.addWidget(cancel_btn)
        layout.addLayout(btns)

    # ------------------------------------------------------------------
    def _is_pixel(self) -> bool:
        return self.type_combo.currentData() == "pixel"

    def _type_changed(self):
        pixel = self._is_pixel()
        self.geo_box.setVisible(not pixel)
        self.pix_box.setVisible(pixel)
        self.ro_header["電極中心 Y [µm]"].setVisible(not pixel)
        self.ro_header["列"].setVisible(pixel)
        self.ro_header["行"].setVisible(pixel)
        for use, pad, y, col_spin, row_spin in self.ro_rows.values():
            y.setVisible(not pixel)
            col_spin.setVisible(pixel)
            row_spin.setVisible(pixel)
        self.fill_btn.setVisible(not pixel)
        self.fill_pix_btn.setVisible(pixel)
        for w in (self.x_points_label, self.x_points_edit,
                  self.y_points_label, self.y_points_edit):
            w.setVisible(not pixel)
        for w in (self.pads_label, self.pads_text):
            w.setVisible(pixel)
        self.x_probe.setVisible(not pixel)
        self.probe_labels[1].setVisible(not pixel)
        if pixel:
            self._update_pads_text()

    def _update_pads_text(self):
        try:
            p = self._geometry_profile()
            al = sensors.derive_align(p)
            self.pads_text.setText(
                ", ".join(f"{ch} ({x:.0f}, {y:.0f})" for ch, x, y in al.get("pads", []))
                or "なし (基準以外の読み出し CH がありません)")
        except Exception as exc:          # 入力途中の不整合は表示だけにする
            self.pads_text.setText(f"({exc})")

    # ------------------------------------------------------------------
    def _load(self, p: dict):
        self.name_edit.setText(p.get("name", ""))
        self.note_edit.setText(p.get("note", ""))
        idx = self.type_combo.findData(sensors.sensor_type(p))
        self.type_combo.setCurrentIndex(max(idx, 0))
        self.pitch.setValue(p["pitch_um"])
        self.pix_pitch.setValue(p["pitch_um"])
        self.pad.setValue(p.get("pad_um", 100.0))
        self.width.setValue(p["electrode_width_um"])
        self.x_off.setValue(p["x_frame_offset_um"])
        self.length.setValue(p["strip_length_um"])
        self.gap.setValue(p.get("x_end_gap_um", 0.0))
        for ch, (use, pad, y, col_spin, row_spin) in self.ro_rows.items():
            entry = p["readout"].get(ch, {})
            use.setChecked(bool(entry.get("use", ch in p["readout"])))
            pad.setText(entry.get("pad", ""))
            y.setValue(entry.get("y_um", 0.0))
            col_spin.setValue(int(entry.get("col", 0)))
            row_spin.setValue(int(entry.get("row", 0)))
        self.ref_combo.setCurrentText(p.get("ref_channel", "C1"))
        self._load_scan_align(p)
        self._type_changed()
        dzdx, dzdy, dydx, src = self.registry.coefficients(p["id"]) \
            if p["id"] in self.registry.sensors else (0, 0, 0, "未較正")
        org = p.get("machine_origin")
        org_text = "なし" if not org else \
            "X={:.0f} Y={:.0f} Z={:.0f} µm ({})".format(
                *org["pos_um"], str(org.get("registered_at", ""))[:16])
        self.state_label.setText(
            f"傾き係数: dZ/dX={dzdx * 1000:+.2f}, dZ/dY={dzdy * 1000:+.2f}, "
            f"dY/dX={dydx * 1000:+.2f} µm/mm [{src}]\n"
            f"登録原点 (機械座標): {org_text} / "
            f"登録位置: {len(p.get('positions', {}))} 件")

    def _load_scan_align(self, p: dict):
        for key, spin in self.scan_spins.items():
            spin.setValue(p["scan"][key])
        al = p["align"]
        self.x_points_edit.setText(
            ", ".join(f"{x:g}" for x in al.get("x_points", [])))
        self.y_points_edit.setText(
            ", ".join(f"{y:g}:{ch}" for y, ch in al.get("y_points", [])))
        self.pads_text.setText(
            ", ".join(f"{ch} ({x:.0f}, {y:.0f})" for ch, x, y in al.get("pads", [])))
        self.y_probe.setValue(al.get("y_probe_off_um", 80.0))
        self.x_probe.setValue(al.get("x_probe_off_um", 50.0))
        self.y_half.setValue(al.get("y_half_um", 150.0))

    def _geometry_profile(self) -> dict:
        """入力欄の形状・読み出しを反映したプロファイル (scan/align は旧値)。"""
        p = copy.deepcopy(self.profile)
        p["name"] = self.name_edit.text().strip()
        p["note"] = self.note_edit.text().strip()
        pixel = self._is_pixel()
        p["sensor_type"] = "pixel" if pixel else "strip"
        p["pitch_um"] = self.pix_pitch.value() if pixel else self.pitch.value()
        p["pad_um"] = self.pad.value()
        p["electrode_width_um"] = self.width.value()
        p["x_frame_offset_um"] = self.x_off.value()
        p["strip_length_um"] = self.length.value()
        p["x_end_gap_um"] = self.gap.value()
        ref = self.ref_combo.currentText()
        p["ref_channel"] = ref
        if pixel:
            # 基準 CH を (0, 0) に取り直す (座標の約束: 原点 = 基準パッドの角)
            c0 = self.ro_rows[ref][3].value()
            r0 = self.ro_rows[ref][4].value()
            p["readout"] = {}
            for ch, (use, pad, y, col_spin, row_spin) in self.ro_rows.items():
                col, row = col_spin.value() - c0, row_spin.value() - r0
                p["readout"][ch] = {
                    "pad": pad.text().strip(), "use": use.isChecked(),
                    "col": col, "row": row,
                    "y_um": -p["pad_um"] / 2.0 - row * p["pitch_um"]}
        else:
            # Y は基準 CH の電極中心を 0 に取り直す (座標の約束)
            y_ref = self.ro_rows[ref][2].value()
            p["readout"] = {
                ch: {"pad": pad.text().strip(), "y_um": y.value() - y_ref,
                     "use": use.isChecked(), "col": 0, "row": 0}
                for ch, (use, pad, y, _c, _r) in self.ro_rows.items()}
        return p

    def _fill_readout_y(self):
        ref_idx = sensors.CHANNELS.index(self.ref_combo.currentText())
        for i, ch in enumerate(sensors.CHANNELS):
            self.ro_rows[ch][2].setValue(-(i - ref_idx) * self.pitch.value())

    def _fill_pixel_layout(self):
        for ch, (col, row) in sensors.PIXEL_2X2_LAYOUT.items():
            self.ro_rows[ch][3].setValue(col)
            self.ro_rows[ch][4].setValue(row)
        self.ref_combo.setCurrentText("C1")
        self._update_pads_text()

    def _auto_derive(self):
        p = self._geometry_profile()
        p["scan"] = sensors.derive_scan(p)
        p["align"] = sensors.derive_align(p)
        self._load_scan_align(p)

    def _collect(self) -> dict:
        p = self._geometry_profile()
        p["scan"] = {key: spin.value()
                     for key, spin in self.scan_spins.items()}
        if self._is_pixel():
            al = sensors.derive_align(p)
            al["y_probe_off_um"] = self.y_probe.value()
            al["x_probe_off_um"] = 0.0
            al["y_half_um"] = self.y_half.value()
            p["align"] = al
            if self.y_probe.value() <= self.pad.value() / 2.0:
                raise ValueError("Z 測定の Y 離隔がパッド半幅以下です "
                                 "(パッドの上でフォーカスを測ることになります)")
            if self.y_half.value() <= self.pad.value() / 2.0:
                raise ValueError("Y 掃引半幅がパッド半幅以下です (ディップが範囲に収まりません)")
            sensors.validate(p)
            return p
        try:
            x_points = [float(t) for t in
                        self.x_points_edit.text().split(",") if t.strip()]
        except ValueError:
            raise ValueError("較正 X 測定点は数値をカンマ区切りで入力して"
                             "ください") from None
        y_points = []
        for token in self.y_points_edit.text().split(","):
            if not token.strip():
                continue
            y_text, sep, ch = token.partition(":")
            ch = ch.strip().upper()
            try:
                y = float(y_text)
            except ValueError:
                y = None
            if y is None or not sep or ch not in sensors.CHANNELS:
                raise ValueError(
                    f"較正 Y 測定点の書式が不正です: {token.strip()!r} "
                    "(例: -500:C2)")
            y_points.append([y, ch])
        p["align"] = {
            "x_points": x_points, "y_points": y_points,
            "y_probe_off_um": self.y_probe.value(),
            "x_probe_off_um": self.x_probe.value(),
            "y_half_um": self.y_half.value(),
        }
        if self.y_probe.value() <= self.width.value() / 2.0:
            raise ValueError("Z 測定の Y 離隔が電極半幅以下です "
                             "(電極の上でフォーカスを測ることになります)")
        sensors.validate(p)
        return p

    # ------------------------------------------------------------------
    def _save(self, as_new: bool):
        try:
            p = self._collect()
        except ValueError as exc:
            QMessageBox.warning(self, "入力エラー", str(exc))
            return
        if as_new:
            if p["name"] in (n for _sid, n in self.registry.items()):
                QMessageBox.warning(
                    self, "入力エラー",
                    "同じ名前のセンサーが既にあります。名前を変えてください")
                return
            p["id"] = self.registry.new_id(p["name"])
            p["calibration"] = None
            p["machine_origin"] = None
            p["positions"] = {}
        self.registry.upsert(p, select=True)
        self.accept()

    def _delete(self):
        ret = QMessageBox.question(
            self, "削除確認",
            f"センサー「{self.profile['name']}」を削除しますか?\n"
            "傾き係数・登録原点・登録位置も削除されます。",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ret != QMessageBox.Yes:
            return
        try:
            self.registry.remove(self.profile["id"])
        except ValueError as exc:
            QMessageBox.warning(self, "削除できません", str(exc))
            return
        self.accept()
