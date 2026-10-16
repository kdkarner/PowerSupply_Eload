import threading

import yaml
import pytest

from powsupply import PowerSupply, Channel
from conftest import FakeInstrument


def _first_profile(psu, multichannel):
    for key, prof in psu.config["profiles"].items():
        if bool(prof.get("is_multichannel")) == multichannel and "cmd_set_v" in prof:
            return key
    pytest.skip("No hay perfil de ese tipo en config.yaml")


def _connected(psu, profile, responses=None):
    psu.load_profile_settings(profile)
    psu.psu = FakeInstrument(responses)
    psu.connected = True
    return psu.psu


def test_channel_telemetry_clamps_negative():
    ch = Channel(1)
    ch.update_telemetry(-0.01, 2.0, True)
    assert ch.ReadV == 0.0 and ch.ReadI == 2.0 and ch.ReadPower == 0.0 and ch.ON


def test_loads_profiles_from_config_folder(isolated_config):
    psu = PowerSupply()
    assert psu.config_file.startswith(str(isolated_config))
    assert psu.config["profiles"]


def test_multichannel_profile_creates_all_channels():
    psu = PowerSupply()
    key = _first_profile(psu, True)
    psu.load_profile_settings(key)
    expected = {int(k) for k in psu.get_current_profile()["channels"]}
    assert set(psu.channels) == expected


def test_update_settings_sends_profile_commands():
    psu = PowerSupply()
    key = _first_profile(psu, False)
    inst = _connected(psu, key)
    ok, _ = psu.update_settings(5.0, 1.5)
    prof = psu.get_current_profile()
    assert ok
    assert prof["cmd_set_v"].format(val=5.0) in inst.writes
    assert prof["cmd_set_i"].format(val=1.5) in inst.writes


def test_commands_rejected_when_disconnected():
    psu = PowerSupply()
    assert psu.update_settings(1.0, 1.0)[0] is False
    assert psu.set_output(True)[0] is False


def test_read_telemetry_parses_values():
    psu = PowerSupply()
    key = _first_profile(psu, False)
    psu.load_profile_settings(key)
    prof = psu.get_current_profile()
    _connected(psu, key, {
        prof["cmd_meas_v"]: "12.5\n",
        prof["cmd_meas_i"]: "0.25",
        prof.get("cmd_query_out", "OUTP?"): "1",
    })
    data = psu.read_telemetry()[1]
    assert data["ReadV"] == 12.5 and data["ReadI"] == 0.25 and data["ON"] is True
    assert data["Power"] == pytest.approx(3.125, abs=1e-3)


def test_multichannel_telemetry_restores_active_channel():
    psu = PowerSupply()
    key = _first_profile(psu, True)
    inst = _connected(psu, key)
    psu.channel_num = min(psu.channels)
    psu.read_telemetry()
    sel = psu.get_current_profile()["cmd_select_ch"]
    assert inst.writes[-1] == sel.format(ch=psu.channel_num)


def test_save_config_is_atomic_and_keeps_other_profiles(isolated_config):
    a, b = PowerSupply(), PowerSupply()
    keys = list(a.config["profiles"])[:2]
    a.load_profile_settings(keys[0])
    b.load_profile_settings(keys[1])

    a.ip = "10.0.0.1"
    a.save_config()
    b.ip = "10.0.0.2"
    b.save_config()  # antes: 'b' sobrescribía el YAML con su copia vieja y perdía el cambio de 'a'

    disk = yaml.safe_load((isolated_config / "config.yaml").read_text(encoding="utf-8"))
    assert disk["profiles"][keys[0]]["ip"] == "10.0.0.1"
    assert disk["profiles"][keys[1]]["ip"] == "10.0.0.2"
    assert not (isolated_config / "config.yaml.tmp").exists()


def test_concurrent_saves_keep_yaml_valid(isolated_config):
    instances = [PowerSupply() for _ in range(6)]
    threads = [threading.Thread(target=lambda p=p: [p.save_config() for _ in range(20)]) for p in instances]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    disk = yaml.safe_load((isolated_config / "config.yaml").read_text(encoding="utf-8"))
    assert disk["profiles"]
