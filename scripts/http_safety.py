"""Restrict credential-bearing validation requests to the explicit service origin."""
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler


def service_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if (not parsed.hostname or parsed.username or parsed.password or parsed.query
            or parsed.fragment or parsed.path not in ('', '/')
            or any(c.isspace() for c in value)):
        raise ValueError('Use a service origin without credentials, path, query or fragment')
    if parsed.scheme != 'https' and not (
        parsed.scheme == 'http' and parsed.hostname == '127.0.0.1'
    ):
        raise ValueError('Remote services require HTTPS; HTTP is limited to 127.0.0.1')
    _ = parsed.port  # Validate port syntax before loading credentials.
    return value.rstrip('/')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise HTTPError(req.full_url, code, 'Redirects are not allowed', headers, fp)
