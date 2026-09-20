import json
from pathlib import Path

import pytest

from evergreen_holder import cli_config, cli_search, config


@pytest.fixture
def xdg(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path


@pytest.fixture
def full_config(xdg, monkeypatch):
    # avoid the network fetch that `set base_url` triggers
    monkeypatch.setattr(cli_config, "fetch_idl", lambda base_url, dest: dest.parent.mkdir(parents=True, exist_ok=True) or dest.write_text("<IDL/>"))
    for k, v in [("base_url", "https://example.org"), ("branch_id", "501"), ("system_id", "500"),
                 ("consortium_id", "1"), ("username", "eric"), ("preferred_format", "hardcover")]:
        assert cli_config.main(["set", k, v]) == 0
    config.set_password("pw")


def out(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def test_doctor_exit_1_when_missing(xdg, capsys):
    assert cli_config.main(["doctor"]) == 1
    d = out(capsys)
    assert d["ok"] is False and "base_url" in d["missing"]


def test_set_then_doctor_ok(full_config, capsys):
    assert cli_config.main(["doctor"]) == 0
    assert out(capsys)["ok"] is True


def test_set_base_url_fetches_idl(xdg, monkeypatch, capsys):
    called = {}
    monkeypatch.setattr(cli_config, "fetch_idl", lambda base_url, dest: called.update(url=base_url, dest=dest))
    assert cli_config.main(["set", "base_url", "https://example.org"]) == 0
    assert called["url"] == "https://example.org"
    assert called["dest"] == config.idl_path()


def test_set_password_via_set_is_refused(xdg, capsys):
    assert cli_config.main(["set", "password", "x"]) == 1
    assert out(capsys)["error"] == "config"


def test_search_orgs(full_config, fake_client, monkeypatch, capsys):
    monkeypatch.setattr(cli_search, "build_client", lambda cfg: fake_client)
    assert cli_search.main(["--orgs", "hocutt"]) == 0
    d = out(capsys)
    assert d["orgs"][0]["id"] == 501


def test_search_query_output_shape(full_config, trimmed_client, monkeypatch, capsys):
    monkeypatch.setattr(cli_search, "build_client", lambda cfg: trimmed_client)
    assert cli_search.main(["the overstory powers"]) == 0
    d = out(capsys)
    assert d["preferred_format"] == "hardcover"
    assert d["branch"] == {"id": 501, "name": "Hocutt-Ellington Memorial Library"}
    assert d["system"]["id"] == 500 and d["consortium"]["id"] == 1
    assert {r["bib_id"] for r in d["results"]} == {12547531, 12834206}


def test_search_without_config_fails_cleanly(xdg, capsys):
    assert cli_search.main(["anything"]) == 1
    assert out(capsys)["error"] == "config"


from evergreen_holder import holds


def test_set_password_refuses_non_tty(full_config, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli_config.main(["set-password"]) == 1
    assert out(capsys)["error"] == "not_a_tty"


def test_set_password_verifies_then_writes(full_config, fake_client, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli_config, "build_client", lambda cfg: fake_client)
    fake_client.canned[fake_client.key("open-ils.auth.login", ({"username": "eric", "password": "newpw", "type": "opac"},))] = [
        {"ilsevent": 0, "textcode": "SUCCESS", "payload": {"authtoken": "t", "authtime": 1}}]
    answers = iter(["newpw", "newpw"])
    assert cli_config.cmd_set_password(prompt=lambda _: next(answers)) == 0
    assert out(capsys)["ok"] is True
    assert config.read_raw()["password"] == "newpw"


def test_set_password_mismatch(full_config, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["a", "b"])
    assert cli_config.cmd_set_password(prompt=lambda _: next(answers)) == 1
    assert out(capsys)["error"] == "mismatch"
    assert config.read_raw()["password"] == "pw"  # unchanged


def test_set_password_bad_login_does_not_write(full_config, fake_client, monkeypatch, capsys):
    from evergreen_holder.gateway import IlsEvent
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr(cli_config, "build_client", lambda cfg: fake_client)
    fake_client.canned[fake_client.key("open-ils.auth.login", ({"username": "eric", "password": "wrong", "type": "opac"},))] = \
        IlsEvent({"ilsevent": 1000, "textcode": "LOGIN_FAILED", "desc": "User login failed"})
    answers = iter(["wrong", "wrong"])
    assert cli_config.cmd_set_password(prompt=lambda _: next(answers)) == 2
    assert out(capsys)["error"] == "LOGIN_FAILED"
    assert config.read_raw()["password"] == "pw"
