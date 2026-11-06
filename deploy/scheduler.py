"""Run real cron; preserve its configuration privately and ingest as non-root."""

import fcntl
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STATE = Path("/run/punting")
ENV_FILE = STATE / "environment.json"
ENV_NAMES = {
    "DATABASE_URL", "API_TOKEN", "ADMIN_USERNAME", "ADMIN_PASSWORD",
    "PUNTING_FORM_API_KEY", "PROVIDER_BASE_URL", "PROVIDER_CONFIG_PATH",
    "LOG_LEVEL", "INGEST_LOOKBACK_DAYS", "TZ", "PATH", "PYTHONUNBUFFERED",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
}


def drop_privileges() -> None:
    if os.getuid() == 0:
        os.setgroups([])
        os.setgid(10001)
        os.setuid(10001)


def main() -> None:
    if sys.argv[1:] == ["--run"]:
        # Lock survives exec and prevents overlapping scheduled executions.
        lock = os.open(STATE / "ingest.lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Scheduled ingestion skipped: previous run remains active.", flush=True)
            return
        os.set_inheritable(lock, True)
        environment = json.loads(ENV_FILE.read_text())
        lookback = int(environment.get("INGEST_LOOKBACK_DAYS", "7"))
        if not 0 <= lookback <= 365:
            raise SystemExit("INGEST_LOOKBACK_DAYS must be between 0 and 365")
        today = datetime.now(ZoneInfo("Australia/Perth")).date()
        start = today - timedelta(days=lookback)
        print(f"Scheduled ingestion: {start} through {today} (Australia/Perth)", flush=True)
        drop_privileges()
        os.execvpe("punting", ["punting", "ingest-range", "--start", str(start),
                              "--end", str(today)], environment)
    elif sys.argv[1:]:
        drop_privileges()
        os.execvp(sys.argv[1], sys.argv[1:])
    else:
        if not os.environ.get("PUNTING_FORM_API_KEY"):
            raise SystemExit("Scheduler requires PUNTING_FORM_API_KEY; configure it before enabling ingest.")
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        environment = {key: value for key, value in os.environ.items() if key in ENV_NAMES}
        fd = os.open(ENV_FILE, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(environment, stream)
        print("Cron ready: 05:15 daily, Australia/Perth; ingestion runs as UID 10001.", flush=True)
        os.execv("/usr/sbin/cron", ["cron", "-f", "-L", "0"])


if __name__ == "__main__":
    main()
