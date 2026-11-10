"""Origenes de presentacion: validacion pura, sin DNS ni HTTP."""

import pytest

from sgmcp_adapter.public_url import normalize_public_base_url, public_url_metadata


@pytest.mark.parametrize("value,expected", [
    (None, None), ("", None), ("   ", None),
    (" HTTPS://APP.Example.invalid:443/ ", "https://app.example.invalid"),
    ("https://app.example.invalid:8443/", "https://app.example.invalid:8443"),
    ("https://127.0.0.1/", "https://127.0.0.1"),
    ("https://[2001:0db8::1]:8443/", "https://[2001:db8::1]:8443"),
])
def test_normalize_public_origin(value, expected):
    assert normalize_public_base_url(value) == expected


@pytest.mark.parametrize("value", [
    12, False, "http://app.example.invalid", "//app.example.invalid", "https://",
    "https://user:secret@app.example.invalid", "https://@app.example.invalid",
    "https://app.example.invalid/path", "https://app.example.invalid//",
    "https://app.example.invalid?", "https://app.example.invalid#",
    "https://app.example.invalid/?q=secret", "https://app.example.invalid/#token",
    "https://app.example.invalid\\evil", "\nhttps://app.example.invalid",
    "https://app.example.invalid\t", "https://app.example.invalid\x00",
    "https://app.example.invalid\x7f", "https://app.example.invalid\x85", "https://app.ex ample.invalid",
    "https://-bad.invalid", "https://bad-.invalid", "https://bad..invalid",
    "https://bad_.invalid", "https://app.example.invalid.", "https://%65xample.invalid",
    "https://app.example.invalid:0", "https://app.example.invalid:65536",
    "https://app.example.invalid:", "https://app.example.invalid:+443",
    "https://app.example.invalid:abc", "https://999.0.0.1", "https://127.1",
    "https://[::1]evil", "https://[::1%25eth0]", "https://[invalid]",
    "https://[::1", "https://" + "a" * 64 + ".invalid", "x" * 2049,
])
def test_reject_invalid_public_origin(value):
    with pytest.raises(ValueError):
        normalize_public_base_url(value)


def test_public_origin_precedence_and_invalid_environment(monkeypatch):
    monkeypatch.setenv("APP_PUBLIC_BASE_URL", "https://ENV.example.invalid/")
    assert public_url_metadata("https://override.example.invalid") == {
        "public_base_url": "https://override.example.invalid",
        "effective_public_base_url": "https://override.example.invalid",
        "public_base_url_source": "configuration",
    }
    assert public_url_metadata(None) == {
        "public_base_url": None, "effective_public_base_url": "https://env.example.invalid",
        "public_base_url_source": "environment",
    }
    for value in ("", "http://invalid.example", "https://user:secret@invalid.example"):
        monkeypatch.setenv("APP_PUBLIC_BASE_URL", value)
        assert public_url_metadata(None) == {
            "public_base_url": None, "effective_public_base_url": None, "public_base_url_source": None,
        }
    monkeypatch.delenv("APP_PUBLIC_BASE_URL")
    assert public_url_metadata(None)["effective_public_base_url"] is None
