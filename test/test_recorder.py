"""Grabación de telemetría: Grabar / Pausa / Detener / Exportar."""
import csv
import os

import pytest

import main
from recorder import DataRecorder, CSV_HEADER, IDLE, RECORDING, PAUSED

SAMPLE = {1: {"ReadV": 12.0, "ReadI": 0.5, "Power": 6.0, "ON": True},
          2: {"ReadV": 5.0, "ReadI": 0.1, "Power": 0.5, "ON": False}}


def _rows(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.reader(f))


# ---------------- DataRecorder ----------------

def test_record_writes_csv_to_data_dir(tmp_path):
    rec = DataRecorder(str(tmp_path))
    assert rec.start() == "started" and rec.state == RECORDING
    assert rec.add_samples("Fuente #1", "psu", "E36155A_1", SAMPLE) == 2
    rec.stop()
    rows = _rows(rec.file_path)
    assert rows[0] == CSV_HEADER
    assert rows[1][2:] == ["Fuente #1", "psu", "E36155A_1", "1", "12.0", "0.5", "6.0", "1"]
    assert rows[2][5] == "2" and rows[2][9] == "0"


def test_pause_skips_samples_and_resume_continues_same_file(tmp_path):
    rec = DataRecorder(str(tmp_path))
    rec.start()
    rec.add_samples("A", "psu", "p", SAMPLE)
    assert rec.pause() and rec.state == PAUSED
    assert rec.add_samples("A", "psu", "p", SAMPLE) == 0
    first_file = rec.file_path
    assert rec.start() == "resumed"
    rec.add_samples("A", "psu", "p", SAMPLE)
    rec.stop()
    assert rec.file_path == first_file and rec.samples == 4
    assert len(_rows(first_file)) == 5


def test_samples_ignored_when_idle(tmp_path):
    rec = DataRecorder(str(tmp_path))
    assert rec.add_samples("A", "psu", "p", SAMPLE) == 0 and not rec.has_data()


def test_data_on_disk_while_recording(tmp_path):
    """Si la app se cierra de golpe, lo grabado ya está en disco."""
    rec = DataRecorder(str(tmp_path))
    rec.start()
    rec.add_samples("A", "psu", "p", SAMPLE)
    assert len(_rows(rec.file_path)) == 3


def test_export_copies_file(tmp_path):
    rec = DataRecorder(str(tmp_path / "rec"))
    rec.start()
    rec.add_samples("A", "psu", "p", SAMPLE)
    rec.stop()
    dest = tmp_path / "out.csv"
    rec.export(str(dest))
    assert _rows(dest) == _rows(rec.file_path)


# ---------------- Toolbar ----------------

@pytest.fixture
def window(qtbot):
    win = main.MultiPSUController()
    qtbot.addWidget(win)
    win.show()
    yield win
    win.record_stop()
    for _, ctrl, _ in win.docks:
        ctrl.disconnect_psu()


def test_buttons_left_of_add_psu_with_icons(window):
    tb = window.toolbar
    order = [tb.widgetForAction(a) for a in tb.actions()]
    idx = {w: i for i, w in enumerate(order) if w is not None}
    for btn in (window.btn_record, window.btn_pause, window.btn_stop, window.btn_export):
        assert idx[btn] < idx[window.btn_add_psu]
        assert not btn.icon().isNull()
    assert idx[window.btn_record] < idx[window.btn_pause] < idx[window.btn_stop] < idx[window.btn_export]


def test_button_states_follow_recording(window):
    assert window.btn_record.isEnabled() and not window.btn_stop.isEnabled() and not window.btn_export.isEnabled()
    window.btn_record.click()
    assert window.recorder.state == RECORDING
    assert not window.btn_record.isEnabled() and window.btn_pause.isEnabled() and window.btn_stop.isEnabled()
    window.btn_pause.click()
    assert window.recorder.state == PAUSED and window.btn_pause.isChecked()
    window.btn_pause.click()  # pulsar de nuevo reanuda
    assert window.recorder.state == RECORDING
    window.btn_stop.click()
    assert window.recorder.state == IDLE and "REC" not in window.lbl_rec_status.text()


def test_telemetry_from_panels_is_recorded(window):
    window.btn_record.click()
    ctrl = window.docks[0][1]
    ctrl.on_telemetry_received(SAMPLE)
    window.btn_stop.click()
    rows = _rows(window.recorder.file_path)
    assert rows[1][2] == window.instrument_name(ctrl) and len(rows) == 3


def test_export_only_after_stop(window, monkeypatch, tmp_path):
    dest = tmp_path / "export.csv"
    monkeypatch.setattr(main.QFileDialog, "getSaveFileName", lambda *a, **k: (str(dest), "CSV (*.csv)"))
    window.btn_record.click()
    window.docks[0][1].on_telemetry_received(SAMPLE)
    assert not window.btn_export.isEnabled()  # grabando: no se exporta
    window.btn_pause.click()
    assert not window.btn_export.isEnabled()  # en pausa: tampoco
    window.btn_stop.click()
    assert window.btn_export.isEnabled()
    window.btn_export.click()
    assert dest.exists() and len(_rows(dest)) == 3
    assert str(dest) in window.log_widget.txt_events.toPlainText()


def test_closing_app_finalizes_recording(window):
    window.btn_record.click()
    window.docks[0][1].on_telemetry_received(SAMPLE)
    path = window.recorder.file_path
    window.close()
    assert window.recorder.state == IDLE and len(_rows(path)) == 3


def test_recordings_go_to_data_dir(window, tmp_path):
    window.btn_record.click()
    assert os.path.dirname(window.recorder.file_path) == str(tmp_path / "recordings")
