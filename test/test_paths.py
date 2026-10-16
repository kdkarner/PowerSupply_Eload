import os

import paths


def test_config_dir_uses_env_override(isolated_config):
    assert paths.get_config_dir() == str(isolated_config)


def test_config_path_seeds_from_default(tmp_path, monkeypatch):
    empty = tmp_path / "user_cfg"
    monkeypatch.setenv("PSU_CONFIG_DIR", str(empty))
    target = paths.config_path("config.yaml")
    assert os.path.exists(target)
    assert os.path.dirname(target) == str(empty)


def test_default_config_dir_contains_shipped_files():
    default = paths.get_default_config_dir()
    for name in ("config.yaml", "translations.json"):
        assert os.path.exists(os.path.join(default, name))


def test_frozen_uses_user_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("PSU_CONFIG_DIR", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(paths.sys, "frozen", True, raising=False)
    assert paths.get_config_dir() == os.path.join(str(tmp_path), paths.APP_NAME)


def test_user_state_is_never_seeded(tmp_path, monkeypatch):
    target = tmp_path / "user_cfg"
    monkeypatch.setenv("PSU_CONFIG_DIR", str(target))
    path = paths.config_path("session.ini", seed=False)
    assert not os.path.exists(path)
