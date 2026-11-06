#!/usr/bin/env python3
"""Reconcile the default 160-race sample's API output with independent CSV arithmetic."""
import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.request import Request, build_opener

from http_safety import NoRedirect, service_base_url


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    args = parser.parse_args()
    args.url = service_base_url(args.url)
    config = dict(line.split('=', 1) for line in Path('.env').read_text().splitlines()
                  if '=' in line and not line.startswith('#'))
    with Path('.local/public-data/races.csv').open() as stream:
        races = list(csv.DictReader(stream))
    with Path('.local/public-data/runs.csv').open() as stream:
        runs = list(csv.DictReader(stream))
    by_id = {r['race_id']: r for r in races}
    selected = {r['race_id'] for r in sorted(races, key=lambda r: (
        r['date'], r['venue'], int(r['race_no']), r['race_id']))[-160:]}
    history = defaultdict(set)
    for r in runs:
        history[r['horse_id']].add(date.fromisoformat(by_id[r['race_id']]['date']))
    expected = defaultdict(lambda: {'starts': 0, 'wins': 0, 'return_units': 0.0})
    for r in runs:
        if r['race_id'] not in selected:
            continue
        race = by_id[r['race_id']]
        day = date.fromisoformat(race['date'])
        prior = [d for d in history[r['horse_id']] if d < day]
        if not prior or (day - max(prior)).days > 7:
            continue
        row = expected['HK trainer ID ' + r['trainer_id']]
        row['starts'] += 1
        if int(r['result']) == 1:
            row['wins'] += 1
            payout = None
            for i in (1, 2):
                if race.get(f'win_combination{i}') == r['horse_no']:
                    payout = float(race[f'win_dividend{i}']) / 10
            assert payout is not None, 'Sample winner needs published payout'
            row['return_units'] += payout
    request = Request(args.url + '/api/trainers/backup?state=HK&limit=500',
                      headers={'Authorization': 'Bearer ' + config['API_TOKEN']})
    with build_opener(NoRedirect()).open(request, timeout=30) as response:
        actual = json.load(response)
    assert actual['total'] == len(expected)
    for row in actual['items']:
        source = expected[row['trainer']]
        for key in ('starts', 'wins', 'return_units'):
            assert abs(row[key] - source[key]) < 1e-6, (row['trainer'], key)
        assert row['priced_starts'] == source['starts']
        roi = 100 * (source['return_units'] - source['starts']) / source['starts']
        assert abs(row['roi_pct'] - roi) < 0.0001
    print(json.dumps({'status': 'reconciled', 'trainers': len(expected),
                      'starts': sum(r['starts'] for r in expected.values()),
                      'wins': sum(r['wins'] for r in expected.values()),
                      'gross_return_units': round(sum(r['return_units'] for r in expected.values()), 6)}))


if __name__ == '__main__':
    main()
