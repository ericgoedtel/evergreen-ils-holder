import os
import stat
from pathlib import Path

import pytest

from evergreen_holder import config


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path


def test_paths_follow_xdg(xdg):
    assert config.config_path() == xdg / "cfg" / "evergreen-holder" / "config.toml"
    assert config.idl_path() == xdg / "cache" / "evergreen-holder" / "fm_IDL.xml"


def test_set_value_creates_file_with_0600(xdg):
    config.set_value("base_url", "https://example.org")
    p = config.config_path()
    assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert stat.S_IMODE(p.parent.stat().st_mode) == 0o700
    assert config.read_raw() == {"base_url": "https://example.org"}


def test_set_value_coerces_org_ids_to_int(xdg):
    config.set_value("branch_id", "501")
    assert config.read_raw()["branch_id"] == 501


def test_set_value_rejects_non_int_org_id(xdg):
    with pytest.raises(config.ConfigError):
        config.set_value("branch_id", "hocutt")


def test_set_value_validates_preferred_format(xdg):
    with pytest.raises(config.ConfigError):
        config.set_value("preferred_format", "vinyl")
    config.set_value("preferred_format", "hardcover")
    assert config.read_raw()["preferred_format"] == "hardcover"


def test_set_value_refuses_password_and_unknown_keys(xdg):
    with pytest.raises(config.ConfigError):
        config.set_value("password", "x")
    with pytest.raises(config.ConfigError):
        config.set_value("favourite_colour", "blue")


def test_set_password_writes_and_preserves_other_keys(xdg):
    config.set_value("username", "eric")
    config.set_password("s3cret")
    raw = config.read_raw()
    assert raw == {"username": "eric", "password": "s3cret"}


def test_read_raw_refuses_loose_permissions(xdg):
    config.set_value("username", "eric")
    os.chmod(config.config_path(), 0o644)
    with pytest.raises(config.ConfigError):
        config.read_raw()


def test_doctor_reports_missing_when_no_file(xdg):
    d = config.doctor()
    assert d["exists"] is False
    assert d["ok"] is False
    assert set(d["missing"]) == set(config.REQUIRED_KEYS)
    assert d["password_set"] is False
    assert d["idl_cached"] is False


def test_doctor_ok_when_complete(xdg):
    for k, v in [("base_url", "https://example.org"), ("branch_id", "501"), ("system_id", "500"),
                 ("consortium_id", "1"), ("username", "eric"), ("preferred_format", "hardcover")]:
        config.set_value(k, v)
    config.set_password("pw")
    config.idl_path().parent.mkdir(parents=True)
    config.idl_path().write_text("<IDL/>")
    d = config.doctor()
    assert d["missing"] == []
    assert d["password_set"] is True
    assert d["idl_cached"] is True
    assert d["mode_ok"] is True
    assert d["ok"] is True


def test_doctor_reports_bad_mode_without_raising(xdg):
    config.set_value("username", "eric")
    os.chmod(config.config_path(), 0o644)
    d = config.doctor()
    assert d["mode_ok"] is False
    assert d["ok"] is False


def test_load_raises_listing_missing_keys(xdg):
    config.set_value("username", "eric")
    with pytest.raises(config.ConfigError) as ei:
        config.load()
    assert "base_url" in str(ei.value)


def test_set_value_rejects_non_https_base_url(xdg):
    with pytest.raises(config.ConfigError):
        config.set_value("base_url", "http://example.org")
    with pytest.raises(config.ConfigError):
        config.set_value("base_url", "example.org")
    config.set_value("base_url", "https://example.org")
    assert config.read_raw()["base_url"] == "https://example.org"


def test_set_value_clears_password_when_base_url_changes(xdg):
    config.set_value("base_url", "https://example.org")
    config.set_password("pw")
    config.set_value("base_url", "https://other.example.org")
    assert "password" not in config.read_raw()


def test_set_value_keeps_password_when_base_url_unchanged(xdg):
    config.set_value("base_url", "https://example.org")
    config.set_password("pw")
    config.set_value("base_url", "https://example.org")
    assert config.read_raw()["password"] == "pw"


def test_set_value_keeps_password_when_other_key_set(xdg):
    config.set_value("base_url", "https://example.org")
    config.set_password("pw")
    config.set_value("username", "eric")
    assert config.read_raw()["password"] == "pw"


def test_set_value_rejects_control_characters(xdg):
    with pytest.raises(config.ConfigError):
        config.set_value("username", "eric\x00")
    with pytest.raises(config.ConfigError):
        config.set_value("base_url", "https://example.org\n")


def test_set_password_rejects_control_characters(xdg):
    with pytest.raises(config.ConfigError):
        config.set_password("s3cret\x07")


def test_read_raw_reports_invalid_toml_as_config_error(xdg):
    config.set_value("username", "eric")
    config.config_path().write_bytes(b"not = valid = toml")
    os.chmod(config.config_path(), 0o600)
    with pytest.raises(config.ConfigError) as ei:
        config.read_raw()
    assert "not valid TOML" in str(ei.value)


def test_doctor_reports_parse_error_without_raising(xdg):
    config.set_value("username", "eric")
    config.config_path().write_bytes(b"not = valid = toml")
    os.chmod(config.config_path(), 0o600)
    d = config.doctor()
    assert d["ok"] is False
    assert "parse_error" in d


def test_write_raw_does_not_leave_tmp_file(xdg):
    config.set_value("username", "eric")
    assert not config.config_path().with_suffix(".tmp").exists()
