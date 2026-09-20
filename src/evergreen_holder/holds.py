"""Authenticated operations: login, patron lookup, title hold placement, queue stats."""
from __future__ import annotations

from .gateway import IlsEvent, is_event

AUTH = "open-ils.auth"
ACTOR = "open-ils.actor"
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


SETTING_KEYS = ["opac.hold_notify", "opac.default_phone", "opac.default_sms_notify", "opac.default_sms_carrier"]


def notify_prefs(client, authtoken: str, patron: int) -> dict:
    """Resolve hold notification fields the way the OPAC's place-hold form pre-fills them:
    from the patron's opac.hold_notify setting ("email", "email:phone:sms", ...), falling
    back to email when unset and the patron has an email address."""
    settings = client.call_one(ACTOR, "open-ils.actor.patron.settings.retrieve", authtoken, patron, SETTING_KEYS) or {}
    au = client.call_one(AUTH, "open-ils.auth.session.retrieve", authtoken) or {}
    pref = settings.get("opac.hold_notify")
    methods = set(pref.split(":")) if pref else ({"email"} if au.get("email") else set())
    out: dict = {"email_notify": 1 if "email" in methods else 0}
    if "phone" in methods:
        phone = settings.get("opac.default_phone") or au.get("day_phone")
        if phone:
            out["phone_notify"] = phone
    if "sms" in methods and settings.get("opac.default_sms_notify"):
        out["sms_notify"] = settings["opac.default_sms_notify"]
        if settings.get("opac.default_sms_carrier") is not None:
            out["sms_carrier"] = settings["opac.default_sms_carrier"]
    return out


def notify_summary(notify: dict) -> list[str]:
    """Method names present in a notify dict, never the phone/SMS numbers themselves."""
    out = []
    if notify.get("email_notify"):
        out.append("email")
    if notify.get("phone_notify"):
        out.append("phone")
    if notify.get("sms_notify"):
        out.append("sms")
    return out


def hold_payload(patron: int, pickup_lib: int, notify: dict | None = None) -> dict:
    payload = {"patronid": patron, "pickup_lib": pickup_lib, "hold_type": HOLD_TYPE_TITLE}
    if notify:
        payload.update(notify)
    return payload


def place_title_hold(client, authtoken: str, patron: int, pickup_lib: int, bib_id: int,
                     notify: dict | None = None) -> int:
    responses = client.call(CIRC, "open-ils.circ.holds.test_and_create.batch",
                            authtoken, hold_payload(patron, pickup_lib, notify), [bib_id])
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


def targeted_copy(client, authtoken: str, patron: int, hold_id: int) -> dict | None:
    """Which copy Evergreen has assigned to the hold and which library owns it.
    The patron OPAC hides this behind "Waiting for copy"; the API does not."""
    from .catalog import org_names  # local import: catalog is read-only, holds is authenticated

    payload = client.call(CIRC, "open-ils.circ.holds.retrieve", authtoken, patron)
    hs = payload[0] if payload and isinstance(payload[0], list) else payload
    hold = next((h for h in hs if isinstance(h, dict) and int(h.get("id", -1)) == hold_id), None)
    if not hold or not hold.get("current_copy"):
        return None
    copy_id = int(hold["current_copy"])
    cp = client.call_one("open-ils.search", "open-ils.search.asset.copy.retrieve", copy_id) or {}
    lib = int(cp["circ_lib"]) if cp.get("circ_lib") is not None else None
    return {"copy_id": copy_id, "library_id": lib,
            "library": org_names(client, [lib]).get(lib, "") if lib is not None else ""}


def logout(client, authtoken: str) -> None:
    try:
        client.call(AUTH, "open-ils.auth.session.delete", authtoken)
    except Exception:
        pass
