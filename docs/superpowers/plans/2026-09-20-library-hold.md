# library-hold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build three deterministic Python CLIs (`evergreen-config`, `evergreen-search`, `evergreen-hold`) plus a `/library-hold` Claude Code skill that finds a book in an Evergreen ILS catalog, reports availability by branch/system/consortium, and places a title hold only after explicit human confirmation.

**Architecture:** A ~60-line gateway client (`httpx`) talks to the OpenSRF HTTP gateway and decodes fieldmapper objects using a cached copy of the server's `fm_IDL.xml`. Thin domain modules (`catalog.py`, `holds.py`, `config.py`) sit on top and are exercised with a fake client fed from recorded JSON fixtures. Each CLI is an `argparse` `main()` that prints one JSON document. The skill (`skill/SKILL.md`) tells Claude how to drive the CLIs; all judgment and all prompting happen in Claude, all network I/O happens in the tools.

**Tech Stack:** Python ≥ 3.11, `uv`, `httpx`, stdlib `tomllib`/`argparse`/`xml.etree`/`getpass`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-09-20-library-hold-design.md`

## Global Constraints

- Python ≥ 3.11 (needs `tomllib`).
- Runtime dependency: `httpx` only. Dev dependency: `pytest` only.
- Three separate console scripts: `evergreen-config`, `evergreen-search`, `evergreen-hold`. Never a single multiplexed binary.
- `evergreen-hold` reads the password from the config file only. It must not accept `--password` or read `EVERGREEN_PASSWORD`.
- `evergreen-config set password ...` must be refused. The password is only ever written by `evergreen-config set-password` (interactive `getpass`).
- Config file mode must be `0600` and its directory `0700`; tools that read it refuse to run otherwise.
- Every CLI prints exactly one JSON document to stdout. Errors print `{"error": ..., "desc": ...}` and exit non-zero (1 for local/config errors, 2 for Evergreen events).
- No pre-hold eligibility checks, no Open Library, no result ranking in the tool (Claude ranks).
- Fieldmapper field order comes from cached `fm_IDL.xml`, never from hardcoded index numbers in domain code.
- All network access lives in `gateway.py` and `idl.py` (fetch). Domain modules take a client object so tests never touch the network.

---

## File Structure

```
pyproject.toml                       uv project; three console scripts
Makefile                             install / uninstall / test
README.md                            setup + config reference
.gitignore
src/evergreen_holder/__init__.py     version only
src/evergreen_holder/idl.py          parse fm_IDL.xml → {class: [field names]}; fetch+cache
src/evergreen_holder/gateway.py      GatewayClient.call(); fieldmapper decode; IlsEvent
src/evergreen_holder/config.py       XDG paths, load/validate/set/doctor, TOML write
src/evergreen_holder/catalog.py      org lookup; bib search (query + mods + MARC 020 + copy tiers)
src/evergreen_holder/holds.py        login, patron id, place hold, queue stats
src/evergreen_holder/cli_config.py   evergreen-config
src/evergreen_holder/cli_search.py   evergreen-search
src/evergreen_holder/cli_hold.py     evergreen-hold
tests/conftest.py                    FakeClient + fixture loader
tests/fixtures/*.json                recorded gateway responses
tests/record_fixtures.py             one-off script that (re)records fixtures from the live gateway
tests/test_idl.py
tests/test_gateway.py
tests/test_config.py
tests/test_catalog.py
tests/test_holds.py
tests/test_cli.py
skill/SKILL.md                       /library-hold skill
```

---

### Task 1: Project scaffold

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `src/evergreen_holder/__init__.py`, `tests/__init__.py`, `tests/test_scaffold.py`

**Interfaces:**
- Produces: importable package `evergreen_holder` with `__version__`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_scaffold.py
def test_package_imports():
    import evergreen_holder
    assert evergreen_holder.__version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_scaffold.py -v`
Expected: FAIL (no pyproject / module not found)

- [ ] **Step 3: Create the project files**

```toml
# pyproject.toml
[project]
name = "evergreen-holder"
version = "0.1.0"
description = "Find a book in an Evergreen ILS catalog and place a hold, with a human in the loop."
requires-python = ">=3.11"
dependencies = ["httpx>=0.27"]

[project.scripts]
evergreen-config = "evergreen_holder.cli_config:main"
evergreen-search = "evergreen_holder.cli_search:main"
evergreen-hold = "evergreen_holder.cli_hold:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/evergreen_holder"]

[dependency-groups]
dev = ["pytest>=8"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

```
# .gitignore
.venv/
__pycache__/
*.egg-info/
dist/
.pytest_cache/
```

```python
# src/evergreen_holder/__init__.py
__version__ = "0.1.0"
```

`tests/__init__.py` is empty.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv sync && uv run pytest tests/test_scaffold.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml .gitignore src tests uv.lock
git commit -m "chore: scaffold evergreen-holder package"
```

---

### Task 2: IDL field map

**Files:**
- Create: `src/evergreen_holder/idl.py`, `tests/test_idl.py`

**Interfaces:**
- Produces: `parse_field_map(xml_text: str) -> dict[str, list[str]]`, `load_field_map(path: Path) -> dict[str, list[str]]`, `fetch_idl(base_url: str, dest: Path) -> None`.

Background: the server's `fm_IDL.xml` (at `<base_url>/reports/fm_IDL.xml`, ~1.1 MB) lists every fieldmapper class and its fields in order. Gateway responses encode objects as `{"__c": "aou", "__p": [values...]}` where `__p` is positional in that field order. The XML uses single-quoted attributes and a default namespace `http://opensrf.org/spec/IDL/base/v1`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_idl.py
from pathlib import Path
from evergreen_holder.idl import parse_field_map, load_field_map

SAMPLE = """<IDL xmlns='http://opensrf.org/spec/IDL/base/v1'>
  <class id='aou' controller='open-ils.cstore'>
    <fields oils_persist:primary='id' xmlns:oils_persist='x'>
      <field name='children' virtual='true'/>
      <field name='id'/>
      <field name='name'/>
    </fields>
  </class>
  <class id='mvr'>
    <fields>
      <field name='title'/>
      <field name='author'/>
    </fields>
  </class>
</IDL>"""


def test_parse_field_map_keeps_field_order():
    m = parse_field_map(SAMPLE)
    assert m["aou"] == ["children", "id", "name"]
    assert m["mvr"] == ["title", "author"]


def test_load_field_map_reads_file(tmp_path: Path):
    p = tmp_path / "fm_IDL.xml"
    p.write_text(SAMPLE)
    assert load_field_map(p)["mvr"] == ["title", "author"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_idl.py -v`
Expected: FAIL with "No module named 'evergreen_holder.idl'"

- [ ] **Step 3: Implement**

```python
# src/evergreen_holder/idl.py
"""Fieldmapper IDL: the server-provided field order for gateway objects."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

NS = "{http://opensrf.org/spec/IDL/base/v1}"
IDL_PATH = "/reports/fm_IDL.xml"


def parse_field_map(xml_text: str) -> dict[str, list[str]]:
    root = ET.fromstring(xml_text)
    out: dict[str, list[str]] = {}
    for cls in root.iter(f"{NS}class"):
        cid = cls.get("id")
        if not cid:
            continue
        out[cid] = [f.get("name") for f in cls.iter(f"{NS}field") if f.get("name")]
    return out


def load_field_map(path: Path) -> dict[str, list[str]]:
    return parse_field_map(path.read_text(encoding="utf-8"))


def fetch_idl(base_url: str, dest: Path) -> None:
    resp = httpx.get(base_url.rstrip("/") + IDL_PATH, timeout=120, follow_redirects=True)
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(resp.text, encoding="utf-8")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_idl.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/evergreen_holder/idl.py tests/test_idl.py
git commit -m "feat: parse fieldmapper IDL into a class→fields map"
```

---

### Task 3: Gateway client

**Files:**
- Create: `src/evergreen_holder/gateway.py`, `tests/test_gateway.py`

**Interfaces:**
- Produces:
  - `class IlsEvent(Exception)` with attributes `textcode: str`, `desc: str`, `ilsevent: int`.
  - `class GatewayError(Exception)` for transport/HTTP problems.
  - `class GatewayClient:`
    - `__init__(self, base_url: str, field_map: dict[str, list[str]], transport: httpx.BaseTransport | None = None)`
    - `call(self, service: str, method: str, *params) -> list` — returns the decoded `payload` list. Fieldmapper objects become `dict` with `"_class"` plus named fields. Any payload element that is an event (`dict` with `ilsevent` != 0 and `textcode`) raises `IlsEvent`.
    - `call_one(self, service, method, *params)` — `call(...)[0]`, or `None` if the payload is empty.
  - `is_event(obj) -> bool` helper.

Background: the gateway is `<base_url>/osrf-gateway-v1`. Send `POST` with form fields `service`, `method`, and one `param` per positional argument, each JSON-encoded. The response is `{"payload": [...], "status": 200}`. A non-200 `status` (e.g. 500 with `debug`) is a `GatewayError`. `open-ils.auth.login` returns a *success* event (`ilsevent: 0, textcode: "SUCCESS", payload: {...}`), which must **not** raise.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gateway.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_gateway.py -v`
Expected: FAIL with "No module named 'evergreen_holder.gateway'"

- [ ] **Step 3: Implement**

```python
# src/evergreen_holder/gateway.py
"""Minimal OpenSRF HTTP gateway client with fieldmapper decoding."""
from __future__ import annotations

import json
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
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=True)

    def call(self, service: str, method: str, *params: Any) -> list:
        data = [("service", service), ("method", method)]
        data += [("param", json.dumps(p)) for p in params]
        try:
            resp = self._http.post(self.base_url + GATEWAY_PATH, data=data)
            resp.raise_for_status()
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_gateway.py -v`
Expected: PASS (if the `param=%7B...` assertion fails on encoding, loosen it to `assert "param=" in seen["body"]` and additionally parse with `urllib.parse.parse_qs` to check `params["param"] == ['{"a": 1}', "501"]`).

- [ ] **Step 5: Commit**

```bash
git add src/evergreen_holder/gateway.py tests/test_gateway.py
git commit -m "feat: OpenSRF gateway client with fieldmapper decoding and event handling"
```

---

### Task 4: Config module (XDG paths, permissions, set, doctor)

**Files:**
- Create: `src/evergreen_holder/config.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `REQUIRED_KEYS = ("base_url", "branch_id", "system_id", "consortium_id", "username", "preferred_format")` (password is checked separately).
  - `INT_KEYS = ("branch_id", "system_id", "consortium_id")`
  - `FORMATS = ("hardcover", "paperback")`
  - `config_path() -> Path` (`$XDG_CONFIG_HOME/evergreen-holder/config.toml`, default `~/.config/...`)
  - `idl_path() -> Path` (`$XDG_CACHE_HOME/evergreen-holder/fm_IDL.xml`, default `~/.cache/...`)
  - `class ConfigError(Exception)`
  - `read_raw() -> dict` — returns `{}` if the file doesn't exist; raises `ConfigError` if mode is not 0600.
  - `write_raw(data: dict) -> None` — creates dir 0700, writes TOML, chmod 0600.
  - `set_value(key: str, value: str) -> dict` — validates key (refuses `password`), coerces `INT_KEYS` to int, validates `preferred_format`, writes, returns new dict.
  - `set_password(password: str) -> None` — writes the password key.
  - `doctor() -> dict` — `{"config_path", "exists", "mode_ok", "idl_cached", "missing": [...], "password_set", "ok": bool}`. Never raises for a bad mode; reports `mode_ok: False`.
  - `load() -> dict` — full validated config for the search/hold tools; raises `ConfigError` listing missing keys or bad mode. Does **not** require `password` (search doesn't need it); `holds` checks that itself.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_config.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with "No module named 'evergreen_holder.config'"

- [ ] **Step 3: Implement**

```python
# src/evergreen_holder/config.py
"""XDG config file: paths, permission enforcement, reading, writing, doctor."""
from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path

APP = "evergreen-holder"
REQUIRED_KEYS = ("base_url", "branch_id", "system_id", "consortium_id", "username", "preferred_format")
INT_KEYS = ("branch_id", "system_id", "consortium_id")
FORMATS = ("hardcover", "paperback")
SETTABLE_KEYS = REQUIRED_KEYS  # password is deliberately excluded


class ConfigError(Exception):
    pass


def config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / APP / "config.toml"


def idl_path() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / APP / "fm_IDL.xml"


def _mode_ok(p: Path) -> bool:
    return stat.S_IMODE(p.stat().st_mode) == 0o600


def read_raw() -> dict:
    p = config_path()
    if not p.exists():
        return {}
    if not _mode_ok(p):
        raise ConfigError(f"{p} must be mode 0600 (run: chmod 600 {p})")
    with p.open("rb") as f:
        return tomllib.load(f)


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    s = str(v).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def write_raw(data: dict) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    body = "".join(f"{k} = {_toml_value(v)}\n" for k, v in data.items())
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(body)
    os.chmod(p, 0o600)


def set_value(key: str, value: str) -> dict:
    if key == "password":
        raise ConfigError("password can only be set with `evergreen-config set-password`")
    if key not in SETTABLE_KEYS:
        raise ConfigError(f"unknown key {key!r}; valid keys: {', '.join(SETTABLE_KEYS)}")
    coerced: int | str = value
    if key in INT_KEYS:
        try:
            coerced = int(value)
        except ValueError:
            raise ConfigError(f"{key} must be an integer org unit id, got {value!r}") from None
    if key == "preferred_format" and value not in FORMATS:
        raise ConfigError(f"preferred_format must be one of {', '.join(FORMATS)}")
    data = read_raw()
    data[key] = coerced
    write_raw(data)
    return data


def set_password(password: str) -> None:
    data = read_raw()
    data["password"] = password
    write_raw(data)


def doctor() -> dict:
    p = config_path()
    exists = p.exists()
    mode_ok = exists and _mode_ok(p)
    data: dict = {}
    if mode_ok:
        with p.open("rb") as f:
            data = tomllib.load(f)
    missing = [k for k in REQUIRED_KEYS if k not in data]
    password_set = bool(data.get("password"))
    idl_cached = idl_path().exists()
    return {
        "config_path": str(p),
        "exists": exists,
        "mode_ok": bool(mode_ok),
        "idl_cached": idl_cached,
        "missing": missing,
        "password_set": password_set,
        "ok": bool(exists and mode_ok and not missing and password_set and idl_cached),
    }


def load() -> dict:
    data = read_raw()
    missing = [k for k in REQUIRED_KEYS if k not in data]
    if missing:
        raise ConfigError("config is missing: " + ", ".join(missing) + " (run `evergreen-config doctor`)")
    return data
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/evergreen_holder/config.py tests/test_config.py
git commit -m "feat: XDG config with 0600 enforcement, set, and doctor"
```

---

### Task 5: Record live fixtures and build the FakeClient

**Files:**
- Create: `tests/record_fixtures.py`, `tests/conftest.py`, `tests/fixtures/` (generated JSON)

**Interfaces:**
- Produces: `FakeClient` (in `tests/conftest.py`) with `call(service, method, *params)` / `call_one(...)` that dispatch on a `(method, json.dumps(params))` key into a dict of canned decoded payloads and record the calls in `.calls`. Fixture `fixtures` (dict loaded from `tests/fixtures/*.json`), fixture `fake_client`.
- Consumes: `GatewayClient`, `load_field_map`, `fetch_idl` (Tasks 2–3).

Fixtures are recorded **decoded** (after fieldmapper expansion) so tests exercise domain code, not the decoder. Each fixture file is `{"<method>|<json params>": <payload list>}`.

- [ ] **Step 1: Write the recorder script**

```python
# tests/record_fixtures.py
"""Re-record gateway fixtures from a live Evergreen. Run manually: uv run python tests/record_fixtures.py
Requires network. Uses NC Cardinal; adjust BASE/ORGS if recording elsewhere."""
from __future__ import annotations

import json
from pathlib import Path

from evergreen_holder.gateway import GatewayClient
from evergreen_holder.idl import fetch_idl, load_field_map

BASE = "https://johnston.nccardinal.org"
BRANCH = 501
HERE = Path(__file__).parent
FIX = HERE / "fixtures"
IDL = HERE / "fixtures" / "fm_IDL.xml"

CALLS = [
    ("open-ils.actor", "open-ils.actor.org_tree.retrieve"),
    ("open-ils.search", "open-ils.search.biblio.multiclass.query",
     {"limit": 25, "org_unit": 1}, "the overstory powers search_format(book) -item_form(d)", 1),
    ("open-ils.search", "open-ils.search.biblio.record.mods_slim.retrieve", 12547531),
    ("open-ils.search", "open-ils.search.biblio.record.mods_slim.retrieve", 12834206),
    ("open-ils.supercat", "open-ils.supercat.record.object.retrieve", 12547531),
    ("open-ils.supercat", "open-ils.supercat.record.object.retrieve", 12834206),
    ("open-ils.search", "open-ils.search.biblio.record.copy_count", BRANCH, 12547531),
    ("open-ils.search", "open-ils.search.biblio.record.copy_count", BRANCH, 12834206),
]


def key(method: str, params: tuple) -> str:
    return f"{method}|{json.dumps(list(params))}"


def main() -> None:
    FIX.mkdir(exist_ok=True)
    if not IDL.exists():
        fetch_idl(BASE, IDL)
    client = GatewayClient(BASE, load_field_map(IDL))
    out: dict[str, list] = {}
    for service, method, *params in CALLS:
        out[key(method, tuple(params))] = client.call(service, method, *params)
        print("recorded", method, params)
    (FIX / "catalog.json").write_text(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run it**

Run: `uv run python tests/record_fixtures.py`
Expected: prints eight `recorded ...` lines; `tests/fixtures/catalog.json` and `tests/fixtures/fm_IDL.xml` exist. `catalog.json` will be a few hundred KB because the org tree is large; that is fine. The MARC string in the `bre` fixture must contain `<datafield tag="020"` — check with `grep -c 'tag=\\"020\\"' tests/fixtures/catalog.json` (expect ≥ 1).

- [ ] **Step 3: Write conftest**

```python
# tests/conftest.py
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
```

- [ ] **Step 4: Verify conftest loads**

Run: `uv run pytest tests -v`
Expected: all existing tests still PASS; no collection errors.

- [ ] **Step 5: Commit**

```bash
git add tests/record_fixtures.py tests/conftest.py tests/fixtures
git commit -m "test: record live gateway fixtures and add FakeClient"
```

---

### Task 6: Catalog — org lookup

**Files:**
- Create: `src/evergreen_holder/catalog.py`, `tests/test_catalog.py`

**Interfaces:**
- Consumes: a client with `call`/`call_one` (real `GatewayClient` or `FakeClient`).
- Produces:
  - `find_orgs(client, fragment: str) -> list[dict]` — each `{"id", "name", "shortname", "ancestors": [{"id", "name"}, ...]}` (ancestors from the root down, excluding the org itself), case-insensitive substring match on `name` or `shortname`, sorted by id.
  - `org_names(client, ids: list[int]) -> dict[int, str]` — id → name for the given ids.

Background: `open-ils.actor.org_tree.retrieve` returns one `aou` whose `children` is a list of `aou`, recursively. After decoding, each is a dict with `id`, `name`, `shortname`, `children`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_catalog.py
from evergreen_holder.catalog import find_orgs, org_names


def test_find_orgs_matches_name_case_insensitively_with_ancestors(fake_client):
    hits = find_orgs(fake_client, "hocutt")
    assert len(hits) == 1
    h = hits[0]
    assert h["id"] == 501
    assert h["name"] == "Hocutt-Ellington Memorial Library"
    assert [a["id"] for a in h["ancestors"]] == [1, 500]
    assert h["ancestors"][0]["name"] == "NC Cardinal"
    assert h["ancestors"][1]["name"] == "Clayton Library System"


def test_find_orgs_matches_shortname(fake_client):
    hits = find_orgs(fake_client, "FONTANA_HQ")
    assert hits and hits[0]["shortname"] == "FONTANA_HQ"


def test_find_orgs_no_match(fake_client):
    assert find_orgs(fake_client, "zzzznotalibrary") == []


def test_org_names(fake_client):
    names = org_names(fake_client, [501, 500, 1])
    assert names == {501: "Hocutt-Ellington Memorial Library", 500: "Clayton Library System", 1: "NC Cardinal"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: FAIL with "No module named 'evergreen_holder.catalog'"

- [ ] **Step 3: Implement**

```python
# src/evergreen_holder/catalog.py
"""Read-only catalog operations: org lookup and bib search."""
from __future__ import annotations

from typing import Any

ACTOR = "open-ils.actor"
SEARCH = "open-ils.search"
SUPERCAT = "open-ils.supercat"


def _org_tree(client) -> dict:
    return client.call_one(ACTOR, "open-ils.actor.org_tree.retrieve")


def _walk(node: dict, ancestors: list[dict]):
    yield node, ancestors
    for child in node.get("children") or []:
        yield from _walk(child, ancestors + [{"id": node["id"], "name": node["name"]}])


def find_orgs(client, fragment: str) -> list[dict]:
    frag = fragment.lower()
    hits = []
    for node, ancestors in _walk(_org_tree(client), []):
        name = node.get("name") or ""
        short = node.get("shortname") or ""
        if frag in name.lower() or frag in short.lower():
            hits.append({"id": node["id"], "name": name, "shortname": short, "ancestors": ancestors})
    return sorted(hits, key=lambda h: h["id"])


def org_names(client, ids: list[int]) -> dict[int, str]:
    wanted = set(ids)
    return {node["id"]: node["name"] for node, _ in _walk(_org_tree(client), []) if node["id"] in wanted}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: PASS. If the recorded root's `name` is not exactly `"NC Cardinal"`, correct the assertion to the recorded value (check with `python -c "import json;d=json.load(open('tests/fixtures/catalog.json'));print(d['open-ils.actor.org_tree.retrieve|[]'][0]['name'])"`).

- [ ] **Step 5: Commit**

```bash
git add src/evergreen_holder/catalog.py tests/test_catalog.py
git commit -m "feat: org unit lookup by name with ancestor chain"
```

---

### Task 7: Catalog — bib search with formats and copy tiers

**Files:**
- Modify: `src/evergreen_holder/catalog.py`
- Modify: `tests/test_catalog.py`

**Interfaces:**
- Produces:
  - `SEARCH_FILTERS = "search_format(book) -item_form(d)"`, `MAX_RESULTS = 25`
  - `parse_isbns(marcxml: str) -> list[dict]` — `[{"isbn": "039335668X", "label": "paperback"}, ...]` from `020 $a` (normalised: strip everything after the first space, uppercase) and `020 $q` (parenthesis stripped, lowercased, `""` if absent).
  - `search_bibs(client, query: str, branch_id: int, system_id: int, consortium_id: int) -> list[dict]` — list of result dicts in the shape shown in the spec (`bib_id`, `title`, `author`, `year`, `isbns`, `formats`, `physical_description`, `large_print`, `copies`).

Background:
- Query call: `client.call_one(SEARCH, "open-ils.search.biblio.multiclass.query", {"limit": MAX_RESULTS, "org_unit": consortium_id}, f"{query} {SEARCH_FILTERS}", 1)` → `{"count": n, "ids": [[bib_id, ...], ...]}`.
- Per bib: `mods_slim.retrieve(bib_id)` → `mvr` dict (`title`, `author`, `pubdate`, `physical_description`); `open-ils.supercat.record.object.retrieve(bib_id)` → payload `[[bre]]` i.e. `call_one` returns a list whose first element is the `bre` dict with `marc`; `copy_count(branch_id, bib_id)` → payload `[[{org_unit, depth, count, available}, ...]]`, again `call_one` returns the inner list.
- `formats` = sorted unique non-empty labels from `isbns`.
- `large_print` = `"large print" in physical_description.lower()`.
- Tier lookup: pick the `copy_count` entry whose `org_unit` equals the configured id; if absent (e.g. bib has no copies under that org), report `{"available": 0, "total": 0}`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_catalog.py`)

```python
from evergreen_holder.catalog import parse_isbns, search_bibs

MARC = """<record xmlns="http://www.loc.gov/MARC21/slim">
<datafield tag="020" ind1=" " ind2=" "><subfield code="a">039335668X</subfield><subfield code="q">(paperback)</subfield></datafield>
<datafield tag="020" ind1=" " ind2=" "><subfield code="a">9780393635522 (hardcover)</subfield></datafield>
<datafield tag="020" ind1=" " ind2=" "><subfield code="a">1234567890</subfield></datafield>
<datafield tag="020" ind1=" " ind2=" "><subfield code="z">0000000000</subfield></datafield>
</record>"""


def test_parse_isbns_reads_a_and_q_subfields():
    assert parse_isbns(MARC) == [
        {"isbn": "039335668X", "label": "paperback"},
        {"isbn": "9780393635522", "label": ""},
        {"isbn": "1234567890", "label": ""},
    ]


def test_search_bibs_shapes_results(fake_client):
    results = search_bibs(fake_client, "the overstory powers", 501, 500, 1)
    by_id = {r["bib_id"]: r for r in results}
    main = by_id[12547531]
    assert main["title"].lower().startswith("the overstory")
    assert main["author"].startswith("Powers")
    assert main["year"] == "2018"
    assert {"isbn": "039335668X", "label": "paperback"} in main["isbns"]
    assert {"isbn": "039363552X", "label": "hardcover"} in main["isbns"]
    assert main["formats"] == ["hardcover", "paperback"]
    assert main["large_print"] is False
    assert set(main["copies"]) == {"branch", "system", "consortium"}
    assert main["copies"]["consortium"]["total"] > main["copies"]["system"]["total"] >= main["copies"]["branch"]["total"]

    pb = by_id[12834206]
    assert pb["formats"] == ["paperback"]
    assert pb["copies"]["branch"] == {"available": 0, "total": 0}


def test_search_bibs_uses_format_filters_and_result_cap(fake_client):
    search_bibs(fake_client, "the overstory powers", 501, 500, 1)
    _, method, params = fake_client.calls[0]
    assert method == "open-ils.search.biblio.multiclass.query"
    assert params[0] == {"limit": 25, "org_unit": 1}
    assert params[1] == "the overstory powers search_format(book) -item_form(d)"


def test_search_bibs_empty(fake_client):
    fake_client.canned[fake_client.key("open-ils.search.biblio.multiclass.query",
                                       ({"limit": 25, "org_unit": 1}, "nothing search_format(book) -item_form(d)", 1))] = [
        {"count": 0, "ids": []}]
    assert search_bibs(fake_client, "nothing", 501, 500, 1) == []
```

The recorded query fixture returns up to 25 bib ids but only two have recorded per-bib responses. Make `search_bibs` testable against that by having it skip a bib when the client raises `KeyError`? **No** — that would hide bugs. Instead, in the test, trim the canned query result to the two recorded ids before calling:

```python
@pytest.fixture
def trimmed_client(fake_client):
    k = fake_client.key("open-ils.search.biblio.multiclass.query",
                        ({"limit": 25, "org_unit": 1}, "the overstory powers search_format(book) -item_form(d)", 1))
    res = dict(fake_client.canned[k][0])
    res["ids"] = [row for row in res["ids"] if row[0] in (12547531, 12834206)]
    fake_client.canned[k] = [res]
    return fake_client
```

and use `trimmed_client` instead of `fake_client` in `test_search_bibs_shapes_results` and `test_search_bibs_uses_format_filters_and_result_cap`. Add `import pytest` at the top of the test file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: FAIL with "cannot import name 'parse_isbns'"

- [ ] **Step 3: Implement** (append to `catalog.py`)

```python
import re
import xml.etree.ElementTree as ET

MARC_NS = "{http://www.loc.gov/MARC21/slim}"
SEARCH_FILTERS = "search_format(book) -item_form(d)"
MAX_RESULTS = 25


def parse_isbns(marcxml: str) -> list[dict]:
    root = ET.fromstring(marcxml)
    out = []
    for df in root.iter(f"{MARC_NS}datafield"):
        if df.get("tag") != "020":
            continue
        a = q = None
        for sf in df.iter(f"{MARC_NS}subfield"):
            if sf.get("code") == "a" and a is None:
                a = (sf.text or "").strip()
            elif sf.get("code") == "q" and q is None:
                q = (sf.text or "").strip()
        if not a:
            continue
        isbn = a.split()[0].upper()
        label = re.sub(r"[()\s:;.]", "", q).lower() if q else ""
        out.append({"isbn": isbn, "label": label})
    return out


def _tier(counts: list[dict], org_id: int) -> dict:
    for c in counts:
        if int(c.get("org_unit", -1)) == org_id:
            return {"available": int(c.get("available", 0)), "total": int(c.get("count", 0))}
    return {"available": 0, "total": 0}


def _first(payload: Any) -> Any:
    """supercat and copy_count wrap their result in an extra list."""
    if isinstance(payload, list) and payload and isinstance(payload[0], (dict, list)):
        return payload[0] if isinstance(payload[0], dict) else payload
    return payload


def search_bibs(client, query: str, branch_id: int, system_id: int, consortium_id: int) -> list[dict]:
    res = client.call_one(SEARCH, "open-ils.search.biblio.multiclass.query",
                          {"limit": MAX_RESULTS, "org_unit": consortium_id}, f"{query} {SEARCH_FILTERS}", 1)
    ids = [int(row[0]) for row in (res or {}).get("ids", [])]
    results = []
    for bib_id in ids:
        mods = client.call_one(SEARCH, "open-ils.search.biblio.record.mods_slim.retrieve", bib_id) or {}
        bre_payload = client.call_one(SUPERCAT, "open-ils.supercat.record.object.retrieve", bib_id)
        bre = bre_payload[0] if isinstance(bre_payload, list) else bre_payload
        marc = (bre or {}).get("marc") or ""
        counts = client.call_one(SEARCH, "open-ils.search.biblio.record.copy_count", branch_id, bib_id) or []
        isbns = parse_isbns(marc) if marc else []
        physical = mods.get("physical_description") or ""
        results.append({
            "bib_id": bib_id,
            "title": mods.get("title") or "",
            "author": mods.get("author") or "",
            "year": str(mods.get("pubdate") or ""),
            "isbns": isbns,
            "formats": sorted({i["label"] for i in isbns if i["label"]}),
            "physical_description": physical,
            "large_print": "large print" in physical.lower(),
            "copies": {
                "branch": _tier(counts, branch_id),
                "system": _tier(counts, system_id),
                "consortium": _tier(counts, consortium_id),
            },
        })
    return results
```

Delete the unused `_first` helper before committing if it is not needed once the fixture shapes are confirmed (check the recorded fixture: `call_one` for supercat returns a list of one `bre` dict; for copy_count it returns the list of tier dicts).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/evergreen_holder/catalog.py tests/test_catalog.py
git commit -m "feat: bib search with ISBN format labels and three-tier copy counts"
```

---

### Task 8: `evergreen-config` and `evergreen-search` CLIs

**Files:**
- Create: `src/evergreen_holder/cli_common.py`, `src/evergreen_holder/cli_config.py`, `src/evergreen_holder/cli_search.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `cli_common.emit(obj) -> None` (prints JSON, `indent=1`, `ensure_ascii=False`), `cli_common.fail(error: str, desc: str, code: int) -> NoReturn`, `cli_common.build_client(cfg: dict) -> GatewayClient` (loads IDL from `idl_path()`, raises `ConfigError` if not cached).
  - `cli_config.main(argv: list[str] | None = None) -> int` with subcommands `doctor`, `set <key> <value>`, `set-password`.
  - `cli_search.main(argv=None) -> int` with `<query>` or `--orgs <fragment>`.
- Consumes: `config.*`, `catalog.*`, `idl.*`, `gateway.GatewayClient`.

`set-password` is implemented in Task 10; here it only exists as a subcommand that calls `cli_config.cmd_set_password()` which is stubbed to return 1 with `{"error": "not_implemented"}`. (That stub is replaced in Task 10; it is not a placeholder in the finished product.)

`cli_search` needs the org names for the three tiers to include in its output; get them with `catalog.org_names` (one org-tree call).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cli.py
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
```

Move the `trimmed_client` fixture from `tests/test_catalog.py` into `tests/conftest.py` so both test files can use it.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with "No module named 'evergreen_holder.cli_config'"

- [ ] **Step 3: Implement**

```python
# src/evergreen_holder/cli_common.py
from __future__ import annotations

import json
import sys
from typing import NoReturn

from .config import ConfigError, idl_path
from .gateway import GatewayClient
from .idl import load_field_map


def emit(obj) -> None:
    print(json.dumps(obj, indent=1, ensure_ascii=False))


def fail(error: str, desc: str, code: int) -> NoReturn:
    emit({"error": error, "desc": desc})
    sys.exit(code)


def build_client(cfg: dict) -> GatewayClient:
    p = idl_path()
    if not p.exists():
        raise ConfigError(f"IDL not cached at {p}; run `evergreen-config set base_url <url>` to fetch it")
    return GatewayClient(cfg["base_url"], load_field_map(p))
```

```python
# src/evergreen_holder/cli_config.py
"""evergreen-config: doctor | set <key> <value> | set-password"""
from __future__ import annotations

import argparse
import sys

from . import config
from .cli_common import emit
from .idl import fetch_idl


def cmd_doctor() -> int:
    d = config.doctor()
    emit(d)
    return 0 if d["ok"] else 1


def cmd_set(key: str, value: str) -> int:
    try:
        config.set_value(key, value)
        if key == "base_url":
            fetch_idl(value, config.idl_path())
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    except Exception as e:  # network failure fetching IDL
        emit({"error": "idl_fetch", "desc": str(e)})
        return 1
    emit({"ok": True, "key": key})
    return 0


def cmd_set_password() -> int:
    emit({"error": "not_implemented", "desc": "set-password arrives in a later task"})
    return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evergreen-config")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor", help="report config state as JSON; exit 0 only when complete")
    s = sub.add_parser("set", help="set one config key")
    s.add_argument("key")
    s.add_argument("value")
    sub.add_parser("set-password", help="interactively set the password (never via Claude)")
    a = p.parse_args(argv)
    if a.cmd == "doctor":
        return cmd_doctor()
    if a.cmd == "set":
        return cmd_set(a.key, a.value)
    return cmd_set_password()


if __name__ == "__main__":
    sys.exit(main())
```

```python
# src/evergreen_holder/cli_search.py
"""evergreen-search "<title> <author>" | --orgs <name fragment>"""
from __future__ import annotations

import argparse
import sys

from . import config
from .catalog import find_orgs, org_names, search_bibs
from .cli_common import build_client, emit
from .gateway import GatewayError, IlsEvent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evergreen-search")
    p.add_argument("query", nargs="?", help="title and author words")
    p.add_argument("--orgs", metavar="NAME", help="find org units whose name contains NAME")
    a = p.parse_args(argv)
    if not a.query and not a.orgs:
        p.error("give a query or --orgs")
    try:
        cfg = config.read_raw() if a.orgs else config.load()
        if "base_url" not in cfg:
            raise config.ConfigError("base_url is not set")
        client = build_client(cfg)
        if a.orgs:
            emit({"orgs": find_orgs(client, a.orgs)})
            return 0
        names = org_names(client, [cfg["branch_id"], cfg["system_id"], cfg["consortium_id"]])
        results = search_bibs(client, a.query, cfg["branch_id"], cfg["system_id"], cfg["consortium_id"])
        emit({
            "preferred_format": cfg["preferred_format"],
            "branch": {"id": cfg["branch_id"], "name": names.get(cfg["branch_id"], "")},
            "system": {"id": cfg["system_id"], "name": names.get(cfg["system_id"], "")},
            "consortium": {"id": cfg["consortium_id"], "name": names.get(cfg["consortium_id"], "")},
            "results": results,
        })
        return 0
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    except IlsEvent as e:
        emit({"error": e.textcode, "desc": e.desc})
        return 2
    except GatewayError as e:
        emit({"error": "gateway", "desc": str(e)})
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests -v`
Expected: all PASS

- [ ] **Step 5: Smoke-test the real binaries against the live catalog** (network; read-only)

```bash
uv run evergreen-config set base_url https://johnston.nccardinal.org
uv run evergreen-config set branch_id 501
uv run evergreen-config set system_id 500
uv run evergreen-config set consortium_id 1
uv run evergreen-config set username placeholder
uv run evergreen-config set preferred_format hardcover
uv run evergreen-search --orgs hocutt
uv run evergreen-search "the overstory powers" | head -60
```

Expected: `--orgs` prints id 501 with ancestors `[1, 500]`; the search prints results including bib `12547531` with `formats: ["hardcover", "paperback"]`. Then `uv run evergreen-config set username <real username>` if it differs.

- [ ] **Step 6: Commit**

```bash
git add src/evergreen_holder/cli_common.py src/evergreen_holder/cli_config.py src/evergreen_holder/cli_search.py tests/test_cli.py tests/conftest.py tests/test_catalog.py
git commit -m "feat: evergreen-config and evergreen-search CLIs"
```

---

### Task 9: Holds module

**Files:**
- Create: `src/evergreen_holder/holds.py`, `tests/test_holds.py`

**Interfaces:**
- Produces:
  - `login(client, username: str, password: str) -> str` — authtoken; raises `IlsEvent` on `LOGIN_FAILED`.
  - `patron_id(client, authtoken: str) -> int`
  - `hold_payload(patron: int, pickup_lib: int) -> dict` → `{"patronid": patron, "pickup_lib": pickup_lib, "hold_type": "T"}`
  - `place_title_hold(client, authtoken: str, patron: int, pickup_lib: int, bib_id: int) -> int` — hold id; raises `IlsEvent` when the per-target `result` is an event.
  - `queue_stats(client, authtoken: str, hold_id: int) -> dict` — the raw stats dict.
  - `logout(client, authtoken: str) -> None` — best effort, swallows errors.

Background:
- `open-ils.auth.login({"username", "password", "type": "opac"})` → `{"ilsevent": 0, "textcode": "SUCCESS", "payload": {"authtoken": ..., "authtime": ...}}` (the client does not raise for `ilsevent` 0). Failure → `IlsEvent("LOGIN_FAILED")` raised by the client.
- `open-ils.auth.session.retrieve(authtoken)` → `au` dict with `id`.
- `open-ils.circ.holds.test_and_create.batch(authtoken, params, [bib_id])` → payload list of `{"target": bib_id, "result": <int hold id | event dict>}`. The gateway client only raises for *top-level* events, so the per-target `result` must be checked here with `is_event`.
- `open-ils.circ.hold.queue_stats.retrieve(authtoken, hold_id)` → `{"total_holds", "queue_position", "potential_copies", "status", "estimated_wait"}`.
- `open-ils.auth.session.delete(authtoken)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_holds.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_holds.py -v`
Expected: FAIL with "No module named 'evergreen_holder.holds'"

- [ ] **Step 3: Implement**

```python
# src/evergreen_holder/holds.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_holds.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/evergreen_holder/holds.py tests/test_holds.py
git commit -m "feat: login, title hold placement, and queue stats"
```

---

### Task 10: `evergreen-config set-password` (interactive, verified)

**Files:**
- Modify: `src/evergreen_holder/cli_config.py` (replace `cmd_set_password`)
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `cli_config.cmd_set_password(prompt=getpass.getpass) -> int`. Refuses to run when stdin is not a TTY (exit 1, `{"error": "not_a_tty"}`). Prompts twice, requires a match, verifies with `holds.login`, writes with `config.set_password` only on success, logs out.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`)

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -k set_password`
Expected: FAIL (`cmd_set_password() got an unexpected keyword argument 'prompt'` / wrong error codes)

- [ ] **Step 3: Implement** (replace `cmd_set_password` in `cli_config.py`; add imports)

```python
import getpass
import sys

from .cli_common import build_client, emit
from .gateway import GatewayError, IlsEvent
from . import holds


def cmd_set_password(prompt=getpass.getpass) -> int:
    if not sys.stdin.isatty():
        emit({"error": "not_a_tty", "desc": "set-password must be run by a person in an interactive terminal"})
        return 1
    try:
        cfg = config.load()
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    first = prompt("Evergreen password: ")
    second = prompt("Again: ")
    if first != second or not first:
        emit({"error": "mismatch", "desc": "passwords did not match (or were empty); nothing written"})
        return 1
    try:
        client = build_client(cfg)
        token = holds.login(client, cfg["username"], first)
        holds.logout(client, token)
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1
    except IlsEvent as e:
        emit({"error": e.textcode, "desc": e.desc})
        return 2
    except GatewayError as e:
        emit({"error": "gateway", "desc": str(e)})
        return 2
    config.set_password(first)
    emit({"ok": True, "key": "password", "verified": True})
    return 0
```

Remove the old stub and the `from .cli_common import emit` line it used (keep a single import line for `build_client, emit`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests -v`
Expected: all PASS

- [ ] **Step 5: Set the real password** (human step, real terminal, not via Claude)

Run: `uv run evergreen-config set-password`
Expected: two hidden prompts, then `{"ok": true, "key": "password", "verified": true}`. Then `uv run evergreen-config doctor` → `"ok": true`.

- [ ] **Step 6: Commit**

```bash
git add src/evergreen_holder/cli_config.py tests/test_cli.py
git commit -m "feat: interactive, login-verified set-password"
```

---

### Task 11: `evergreen-hold` CLI with `--dry-run`

**Files:**
- Create: `src/evergreen_holder/cli_hold.py`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Produces: `cli_hold.main(argv=None) -> int`. Arguments: `bib_id` (int), `--dry-run`. No password argument exists; `argparse` must not define one.
- Output on success: `{"hold_id", "bib_id", "pickup_lib", "queue_position", "total_holds", "potential_copies", "estimated_wait", "status"}`.
- Output on dry run: `{"dry_run": true, "bib_id", "pickup_lib", "patron_id", "payload": {...}, "would_call": "open-ils.circ.holds.test_and_create.batch"}` and no create call is made.
- Missing password → `{"error": "config", "desc": "...set-password..."}` exit 1.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`)

```python
from evergreen_holder import cli_hold


def canned_auth(fake_client):
    fake_client.canned[fake_client.key("open-ils.auth.login", ({"username": "eric", "password": "pw", "type": "opac"},))] = [
        {"ilsevent": 0, "textcode": "SUCCESS", "payload": {"authtoken": "tok", "authtime": 1}}]
    fake_client.canned[fake_client.key("open-ils.auth.session.retrieve", ("tok",))] = [{"_class": "au", "id": 777}]
    fake_client.canned[fake_client.key("open-ils.auth.session.delete", ("tok",))] = [1]


def test_hold_dry_run_never_calls_create(full_config, fake_client, monkeypatch, capsys):
    canned_auth(fake_client)
    monkeypatch.setattr(cli_hold, "build_client", lambda cfg: fake_client)
    assert cli_hold.main(["12547531", "--dry-run"]) == 0
    d = out(capsys)
    assert d["dry_run"] is True
    assert d["payload"] == {"patronid": 777, "pickup_lib": 501, "hold_type": "T"}
    assert not any(m == "open-ils.circ.holds.test_and_create.batch" for _, m, _ in fake_client.calls)


def test_hold_places_and_reports_queue(full_config, fake_client, monkeypatch, capsys):
    canned_auth(fake_client)
    fake_client.canned[fake_client.key("open-ils.circ.holds.test_and_create.batch",
                                       ("tok", {"patronid": 777, "pickup_lib": 501, "hold_type": "T"}, [12547531]))] = [
        {"target": 12547531, "result": 99001}]
    fake_client.canned[fake_client.key("open-ils.circ.hold.queue_stats.retrieve", ("tok", 99001))] = [
        {"total_holds": 7, "queue_position": 3, "potential_copies": 102, "status": 2, "estimated_wait": 0}]
    monkeypatch.setattr(cli_hold, "build_client", lambda cfg: fake_client)
    assert cli_hold.main(["12547531"]) == 0
    d = out(capsys)
    assert d["hold_id"] == 99001 and d["queue_position"] == 3 and d["total_holds"] == 7
    assert d["pickup_lib"] == 501
    assert any(m == "open-ils.auth.session.delete" for _, m, _ in fake_client.calls)


def test_hold_event_exit_2(full_config, fake_client, monkeypatch, capsys):
    canned_auth(fake_client)
    fake_client.canned[fake_client.key("open-ils.circ.holds.test_and_create.batch",
                                       ("tok", {"patronid": 777, "pickup_lib": 501, "hold_type": "T"}, [12547531]))] = [
        {"target": 12547531, "result": {"ilsevent": 1707, "textcode": "HOLD_EXISTS", "desc": "dup"}}]
    monkeypatch.setattr(cli_hold, "build_client", lambda cfg: fake_client)
    assert cli_hold.main(["12547531"]) == 2
    assert out(capsys)["error"] == "HOLD_EXISTS"


def test_hold_requires_password(xdg, monkeypatch, capsys):
    monkeypatch.setattr(cli_config, "fetch_idl", lambda base_url, dest: dest.parent.mkdir(parents=True, exist_ok=True) or dest.write_text("<IDL/>"))
    for k_, v in [("base_url", "https://example.org"), ("branch_id", "501"), ("system_id", "500"),
                  ("consortium_id", "1"), ("username", "eric"), ("preferred_format", "hardcover")]:
        cli_config.main(["set", k_, v])
    assert cli_hold.main(["12547531"]) == 1
    assert "set-password" in out(capsys)["desc"]


def test_hold_has_no_password_argument():
    import argparse
    with pytest.raises(SystemExit):
        cli_hold.main(["12547531", "--password", "x"])
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -v -k hold`
Expected: FAIL with "No module named 'evergreen_holder.cli_hold'"

- [ ] **Step 3: Implement**

```python
# src/evergreen_holder/cli_hold.py
"""evergreen-hold <bib_id> [--dry-run]: place a title hold for pickup at the configured branch.

The password is read from the config file only. This tool is meant to be run only after a
human has confirmed the specific bib in the active session."""
from __future__ import annotations

import argparse
import sys

from . import config, holds
from .cli_common import build_client, emit
from .gateway import GatewayError, IlsEvent


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="evergreen-hold")
    p.add_argument("bib_id", type=int)
    p.add_argument("--dry-run", action="store_true", help="log in and build the payload, but do not place the hold")
    a = p.parse_args(argv)
    try:
        cfg = config.load()
        if not cfg.get("password"):
            raise config.ConfigError("password is not set; run `evergreen-config set-password` in a terminal")
        client = build_client(cfg)
    except config.ConfigError as e:
        emit({"error": "config", "desc": str(e)})
        return 1

    token = None
    try:
        token = holds.login(client, cfg["username"], cfg["password"])
        patron = holds.patron_id(client, token)
        pickup = cfg["branch_id"]
        if a.dry_run:
            emit({"dry_run": True, "bib_id": a.bib_id, "pickup_lib": pickup, "patron_id": patron,
                  "payload": holds.hold_payload(patron, pickup),
                  "would_call": "open-ils.circ.holds.test_and_create.batch"})
            return 0
        hold_id = holds.place_title_hold(client, token, patron, pickup, a.bib_id)
        stats = holds.queue_stats(client, token, hold_id)
        emit({"hold_id": hold_id, "bib_id": a.bib_id, "pickup_lib": pickup,
              "queue_position": stats.get("queue_position"), "total_holds": stats.get("total_holds"),
              "potential_copies": stats.get("potential_copies"), "estimated_wait": stats.get("estimated_wait"),
              "status": stats.get("status")})
        return 0
    except IlsEvent as e:
        emit({"error": e.textcode, "desc": e.desc})
        return 2
    except GatewayError as e:
        emit({"error": "gateway", "desc": str(e)})
        return 2
    finally:
        if token:
            holds.logout(client, token)


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests -v`
Expected: all PASS

- [ ] **Step 5: Live dry run** (network; logs in with the real account; places nothing)

Run: `uv run evergreen-hold 12547531 --dry-run`
Expected: `{"dry_run": true, "bib_id": 12547531, "pickup_lib": 501, "patron_id": <int>, "payload": {...}, "would_call": "..."}`. If `LOGIN_FAILED`, re-run `evergreen-config set-password`.

- [ ] **Step 6: Commit**

```bash
git add src/evergreen_holder/cli_hold.py tests/test_cli.py
git commit -m "feat: evergreen-hold CLI with dry-run"
```

---

### Task 12: Skill, install, README, and the permission gate

**Files:**
- Create: `skill/SKILL.md`, `Makefile`, `README.md`

**Interfaces:**
- Consumes: the three CLIs' argument and JSON shapes from Tasks 8, 10, 11.

- [ ] **Step 1: Write the skill**

````markdown
# skill/SKILL.md
---
name: library-hold
description: Find a book in the user's Evergreen ILS library catalog, report where copies are available, and place a title hold only after the user explicitly confirms. Use when the user asks to find, look up, check availability of, or place a hold on a book at their library.
---

# /library-hold <title> by <author>

You drive three deterministic CLIs. You never talk to the library system directly, never
compose URLs or curl commands, and never edit the config file by hand.

- `evergreen-config doctor` / `evergreen-config set <key> <value>`
- `evergreen-search "<title> <author>"` / `evergreen-search --orgs <name>`
- `evergreen-hold <bib_id> [--dry-run]`

All three print one JSON document. Non-zero exit means the JSON is `{"error", "desc"}`.

## Hard rules

1. **Never run `evergreen-hold` (without `--dry-run`) until the user has answered an
   AskUserQuestion that names the exact bib id, title, format labels, and pickup library
   and chosen "Yes, place the hold".** One confirmation per hold. A "yes" earlier in the
   conversation does not carry over.
2. **Never ask the user for their library password, never run `evergreen-config
   set-password`, and never pass a password on any command line.** If the password is
   not set, tell the user to run `evergreen-config set-password` in their own terminal
   and stop.
3. Do not place holds on more than one bib per confirmation.

## Step 1: doctor

Run `evergreen-config doctor`. If `ok` is true, go to Step 2. Otherwise:

- If `mode_ok` is false: tell the user to run `chmod 600 <config_path>` and stop.
- For each key in `missing`, collect it with AskUserQuestion and write it with
  `evergreen-config set <key> <value>`:
  - `base_url`: the library's Evergreen catalog origin, e.g. `https://johnston.nccardinal.org`.
    Set this first; it also downloads the server's IDL.
  - `branch_id`, `system_id`, `consortium_id`: ask for the user's home branch **by name**,
    run `evergreen-search --orgs "<name>"`, show the matching org and its `ancestors`
    chain, and confirm. `branch_id` = the match's `id`; `consortium_id` = `ancestors[0].id`;
    `system_id` = the ancestor directly above the branch (`ancestors[-1].id`). If the branch
    sits directly under the consortium, set `system_id` equal to `branch_id`.
  - `username`: the user's catalog login name.
  - `preferred_format`: `hardcover` or `paperback`.
- If `password_set` is false: tell the user to run `evergreen-config set-password` in a
  terminal (it prompts without echo and verifies the login), then stop. Do not continue
  to the search until they say it's done and `doctor` reports `ok: true`.

## Step 2: search

Run `evergreen-search "<title> <author>"`. From `results`:

- Drop hits that are clearly not the requested book (different title/author). Use judgment;
  the catalog's relevance ranking is loose.
- Rank the remaining hits by `preferred_format`: bibs whose `formats` contain only the
  preferred format first, mixed-format bibs second, other-only last, then by consortium
  availability. `formats: []` means the record has no labeled ISBNs; treat it as unknown.
- Flag `large_print: true` hits as large print; they are usually not what the user wants.
- Show the user a short list: bib id, title, year, formats, and the three tiers as
  "<available>/<total>" for branch, system, consortium, using the names from the output.
- If a bib is mixed-format, say so: "this record has both hardcover and paperback copies;
  the hold is filled by whichever comes free first."
- If there are no plausible hits, say so and stop. Do not try alternative spellings more
  than once.

## Step 3: decide by tier

For the bib the user wants:

- `copies.branch.available > 0` → tell them it is on the shelf at `<branch name>` right now
  and that no hold is needed. Do not offer a hold unless they ask.
- else `copies.system.available > 0` → offer the hold (Step 4).
- else `copies.consortium.available > 0` → say it is not available within `<system name>`
  and would ship from elsewhere in `<consortium name>` (a week or more). Ask whether they'd
  rather pick another book, with placing the hold anyway as the non-default option.
- else → say no copies are available anywhere right now; a hold would queue. Offer it.

## Step 4: confirm and place

AskUserQuestion with the exact bib id, title, formats, and pickup library name, options
"Yes, place the hold" / "No". Only on "Yes" run `evergreen-hold <bib_id>`.

Report `hold_id`, `queue_position` of `total_holds`, and `potential_copies`. On
`{"error": "HOLD_EXISTS"}` tell the user they already have a hold on this title. On any
other error, show `desc` and stop; do not retry.
````

- [ ] **Step 2: Write the Makefile and README**

```makefile
# Makefile
SKILL_DIR := $(HOME)/.claude/skills/library-hold

.PHONY: install uninstall test

install:
	uv tool install --force .
	mkdir -p $(HOME)/.claude/skills
	ln -sfn $(CURDIR)/skill $(SKILL_DIR)
	@echo "Installed evergreen-config, evergreen-search, evergreen-hold and linked $(SKILL_DIR)"
	@echo "Now add to ~/.claude/settings.json:  \"permissions\": {\"ask\": [\"Bash(evergreen-hold:*)\"]}"

uninstall:
	uv tool uninstall evergreen-holder || true
	rm -f $(SKILL_DIR)

test:
	uv run pytest
```

````markdown
# README.md
# evergreen-holder

Find a book in an Evergreen ILS catalog (e.g. NC Cardinal), see where copies are
available, and place a title hold — with a human confirming every hold. Ships as three
small CLIs plus a Claude Code skill (`/library-hold`) that drives them.

## Install

```bash
make install
```

Then add to `~/.claude/settings.json` so the hold command always prompts, even in auto mode:

```json
{ "permissions": { "ask": ["Bash(evergreen-hold:*)"] } }
```

## Configure

Config lives at `$XDG_CONFIG_HOME/evergreen-holder/config.toml` (default
`~/.config/evergreen-holder/config.toml`), mode 0600. The skill walks you through it, or:

```bash
evergreen-config set base_url https://johnston.nccardinal.org   # also caches the server IDL
evergreen-search --orgs "hocutt"                                 # find your branch and its ancestors
evergreen-config set branch_id 501
evergreen-config set system_id 500
evergreen-config set consortium_id 1
evergreen-config set username <your catalog username>
evergreen-config set preferred_format hardcover
evergreen-config set-password                                    # interactive; verifies login
evergreen-config doctor
```

The password is only ever entered through `set-password` in your own terminal. Claude never
asks for it.

## Use

```
/library-hold The Overstory by Richard Powers
```

Or by hand:

```bash
evergreen-search "the overstory powers"
evergreen-hold 12547531 --dry-run
evergreen-hold 12547531
```

## Other Evergreen networks

Everything network-specific is in the config file. `base_url` is the catalog origin;
the three org ids come from `evergreen-search --orgs`. The fieldmapper IDL is fetched
from `<base_url>/reports/fm_IDL.xml`, so object decoding follows the server's version.

## Development

```bash
uv sync
uv run pytest
uv run python tests/record_fixtures.py   # re-record live fixtures (network)
```
````

- [ ] **Step 3: Install and add the permission**

Run: `make install`
Expected: `which evergreen-hold` prints a path under `~/.local/bin`; `ls -l ~/.claude/skills/library-hold` shows a symlink to `<repo>/skill`.

Then add the `permissions.ask` entry to `~/.claude/settings.json` (merge with existing content; the file currently has only `enabledPlugins`).

- [ ] **Step 4: Verify the gate actually fires under auto mode**

In a Claude Code session running in auto mode, ask Claude to run `evergreen-hold 12547531 --dry-run`. Expected: a permission prompt appears *before* the command runs. If no prompt appears, the `ask` entry is not overriding auto mode; in that case stop and report this to the user — the gate needs a different mechanism and that is their decision, not the implementer's.

- [ ] **Step 5: End-to-end dry run through the skill**

In Claude Code: `/library-hold The Overstory by Richard Powers`. Expected: doctor passes, the search shows bib 12547531 (mixed hardcover/paperback) and 12834206 (paperback), tiers reported by name, and — because 12547531 has a copy on the shelf at Hocutt-Ellington — Claude says "go get it" rather than offering a hold. Ask Claude to dry-run the hold on 12834206 to see the confirmation prompt and the `--dry-run` output. Do **not** place a real hold as part of implementation; the first real hold is the user's call.

- [ ] **Step 6: Commit**

```bash
git add skill/SKILL.md Makefile README.md
git commit -m "feat: /library-hold skill, install target, and README"
```

---

## Self-review

**Spec coverage:**
- Gateway + IDL decoding → Tasks 2, 3. ✔
- `evergreen-config doctor / set / set-password`, 0600, IDL fetch on `base_url`, password refusal → Tasks 4, 8, 10. ✔
- `evergreen-search` query with `search_format(book) -item_form(d)`, 25 cap, 020 `$q` labels, three tiers by name, `--orgs` with ancestors → Tasks 6, 7, 8. ✔
- `evergreen-hold` with login, T hold, queue stats, `--dry-run`, password from file only, exit 2 on events → Tasks 9, 11. ✔
- Skill flow (doctor → search → tier logic → AskUserQuestion → hold), never asks for password, hardcover ranking, mixed-bib messaging → Task 12. ✔
- Gate: separate executables (Task 1 `project.scripts`), skill mandate, `permissions.ask` verified (Task 12 Step 4). ✔
- Non-goals: nothing in the plan implements Open Library, ranking in the tool, pre-hold checks, or token caching. ✔

**Placeholder scan:** The `cmd_set_password` stub in Task 8 is explicitly replaced in Task 10. The `_first` helper in Task 7 is marked for deletion once fixture shapes are confirmed; the implementer must not leave it dead. No "TBD"/"handle errors" steps remain.

**Type consistency:** `FakeClient.key(method, params_tuple)` is used identically in conftest, test_holds, and test_cli. `hold_payload(patron, pickup_lib)` returns `{"patronid", "pickup_lib", "hold_type"}` and Task 11's tests assert exactly that. `config.load()` does not require `password`; `cli_hold` checks it separately, matching `test_hold_requires_password`. `build_client` is defined in `cli_common` and imported into `cli_config`, `cli_search`, `cli_hold`, which is why the tests monkeypatch it on each module.
