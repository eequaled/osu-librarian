"""Shared helpers for the owned CLI/parser/auth test files.

Centralises the loopback HTTP helper plus a settings.json fixture builder.
Read-only import of TOKEN_PATH from src.server is intentional (constant only).
tests/test_server.py keeps its own local helper (not owned here).
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from src.server import TOKEN_PATH  # noqa: F401  (re-exported for fixtures)

__all__ = ["req", "make_settings", "write_settings", "TOKEN_PATH"]


def req(method, url, body=None, headers=None):
    """Loopback HTTP helper: (status, headers dict, raw body bytes)."""
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, headers=headers or {},
                               method=method)
    if body is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def make_settings(api_overrides=None, **overrides):
    """Settings.json dict with stable defaults + merged api overrides."""
    api = {
        "client_id": 12345,
        "client_secret": "shhh-test-secret",
        "redirect_uri": "http://localhost:8787/api/auth/callback",
        "user_id": 0,
    }
    if api_overrides:
        api.update(api_overrides)
    base = {
        "mode": "stable",
        "stable_dir": "/tmp/fake-stable",
        "songs_dir": "/tmp/fake-stable/Songs",
        "osu_db": "",
        "scores_db": "",
        "lazer_dir": "",
        "realm_export": "",
        "api": api,
    }
    base.update(overrides)
    return base


def write_settings(settings=None, path="settings.json",
                   api_overrides=None, **overrides):
    """Write a settings.json fixture; returns the dict that was written."""
    if settings is None:
        settings = make_settings(api_overrides=api_overrides, **overrides)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(settings, f)
    return settings
