import pytest

from evergreen_holder.gateway import IlsEvent
from evergreen_holder.holds import hold_payload, login, logout, patron_id, place_title_hold, queue_stats
from tests.conftest import FakeClient


def k(method, *params):
    return FakeClient.key(method, params)


def test_login_returns_authtoken():
    c = FakeClient({k("open-ils.auth.login", {"username": "eric", "password": "pw", "type": "opac"}): [
        {"ilsevent": 0, "textcode": "SUCCESS", "payload": {"authtoken": "tok123", "authtime": 420}}]})
    assert login(c, "eric", "pw") == "tok123"


def test_login_failure_propagates_event():
    c = FakeClient({k("open-ils.auth.login", {"username": "eric", "password": "bad", "type": "opac"}):
                    IlsEvent({"ilsevent": 1000, "textcode": "LOGIN_FAILED", "desc": "User login failed"})})
    with pytest.raises(IlsEvent) as ei:
        login(c, "eric", "bad")
    assert ei.value.textcode == "LOGIN_FAILED"


def test_patron_id():
    c = FakeClient({k("open-ils.auth.session.retrieve", "tok"): [{"_class": "au", "id": 777, "usrname": "eric"}]})
    assert patron_id(c, "tok") == 777


def test_hold_payload_is_title_hold():
    assert hold_payload(777, 501) == {"patronid": 777, "pickup_lib": 501, "hold_type": "T"}


def test_place_title_hold_returns_hold_id():
    c = FakeClient({k("open-ils.circ.holds.test_and_create.batch", "tok",
                      {"patronid": 777, "pickup_lib": 501, "hold_type": "T"}, [12547531]):
                    [{"target": 12547531, "result": 99001}]})
    assert place_title_hold(c, "tok", 777, 501, 12547531) == 99001


def test_place_title_hold_raises_on_per_target_event():
    c = FakeClient({k("open-ils.circ.holds.test_and_create.batch", "tok",
                      {"patronid": 777, "pickup_lib": 501, "hold_type": "T"}, [12547531]):
                    [{"target": 12547531, "result": {"ilsevent": 1707, "textcode": "HOLD_EXISTS", "desc": "dup"}}]})
    with pytest.raises(IlsEvent) as ei:
        place_title_hold(c, "tok", 777, 501, 12547531)
    assert ei.value.textcode == "HOLD_EXISTS"


def test_place_title_hold_handles_result_wrapped_in_list_of_events():
    c = FakeClient({k("open-ils.circ.holds.test_and_create.batch", "tok",
                      {"patronid": 777, "pickup_lib": 501, "hold_type": "T"}, [12547531]):
                    [{"target": 12547531, "result": [{"ilsevent": 1707, "textcode": "HOLD_EXISTS", "desc": "dup"}]}]})
    with pytest.raises(IlsEvent):
        place_title_hold(c, "tok", 777, 501, 12547531)


def test_place_title_hold_raises_when_no_result_for_target():
    c = FakeClient({k("open-ils.circ.holds.test_and_create.batch", "tok",
                      {"patronid": 777, "pickup_lib": 501, "hold_type": "T"}, [12547531]): []})
    with pytest.raises(IlsEvent) as ei:
        place_title_hold(c, "tok", 777, 501, 12547531)
    assert ei.value.textcode == "NO_RESULT"


def test_queue_stats():
    stats = {"total_holds": 7, "queue_position": 3, "potential_copies": 102, "status": 2, "estimated_wait": 0}
    c = FakeClient({k("open-ils.circ.hold.queue_stats.retrieve", "tok", 99001): [stats]})
    assert queue_stats(c, "tok", 99001) == stats


def test_logout_swallows_errors():
    c = FakeClient({})  # no canned response → KeyError inside; must not propagate
    logout(c, "tok")
