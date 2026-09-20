"""Authenticated operations: login, patron lookup, title hold placement, queue stats."""
from __future__ import annotations

from .gateway import IlsEvent, is_event

AUTH = "open-ils.auth"
CIRC = "open-ils.circ"
HOLD_TYPE_TITLE = "T"


def login(client, username: str, password: str) -> str:
    res = client.call_one(AUTH, "open-ils.auth.login", {"username": username, "password": password, "type": "opac"})
    try:
        return res["payload"]["authtoken"]
    except (TypeError, KeyError):
        raise IlsEvent({"ilsevent": -1, "textcode": "LOGIN_UNEXPECTED", "desc": f"unexpected login response: {res!r}"}) from None


def patron_id(client, authtoken: str) -> int:
    au = client.call_one(AUTH, "open-ils.auth.session.retrieve", authtoken)
    return int(au["id"])


def hold_payload(patron: int, pickup_lib: int) -> dict:
    return {"patronid": patron, "pickup_lib": pickup_lib, "hold_type": HOLD_TYPE_TITLE}


def place_title_hold(client, authtoken: str, patron: int, pickup_lib: int, bib_id: int) -> int:
    responses = client.call(CIRC, "open-ils.circ.holds.test_and_create.batch",
                            authtoken, hold_payload(patron, pickup_lib), [bib_id])
    for r in responses:
        if not isinstance(r, dict) or int(r.get("target", -1)) != bib_id:
            continue
        result = r.get("result")
        if isinstance(result, list):
            result = result[0] if result else None
        if is_event(result):
            raise IlsEvent(result)
        if isinstance(result, dict) and is_event(result.get("last_event")):
            raise IlsEvent(result["last_event"])
        try:
            return int(result)
        except (TypeError, ValueError):
            raise IlsEvent({"ilsevent": -1, "textcode": "UNEXPECTED_RESULT",
                            "desc": f"hold create returned {result!r}"}) from None
    raise IlsEvent({"ilsevent": -1, "textcode": "NO_RESULT", "desc": f"no result returned for bib {bib_id}"})


def queue_stats(client, authtoken: str, hold_id: int) -> dict:
    return client.call_one(CIRC, "open-ils.circ.hold.queue_stats.retrieve", authtoken, hold_id) or {}


def logout(client, authtoken: str) -> None:
    try:
        client.call(AUTH, "open-ils.auth.session.delete", authtoken)
    except Exception:
        pass
