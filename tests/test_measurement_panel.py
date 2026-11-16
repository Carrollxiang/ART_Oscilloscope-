"""
MeasurementPanel 比值行 (Ratio) 离线测试 — offscreen Qt，不触碰硬件。

覆盖: 比值行的控件切换、候选注入、配置往返、默认命名、
      源缺失保留、无效值显示、运行时 spec 转换与发布。
"""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PyQt6.QtWidgets import QApplication, QWidget

from scope.runtime import EventBus, FittedSnapshot
from scope.ui.panels.measurement_panel import MeasurementPanel


BASE_MEASUREMENTS = [
    {"tag": "m1", "name": "CH1_vpp", "channel": 0, "feature": "Vpp",
     "start_ms": 0.0, "end_ms": 500.0},
    {"tag": "m2", "name": "CH2_vpp", "channel": 1, "feature": "Vpp",
     "start_ms": 0.0, "end_ms": 500.0},
]


@pytest.fixture(scope="module")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def panel(qt_app):
    parent = QWidget()
    p = MeasurementPanel(parent, event_bus=None,
                         initial_measurements=list(BASE_MEASUREMENTS))
    yield p
    for row in list(p._rows):
        row.deleteLater()
    parent.deleteLater()


# ── 行模式切换 ───────────────────────────────────────────────────

class TestRatioRowMode:
    def test_ratio_row_hides_channel_and_window(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio")
        assert row.is_ratio()
        assert not row.channel_combo.isEnabled()
        assert row.start_spin.isHidden()
        assert row.end_spin.isHidden()
        assert not row.ratio_num_combo.isHidden()
        assert not row.ratio_den_combo.isHidden()

    def test_scalar_row_keeps_channel_and_window(self, panel):
        row = panel.add_row(name="s", meas_key="Vpp")
        assert not row.is_ratio()
        assert row.channel_combo.isEnabled()
        assert not row.start_spin.isHidden()
        assert row.ratio_num_combo.isHidden()
        assert row.ratio_den_combo.isHidden()


# ── 候选注入 ─────────────────────────────────────────────────────

class TestRatioCandidates:
    def test_candidates_are_scalar_rows_only(self, panel):
        ratio_row = panel.add_row(name="r", meas_key="Ratio")
        extra = panel.add_row(name="extra", channel="CH3", meas_key="Mean")

        tags = [ratio_row.ratio_num_combo.itemData(i)
                for i in range(ratio_row.ratio_num_combo.count())]
        assert tags == ["m1", "m2", extra.stable_tag]
        # 比值行自身不在候选内
        assert ratio_row.stable_tag not in tags

    def test_new_scalar_row_appears_in_candidates(self, panel):
        ratio_row = panel.add_row(name="r", meas_key="Ratio")
        before = ratio_row.ratio_num_combo.count()
        new_row = panel.add_row(name="CH4_mean", channel="CH4", meas_key="Mean")
        assert ratio_row.ratio_num_combo.count() == before + 1
        tags = [ratio_row.ratio_num_combo.itemData(i)
                for i in range(ratio_row.ratio_num_combo.count())]
        assert new_row.stable_tag in tags

    def test_missing_source_kept_and_marked(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio",
                            numerator_tag="m404", denominator_tag="m2")
        assert row.get_numerator_tag() == "m404"
        assert "缺失" in row.ratio_num_combo.currentText()


# ── 配置读/写 ────────────────────────────────────────────────────

class TestRatioConfig:
    def test_get_measurement_specs_exports_ratio_fields(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio",
                            numerator_tag="m1", denominator_tag="m2")
        item = next(s for s in panel.get_measurement_specs()
                    if s["tag"] == row.stable_tag)
        assert item["feature"] == "Ratio"
        assert item["numerator_tag"] == "m1"
        assert item["denominator_tag"] == "m2"

    def test_config_roundtrip(self, panel):
        row = panel.add_row(name="", meas_key="Ratio",
                            numerator_tag="m1", denominator_tag="m2")
        tag = row.stable_tag
        exported = panel.get_measurement_specs()

        panel.set_config(exported)

        restored = next(r for r in panel._rows if r.stable_tag == tag)
        assert restored.is_ratio()
        assert restored.get_numerator_tag() == "m1"
        assert restored.get_denominator_tag() == "m2"

    def test_legacy_config_without_ratio_fields(self, panel):
        """旧配置(无 numerator_tag/denominator_tag)仍可加载。"""
        panel.set_config(list(BASE_MEASUREMENTS))
        assert len(panel._rows) == 2
        for row in panel._rows:
            assert not row.is_ratio()
            assert row.get_numerator_tag() == ""

    def test_default_name_uses_source_names(self, panel):
        row = panel.add_row(name="", meas_key="Ratio",
                            numerator_tag="m1", denominator_tag="m2")
        assert row.get_name() == "CH1_vpp/CH2_vpp"


# ── 运行时 spec 转换 ─────────────────────────────────────────────

class TestRuntimeSpecConversion:
    def test_ratio_item_converted(self):
        spec = MeasurementPanel._runtime_spec_from_item({
            "tag": "m9", "channel": 0, "feature": "Ratio",
            "start_ms": 0.0, "end_ms": 500.0,
            "numerator_tag": "m1", "denominator_tag": "m2",
        })
        assert spec is not None
        assert spec.is_ratio()
        assert (spec.numerator_tag, spec.denominator_tag) == ("m1", "m2")

    def test_incomplete_ratio_item_skipped(self):
        spec = MeasurementPanel._runtime_spec_from_item({
            "tag": "m9", "channel": 0, "feature": "Ratio",
            "start_ms": 0.0, "end_ms": 500.0,
        })
        assert spec is None

    def test_publish_includes_completed_ratio(self, qt_app):
        bus = EventBus()
        bus.register_topic("measurement.specs.changed", maxsize=16)
        queue = bus.subscribe("measurement.specs.changed")
        parent = QWidget()
        panel = MeasurementPanel(parent, event_bus=bus,
                                 initial_measurements=list(BASE_MEASUREMENTS))
        row = panel.add_row(name="", meas_key="Ratio",
                            numerator_tag="m1", denominator_tag="m2",
                            publish=False)
        panel._publish_specs_changed()

        change = None
        while True:
            item = queue.get_nowait()
            if item is None:
                break
            change = item
        assert change is not None
        specs = {s.tag: s for s in change.specs}
        assert row.stable_tag in specs
        assert specs[row.stable_tag].is_ratio()
        parent.deleteLater()


# ── 值显示 ───────────────────────────────────────────────────────

class TestRatioValueDisplay:
    def test_invalid_reason_displayed(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio",
                            numerator_tag="m1", denominator_tag="m2")
        panel.update_from_fitted(
            FittedSnapshot(invalid_reasons={row.stable_tag: "分母接近 0 (0)"})
        )
        assert "无效" in row.value_label.text()
        assert "分母接近 0" in row.value_label.toolTip()

    def test_valid_value_clears_invalid(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio",
                            numerator_tag="m1", denominator_tag="m2")
        panel.update_from_fitted(
            FittedSnapshot(invalid_reasons={row.stable_tag: "分母接近 0"})
        )
        panel.update_from_fitted(
            FittedSnapshot(event_measurements={row.stable_tag: 2.5})
        )
        assert "2.5000" in row.value_label.text()
        assert row.value_label.toolTip() == ""


# ── 反馈对话框: 比值项提示 ──────────────────────────────────────

def test_feedback_dialog_hints_ratio_on_non_dac_chain(qt_app, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from scope.ui.panels.feedback_panel import FeedbackDialog

    hints = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: hints.append(a)),
    )

    dlg = FeedbackDialog(None, measurement_keys=["m3"], ratio_tags={"m3"})
    dlg._meas_key.setCurrentIndex(dlg._meas_key.findData("m3"))
    dlg._chain_combo.setCurrentIndex(0)      # 无链路
    dlg._on_accept()

    assert hints, "非 DAC 链路选比值项应给出提示"
    assert dlg.result_config is not None, "提示不应阻断保存"


