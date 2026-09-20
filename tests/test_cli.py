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


def test_set_base_url_reports_password_cleared(full_config, capsys):
    assert config.read_raw()["password"] == "pw"
    assert cli_config.main(["set", "base_url", "https://other.example.org"]) == 0
    d = out(capsys)
    assert d["password_cleared"] is True
    assert "password" not in config.read_raw()


def test_set_base_url_same_value_keeps_password(full_config, capsys):
    assert cli_config.main(["set", "base_url", "https://example.org"]) == 0
    d = out(capsys)
    assert "password_cleared" not in d
    assert config.read_raw()["password"] == "pw"


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


from evergreen_holder import cli_hold


def canned_auth(fake_client):
    fake_client.canned[fake_client.key("open-ils.auth.login", ({"username": "eric", "password": "pw", "type": "opac"},))] = [
        {"ilsevent": 0, "textcode": "SUCCESS", "payload": {"authtoken": "tok", "authtime": 1}}]
    fake_client.canned[fake_client.key("open-ils.auth.session.retrieve", ("tok",))] = [
        {"_class": "au", "id": 777, "email": "e@x.org", "day_phone": None}]
    fake_client.canned[fake_client.key("open-ils.actor.patron.settings.retrieve", ("tok", 777, holds.SETTING_KEYS))] = [
        {"opac.hold_notify": "email", "opac.default_phone": None, "opac.default_sms_notify": None,
         "opac.default_sms_carrier": None}]
    fake_client.canned[fake_client.key("open-ils.auth.session.delete", ("tok",))] = [1]


def test_hold_dry_run_never_calls_create(full_config, fake_client, monkeypatch, capsys):
    canned_auth(fake_client)
    monkeypatch.setattr(cli_hold, "build_client", lambda cfg: fake_client)
    assert cli_hold.main(["12547531", "--dry-run"]) == 0
    d = out(capsys)
    assert d["dry_run"] is True
    assert d["payload"] == {"patronid": 777, "pickup_lib": 501, "hold_type": "T", "email_notify": 1}
    assert d["notify"] == ["email"]
    assert not any(m == "open-ils.circ.holds.test_and_create.batch" for _, m, _ in fake_client.calls)


def test_hold_dry_run_redacts_phone_and_sms(full_config, fake_client, monkeypatch, capsys):
    canned_auth(fake_client)
    fake_client.canned[fake_client.key("open-ils.actor.patron.settings.retrieve", ("tok", 777, holds.SETTING_KEYS))] = [
        {"opac.hold_notify": "email:phone:sms", "opac.default_phone": "919-555-0199",
         "opac.default_sms_notify": "9195550188", "opac.default_sms_carrier": 42}]
    monkeypatch.setattr(cli_hold, "build_client", lambda cfg: fake_client)
    assert cli_hold.main(["12547531", "--dry-run"]) == 0
    d = out(capsys)
    assert d["notify"] == ["email", "phone", "sms"]
    assert d["payload"]["phone_notify"] == "<redacted>"
    assert d["payload"]["sms_notify"] == "<redacted>"
    raw = json.dumps(d)
    assert "919-555-0199" not in raw
    assert "9195550188" not in raw


def test_hold_places_and_reports_queue(full_config, fake_client, monkeypatch, capsys):
    canned_auth(fake_client)
    fake_client.canned[fake_client.key("open-ils.circ.holds.test_and_create.batch",
                                       ("tok", {"patronid": 777, "pickup_lib": 501, "hold_type": "T", "email_notify": 1}, [12547531]))] = [
        {"target": 12547531, "result": 99001}]
    fake_client.canned[fake_client.key("open-ils.circ.hold.queue_stats.retrieve", ("tok", 99001))] = [
        {"total_holds": 7, "queue_position": 3, "potential_copies": 102, "status": 2, "estimated_wait": 0}]
    fake_client.canned[fake_client.key("open-ils.circ.holds.retrieve", ("tok", 777))] = [
        [{"_class": "ahr", "id": 99001, "current_copy": 555}]]
    fake_client.canned[fake_client.key("open-ils.search.asset.copy.retrieve", (555,))] = [
        {"_class": "acp", "id": 555, "circ_lib": 393, "status": 0}]
    tree = fake_client.canned[fake_client.key("open-ils.actor.org_tree.retrieve", ())][0]
    tree["children"].append({"_class": "aou", "id": 393, "name": "Braswell Memorial Main Library",
                             "shortname": "BRASWELL", "children": None})
    monkeypatch.setattr(cli_hold, "build_client", lambda cfg: fake_client)
    assert cli_hold.main(["12547531"]) == 0
    d = out(capsys)
    assert d["hold_id"] == 99001 and d["queue_position"] == 3 and d["total_holds"] == 7
    assert d["pickup_lib"] == 501
    assert d["notify"] == ["email"]
    assert d["targeted"] == {"copy_id": 555, "library_id": 393, "library": "Braswell Memorial Main Library"}
    assert d["record_url"] == "https://example.org/eg/opac/record/12547531"
    assert d["holds_url"] == "https://example.org/eg/opac/myopac/holds"
    assert any(m == "open-ils.auth.session.delete" for _, m, _ in fake_client.calls)


