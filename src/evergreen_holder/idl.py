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