def test_feedback_dialog_no_hint_on_dac_chain(qt_app, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from scope.ui.panels.feedback_panel import FeedbackDialog

    hints = []
    monkeypatch.setattr(
        QMessageBox, "information",
        staticmethod(lambda *a, **k: hints.append(a)),
    )

    dlg = FeedbackDialog(None, measurement_keys=["m3"], ratio_tags={"m3"})
    dlg._meas_key.setCurrentIndex(dlg._meas_key.findData("m3"))
    dlg._chain_combo.setCurrentIndex(dlg._chain_combo.findData("dac"))
    dlg._dac_ip.setText("192.168.1.58")
    dlg._on_accept()

    assert hints == []
    assert dlg.result_config is not None
    assert dlg.result_config.target is not None


# ── 选择不被回滚 (回归: 改选择后刷新候选会把选择改回去) ────────────

class TestRatioSelectionStability:
    def test_user_selection_not_rolled_back(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio")
        assert row.ratio_num_combo.count() == 2

        row.ratio_num_combo.setCurrentIndex(1)
        assert row.get_numerator_tag() == "m2"
        assert "m2" in row.ratio_num_combo.currentText()

        row.ratio_num_combo.setCurrentIndex(0)
        assert row.get_numerator_tag() == "m1"

    def test_denominator_selection_persists(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio")
        extra = panel.add_row(name="CH3_vpp", channel="CH3", meas_key="Vpp")

        row.ratio_den_combo.setCurrentIndex(
            row.ratio_den_combo.findData(extra.stable_tag)
        )
        assert row.get_denominator_tag() == extra.stable_tag

    def test_specs_reflect_user_selection(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio")
        row.ratio_num_combo.setCurrentIndex(row.ratio_num_combo.findData("m2"))
        row.ratio_den_combo.setCurrentIndex(row.ratio_den_combo.findData("m1"))

        item = next(s for s in panel.get_measurement_specs()
                    if s["tag"] == row.stable_tag)
        assert (item["numerator_tag"], item["denominator_tag"]) == ("m2", "m1")

    def test_default_denominator_differs_from_numerator(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio")
        assert row.get_numerator_tag() != row.get_denominator_tag()

    def test_rename_source_refreshes_label_keeps_selection(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio")
        row.ratio_num_combo.setCurrentIndex(row.ratio_num_combo.findData("m2"))

        source = next(r for r in panel._rows if r.stable_tag == "m2")
        source.name_edit.setText("CH2_renamed")

        assert row.get_numerator_tag() == "m2"
        assert "CH2_renamed" in row.ratio_num_combo.currentText()

    def test_channel_column_hidden_for_ratio_row(self, panel):
        row = panel.add_row(name="r", meas_key="Ratio")
        assert row.channel_combo.isHidden()