def test_hold_suspend_sends_frozen_and_reports_suspended(full_config, fake_client, monkeypatch, capsys):
    canned_auth(fake_client)
    fake_client.canned[fake_client.key("open-ils.circ.holds.test_and_create.batch",
                                       ("tok", {"patronid": 777, "pickup_lib": 501, "hold_type": "T", "email_notify": 1, "frozen": 1}, [12547531]))] = [
        {"target": 12547531, "result": 99001}]
    fake_client.canned[fake_client.key("open-ils.circ.hold.queue_stats.retrieve", ("tok", 99001))] = [
        {"total_holds": 7, "queue_position": 3, "potential_copies": 102, "status": 2, "estimated_wait": 0}]
    fake_client.canned[fake_client.key("open-ils.circ.holds.retrieve", ("tok", 777))] = [
        [{"_class": "ahr", "id": 99001, "current_copy": None}]]
    monkeypatch.setattr(cli_hold, "build_client", lambda cfg: fake_client)
    assert cli_hold.main(["12547531", "--suspend"]) == 0
    d = out(capsys)
    assert d["hold_id"] == 99001
    assert d["suspended"] is True


def test_hold_dry_run_suspend_shows_frozen_in_payload(full_config, fake_client, monkeypatch, capsys):
    canned_auth(fake_client)
    monkeypatch.setattr(cli_hold, "build_client", lambda cfg: fake_client)
    assert cli_hold.main(["12547531", "--dry-run", "--suspend"]) == 0
    d = out(capsys)
    assert d["suspended"] is True
    assert d["payload"]["frozen"] == 1


def test_hold_event_exit_2(full_config, fake_client, monkeypatch, capsys):
    canned_auth(fake_client)
    fake_client.canned[fake_client.key("open-ils.circ.holds.test_and_create.batch",
                                       ("tok", {"patronid": 777, "pickup_lib": 501, "hold_type": "T", "email_notify": 1}, [12547531]))] = [
        {"target": 12547531, "result": {"ilsevent": 1707, "textcode": "HOLD_EXISTS", "desc": "dup"}}]
    monkeypatch.setattr(cli_hold, "build_client", lambda cfg: fake_client)
    assert cli_hold.main(["12547531"]) == 2
    assert out(capsys)["error"] == "HOLD_EXISTS"


def test_hold_requires_password(xdg, monkeypatch, capsys):
    monkeypatch.setattr(cli_config, "fetch_idl", lambda base_url, dest: dest.parent.mkdir(parents=True, exist_ok=True) or dest.write_text("<IDL/>"))
    for k_, v in [("base_url", "https://example.org"), ("branch_id", "501"), ("system_id", "500"),
                  ("consortium_id", "1"), ("username", "eric"), ("preferred_format", "hardcover")]:
        cli_config.main(["set", k_, v])
    capsys.readouterr()  # discard the six `set` JSON docs; only the hold command's output matters here
    assert cli_hold.main(["12547531"]) == 1
    assert "set-password" in out(capsys)["desc"]


def test_hold_has_no_password_argument():
    import argparse
    with pytest.raises(SystemExit):
        cli_hold.main(["12547531", "--password", "x"])


from evergreen_holder import cli_pika_search
from evergreen_holder.pika import build_client

PIKA_FIX = Path(__file__).parent / "fixtures" / "pika"


def pika_fixture(name: str) -> str:
    return (PIKA_FIX / name).read_text(encoding="utf-8")


