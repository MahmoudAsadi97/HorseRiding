#!/usr/bin/env python3
"""Check HTTP health and required auth without printing credentials or response bodies."""

import argparse
import base64
import json
import urllib.error
import urllib.request
from pathlib import Path

from http_safety import NoRedirect, service_base_url


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    args = parser.parse_args()
    try:
        args.url = service_base_url(args.url)
    except ValueError as error:
        parser.error(str(error))
    config = {}
    for line in args.env_file.read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            config[key.strip()] = value.strip().strip("\"").strip("'")
    basic = base64.b64encode(
        f"{config['ADMIN_USERNAME']}:{config['ADMIN_PASSWORD']}".encode()
    ).decode()
    bearer = f"Bearer {config['API_TOKEN']}"
    opener = urllib.request.build_opener(NoRedirect())
    cases = [
        ("/health/live", None, 200),
        ("/health/ready", None, 200),
        ("/", None, 401),
        ("/api/class-droppers", None, 401),
        ("/api/class-droppers", "Bearer invalid", 401),
        ("/", f"Basic {basic}", 200),
        ("/api/class-droppers?state=WA", bearer, 200),
        ("/api/elite-closers?state=WA", bearer, 200),
        ("/api/trainers/first-up?state=WA", bearer, 200),
        ("/api/trainers/backup?state=WA", bearer, 200),
    ]
    for path, authorization, expected in cases:
        request = urllib.request.Request(args.url.rstrip("/") + path)
        if authorization:
            request.add_header("Authorization", authorization)
        try:
            with opener.open(request, timeout=15) as response:
                status = response.status
                body = response.read()
        except urllib.error.HTTPError as error:
            status, body = error.code, b""
        except urllib.error.URLError:
            raise SystemExit(f"FAIL {path}: server unreachable or TLS validation failed") from None
        if status != expected:
            raise SystemExit(f"FAIL {path}: expected HTTP {expected}, got {status}")
        if status == 200 and path.startswith("/api/"):
            json.loads(body)
        if status == 200 and path == "/" and b"Punting Warehouse" not in body:
            raise SystemExit("FAIL dashboard title missing")
        print(f"PASS {path}: HTTP {status}")
    print("HTTP smoke checks passed. Provider data and public DNS/TLS require separate validation.")


if __name__ == "__main__":
    main()
