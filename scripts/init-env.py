#!/usr/bin/env python3
"""Generate local secrets without displaying them or overwriting an existing file."""

import argparse
import os
import re
import secrets
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain", default="", help="Public DNS name, without https://")
    parser.add_argument("--email", default="", help="ACME certificate contact email")
    parser.add_argument("--output", type=Path, default=Path(".env"))
    args = parser.parse_args()
    if args.domain and not re.fullmatch(r"[A-Za-z0-9.-]+", args.domain):
        parser.error("--domain must contain only DNS name characters")
    if args.email and not re.fullmatch(r"[A-Za-z0-9.!#+/_-]+@[A-Za-z0-9.-]+", args.email):
        parser.error("--email must be a plain email address")
    replacements = {
        "POSTGRES_PASSWORD": secrets.token_hex(32),
        "API_TOKEN": secrets.token_urlsafe(48),
        "ADMIN_PASSWORD": secrets.token_urlsafe(32),
        "DOMAIN": args.domain,
        "ACME_EMAIL": args.email,
    }
    template = Path(__file__).resolve().parents[1] / ".env.example"
    lines = []
    for line in template.read_text().splitlines():
        key = line.partition("=")[0]
        lines.append(f"{key}={replacements[key]}" if key in replacements else line)
    os.umask(0o077)
    try:
        with args.output.open("x") as stream:
            stream.write("\n".join(lines) + "\n")
    except FileExistsError:
        parser.exit(1, f"Refusing to overwrite {args.output}; edit the existing file privately.\n")
    print(f"Created {args.output} with unique credentials and mode 0600. Values were not printed.")


if __name__ == "__main__":
    main()
