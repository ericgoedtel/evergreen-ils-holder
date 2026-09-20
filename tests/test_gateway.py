import json

import httpx
import pytest

from evergreen_holder.gateway import GatewayClient, GatewayError, IlsEvent, is_event

FIELD_MAP = {"aou": ["children", "id", "name"], "mvr": ["title", "author"]}


def make_client(handler):
    return GatewayClient("https://example.org", FIELD_MAP, transport=httpx.MockTransport(handler))


def test_call_posts_service_method_and_json_params():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["body"] = request.content.decode()
        return httpx.Response(200, json={"payload": [42], "status": 200})

    client = make_client(handler)
    assert client.call("open-ils.actor", "some.method", {"a": 1}, 501) == [42]
    assert seen["url"] == "https://example.org/osrf-gateway-v1"
    assert "service=open-ils.actor" in seen["body"]
    assert "method=some.method" in seen["body"]
    assert "param=%7B%22a%22%3A+1%7D" in seen["body"] or "param=%7B%22a%22%3A1%7D" in seen["body"]
    assert "param=501" in seen["body"]


def test_decodes_fieldmapper_objects_recursively():
    def handler(_):
        return httpx.Response(200, json={"payload": [
            {"__c": "aou", "__p": [[{"__c": "aou", "__p": [None, 501, "Hocutt"]}], 500, "Clayton"]}
        ], "status": 200})

    org = make_client(handler).call_one("open-ils.actor", "org_tree")
    assert org == {"_class": "aou", "children": [{"_class": "aou", "children": None, "id": 501, "name": "Hocutt"}],
                   "id": 500, "name": "Clayton"}


def test_unknown_class_keeps_raw_positional_list():
    def handler(_):
        return httpx.Response(200, json={"payload": [{"__c": "zzz", "__p": [1, 2]}], "status": 200})

    assert make_client(handler).call_one("s", "m") == {"_class": "zzz", "_raw": [1, 2]}


def test_event_payload_raises_ils_event():
    def handler(_):
        return httpx.Response(200, json={"payload": [
            {"ilsevent": 1001, "textcode": "NO_SESSION", "desc": "timed out"}], "status": 200})

    with pytest.raises(IlsEvent) as ei:
        make_client(handler).call("s", "m")
    assert ei.value.textcode == "NO_SESSION"
    assert ei.value.desc == "timed out"


def test_success_event_does_not_raise():
    def handler(_):
        return httpx.Response(200, json={"payload": [
            {"ilsevent": 0, "textcode": "SUCCESS", "payload": {"authtoken": "abc"}}], "status": 200})

    assert make_client(handler).call_one("s", "m")["payload"]["authtoken"] == "abc"


def test_gateway_status_error_raises_gateway_error():
    def handler(_):
        return httpx.Response(200, json={"payload": [], "status": 500, "debug": "boom"})

    with pytest.raises(GatewayError):
        make_client(handler).call("s", "m")


def test_http_error_raises_gateway_error():
    def handler(_):
        return httpx.Response(502, text="bad gateway")

    with pytest.raises(GatewayError):
        make_client(handler).call("s", "m")


def test_call_one_returns_none_on_empty_payload():
    def handler(_):
        return httpx.Response(200, json={"payload": [], "status": 200})

    assert make_client(handler).call_one("s", "m") is None


def test_is_event():
    assert is_event({"ilsevent": 1000, "textcode": "LOGIN_FAILED"})
    assert not is_event({"ilsevent": 0, "textcode": "SUCCESS"})
    assert not is_event({"count": 1})
    assert not is_event(5)
