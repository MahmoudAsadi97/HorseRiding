"""Handover helpers must never forward saved credentials through a redirect."""
import importlib.util
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request

import pytest


def helper():
    path = Path(__file__).parents[1] / 'scripts/http_safety.py'
    spec = importlib.util.spec_from_file_location('http_safety', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('url', ['http://example.com', 'http://127.0.0.1:8000@evil.test',
    'https://user:pass@example.com', 'https://example.com?token=x', 'file:///etc/passwd'])
def test_unsafe_service_urls_are_rejected(url):
    with pytest.raises(ValueError):
        helper().service_base_url(url)


def test_expected_service_urls_are_supported():
    assert helper().service_base_url('http://127.0.0.1:8000/') == 'http://127.0.0.1:8000'
    assert helper().service_base_url('https://racing.example.com') == 'https://racing.example.com'


def test_redirect_rejected_before_forwarding_authorization():
    handler = helper().NoRedirect()
    request = Request('https://racing.example.com', headers={'Authorization': 'Bearer private'})
    with pytest.raises(HTTPError):
        handler.redirect_request(request, None, 302, 'Found', {}, 'https://another.example.com')
