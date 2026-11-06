#!/usr/bin/env python3
"""Download the pinned HK CSV pair on request, verify it, and import a bounded sample."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.request import urlopen

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from punting.config import Settings
from punting.db import create_schema, make_engine
from punting.models import Meeting, Race, Runner
from punting.public_data import import_hk_csv

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Fetch pinned public CSV files')
    parser.add_argument('--data-dir', type=Path, default=PROJECT_ROOT / '.local/public-data')
    parser.add_argument('--max-races', type=int, default=160,
                        help='Most recent complete races to project (default 160)')
    parser.add_argument('--all', action='store_true', help='Project all 6,349 historical races')
    parser.add_argument('--database-url', help='Override DATABASE_URL for this import')
    args = parser.parse_args()
    if args.max_races <= 0:
        parser.error('--max-races must be positive')
    manifest = json.loads((PROJECT_ROOT / 'data/public/source.json').read_text())
    bodies = {}
    args.data_dir.mkdir(parents=True, exist_ok=True)
    for filename, spec in manifest['files'].items():
        target = args.data_dir / filename
        if args.download:
            with urlopen(spec['url'], timeout=60) as response:
                body = response.read(spec['bytes'] + 1)
        else:
            if not target.exists():
                parser.error(f'Missing {target}; run with --download to fetch the public source')
            body = target.read_bytes()
        if len(body) != spec['bytes'] or hashlib.sha256(body).hexdigest() != spec['sha256']:
            parser.error(f'Integrity check failed for {filename}; no data imported')
        bodies[filename] = body
    if args.download:
        for filename, body in bodies.items():
            target = args.data_dir / filename
            temporary = target.with_suffix('.csv.tmp')
            temporary.write_bytes(body)
            temporary.replace(target)
    settings = Settings()
    engine = make_engine(args.database_url or settings.database_url)
    create_schema(engine)
    try:
        with Session(engine) as session:
            feed_id = import_hk_csv(
                session, bodies['races.csv'], bodies['runs.csv'],
                max_races=None if args.all else args.max_races,
                races_endpoint=manifest['files']['races.csv']['url'],
                runs_endpoint=manifest['files']['runs.csv']['url'],
            )
            counts = {
                'meetings': session.scalar(select(func.count()).select_from(Meeting)
                                           .where(Meeting.state == 'HK')),
                'races': session.scalar(select(func.count()).select_from(Race)
                                       .where(Race.id.like('hk-public:%'))),
                'runners': session.scalar(select(func.count()).select_from(Runner)
                                         .where(Runner.id.like('hk-public:%'))),
            }
            print(json.dumps({'source': manifest['dataset'], 'projection_feed_id': feed_id,
                              'region': 'HK', **counts}))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
