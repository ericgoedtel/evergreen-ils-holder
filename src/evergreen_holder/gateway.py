"""Minimal OpenSRF HTTP gateway client with fieldmapper decoding."""
from __future__ import annotations

import json
import urllib.parse
from typing import Any

import httpx

GATEWAY_PATH = "/osrf-gateway-v1"


class GatewayError(Exception):
    """Transport-level or gateway-level failure (not an ILS event)."""


class IlsEvent(Exception):
    """An Evergreen event returned in place of a result (e.g. NO_SESSION, HOLD_EXISTS)."""

    def __init__(self, event: dict):
        self.ilsevent = int(event.get("ilsevent", -1))
        self.textcode = str(event.get("textcode", "UNKNOWN"))
        self.desc = str(event.get("desc", ""))
        self.event = event
        super().__init__(f"{self.textcode}: {self.desc}")


def is_event(obj: Any) -> bool:
    return (
        isinstance(obj, dict)
        and "ilsevent" in obj
        and "textcode" in obj
        and str(obj["ilsevent"]) != "0"
    )


class GatewayClient:
    def __init__(self, base_url: str, field_map: dict[str, list[str]],
                 transport: httpx.BaseTransport | None = None, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.field_map = field_map
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)

    def call(self, service: str, method: str, *params: Any) -> list:
        data = [("service", service), ("method", method)]
        data += [("param", json.dumps(p)) for p in params]
        # httpx >= 0.28 dropped support for a list of tuples in `data=`
        # (it now must be a Mapping); encode the form body ourselves so
        # repeated `param` keys still work. quote (not quote_plus): the
        # gateway decodes %20 but not '+', and a '+' inside a JSON param
        # corrupts it so the server sees an empty argument hash.
        encoded = urllib.parse.urlencode(data, quote_via=urllib.parse.quote)
        try:
            resp = self._http.post(
                self.base_url + GATEWAY_PATH,
                content=encoded,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if not (200 <= resp.status_code < 300):
                raise GatewayError(f"{method}: HTTP {resp.status_code}")
            body = resp.json()
        except (httpx.HTTPError, ValueError) as e:
            raise GatewayError(f"{method}: {e}") from e
        if body.get("status") != 200:
            raise GatewayError(f"{method}: gateway status {body.get('status')}: {body.get('debug', '')}")
        payload = [self._decode(x) for x in body.get("payload", [])]
        for item in payload:
            if is_event(item):
                raise IlsEvent(item)
        return payload

    def call_one(self, service: str, method: str, *params: Any) -> Any:
        payload = self.call(service, method, *params)
        return payload[0] if payload else None

    def _decode(self, obj: Any) -> Any:
        if isinstance(obj, list):
            return [self._decode(x) for x in obj]
        if isinstance(obj, dict):
            if "__c" in obj and "__p" in obj:
                cls = obj["__c"]
                values = [self._decode(x) for x in obj["__p"]]
                fields = self.field_map.get(cls)
                if fields is None:
                    return {"_class": cls, "_raw": values}
                out: dict[str, Any] = {"_class": cls}
                out.update(zip(fields, values))
                return out
            return {k: self._decode(v) for k, v in obj.items()}
        return obj
