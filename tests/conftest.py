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
