import json

from PyQt6.QtCore import QByteArray

from config import LanguageManager, AppConfigManager


def test_translations_have_same_keys_in_all_languages():
    lang = LanguageManager()
    keys = {code: set(d) for code, d in lang.translations.items()}
    reference = keys["en"]
    for code, k in keys.items():
        assert k == reference, f"Claves distintas en '{code}': {k ^ reference}"


def test_get_formats_and_falls_back_to_key():
    lang = LanguageManager()
    lang.current_lang = "en"
    assert lang.get("plot_title", index=3).endswith("(#3)")
    assert lang.get("clave_inexistente") == "clave_inexistente"


def test_missing_translations_file_is_recreated(tmp_path):
    path = tmp_path / "tr.json"
    lang = LanguageManager(str(path))
    assert path.exists()
    assert "es" in json.loads(path.read_text(encoding="utf-8"))
    assert lang.get("btn_connect")


def test_session_roundtrip(isolated_config, qapp):
    mgr = AppConfigManager()
    states = [{"dock_object_name": "PSUDock_1", "state": {"profile": "E36155A_1", "port": 5025}}]
    mgr.save_session("fr", QByteArray(b"geo"), QByteArray(b"state"), states, "HDMI-1:1920x1080@1.00")
    mgr.sync()

    assert (isolated_config / "session.ini").exists()
    session = AppConfigManager().load_session()
    assert session["psu_states"] == states
    assert session["screen_signature"] == "HDMI-1:1920x1080@1.00"
    assert AppConfigManager().get_language() == "fr"


def test_load_session_empty_returns_none(qapp):
    assert AppConfigManager().load_session() is None