def pika_handler(request):
    import httpx
    path = request.url.path
    params = dict(request.url.params)
    if path == "/Search/Results":
        q = params.get("lookfor")
        if q == "Cryptonomicon":
            return httpx.Response(200, text=pika_fixture("search_cryptonomicon.xml"))
        if q == "Dune":
            return httpx.Response(200, text=pika_fixture("search_dune.xml"))
        return httpx.Response(200, text='<rss version="2.0"><channel><description>d</description></channel></rss>')
    if path == "/GroupedWork/dfe46b1a-9917-0768-bc0e-e6dfad241b5f/Home":
        return httpx.Response(200, text=pika_fixture("work_cryptonomicon.html"))
    if path == "/GroupedWork/ddb713e8-646d-8b2b-8ee6-02d926507b73/Home":
        return httpx.Response(200, text=pika_fixture("work_dune.html"))
    if path.startswith("/GroupedWork/"):
        # other Dune-search hits: no fixture recorded, serve a page with no ils records
        return httpx.Response(200, text="<html><body>no debugging table here</body></html>")
    if path == "/API/ItemAPI":
        rec_id = params.get("id")
        if rec_id == "ils:428175":
            return httpx.Response(200, text=pika_fixture("availability_428175.json"))
        if rec_id == "ils:495749":
            return httpx.Response(200, text=pika_fixture("availability_495749.json"))
    return httpx.Response(404, text="not found")


@pytest.fixture
def pika_config(xdg):
    config.set_value("pika.base_url", "https://catalog.wake.gov")
    config.set_value("pika.pickup_branch", "Middlecreek Community")


def test_pika_search_happy_path(pika_config, monkeypatch, capsys):
    import httpx
    monkeypatch.setattr(cli_pika_search, "build_client",
                         lambda base_url: build_client(base_url, transport=httpx.MockTransport(pika_handler)))
    assert cli_pika_search.main(["Cryptonomicon"]) == 0
    d = out(capsys)
    assert d["system"] == "pika"
    assert d["base_url"] == "https://catalog.wake.gov"
    assert d["pickup_branch"] == "Middlecreek Community"
    assert d["total_found"] == 1
    assert len(d["results"]) == 1
    r = d["results"][0]
    assert r["grouped_work_id"] == "dfe46b1a-9917-0768-bc0e-e6dfad241b5f"
    assert r["title"] == "Cryptonomicon"
    assert r["author"] == "Stephenson, Neal"
    assert r["url"] == "https://catalog.wake.gov/GroupedWork/dfe46b1a-9917-0768-bc0e-e6dfad241b5f/Home"
    assert r["wait_list"] is None
    assert len(r["records"]) == 1
    rec = r["records"][0]
    assert rec["record_id"] == "428175"
    assert rec["format"] == "Book"
    assert rec["large_print"] is False
    assert rec["copies"]["branch"] == {"available": 1, "total": 1}
    assert rec["copies"]["system"] == {"available": 1, "total": 2}
    assert rec["on_shelf_at"] == ["Middlecreek Community"]
    assert rec["record_url"] == "https://catalog.wake.gov/Record/428175"


def test_pika_search_dune_has_wait_list(pika_config, monkeypatch, capsys):
    import httpx
    monkeypatch.setattr(cli_pika_search, "build_client",
                         lambda base_url: build_client(base_url, transport=httpx.MockTransport(pika_handler)))
    assert cli_pika_search.main(["Dune"]) == 0
    d = out(capsys)
    assert d["total_found"] == 51
    assert len(d["results"]) == 10  # capped at MAX_WORKS
    dune_result = next(r for r in d["results"] if r["grouped_work_id"] == "ddb713e8-646d-8b2b-8ee6-02d926507b73")
    assert dune_result["wait_list"] == {"copies": 9, "holds": 57}


def test_pika_search_config_error(xdg, capsys):
    assert cli_pika_search.main(["Cryptonomicon"]) == 1
    d = out(capsys)
    assert d["error"] == "config"
    assert "pika.base_url" in d["desc"]


def test_pika_search_remote_error(pika_config, monkeypatch, capsys):
    import httpx

    def failing_handler(request):
        return httpx.Response(500, text="boom")

    monkeypatch.setattr(cli_pika_search, "build_client",
                         lambda base_url: build_client(base_url, transport=httpx.MockTransport(failing_handler)))
    assert cli_pika_search.main(["Cryptonomicon"]) == 2
    d = out(capsys)
    assert d["error"] == "pika"
