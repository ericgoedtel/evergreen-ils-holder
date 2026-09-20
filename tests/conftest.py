from __future__ import annotations

import json
from pathlib import Path

import pytest

FIX = Path(__file__).parent / "fixtures"


class FakeClient:
    """Stand-in for GatewayClient: canned decoded payloads keyed by method + JSON params."""

    def __init__(self, canned: dict[str, list]):
        self.canned = canned
        self.calls: list[tuple[str, str, tuple]] = []

    @staticmethod
    def key(method: str, params: tuple) -> str:
        return f"{method}|{json.dumps(list(params))}"

    def call(self, service: str, method: str, *params):
        self.calls.append((service, method, params))
        k = self.key(method, params)
        if k not in self.canned:
            raise KeyError(f"no canned response for {k}")
        resp = self.canned[k]
        if isinstance(resp, Exception):
            raise resp
        return resp

    def call_one(self, service: str, method: str, *params):
        payload = self.call(service, method, *params)
        return payload[0] if payload else None


@pytest.fixture
def fixtures() -> dict:
    return json.loads((FIX / "catalog.json").read_text())


@pytest.fixture
def fake_client(fixtures) -> FakeClient:
    return FakeClient(dict(fixtures))


@pytest.fixture
def trimmed_client(fake_client):
    k = fake_client.key("open-ils.search.biblio.multiclass.query",
                        ({"limit": 25, "org_unit": 1}, "the overstory powers search_format(book) -item_form(d)", 1))
    res = dict(fake_client.canned[k][0])
    res["ids"] = [row for row in res["ids"] if row[0] in (12547531, 12834206)]
    fake_client.canned[k] = [res]
    return fake_client
