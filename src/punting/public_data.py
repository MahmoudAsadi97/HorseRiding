"""Historical Hong Kong CSV adapter; no Punting Form metrics are fabricated.

The original CSVs are evidence. This projection uses anonymized source IDs,
retains all original columns, and derives DLR only from prior observed dates.
"""
import csv
import io
import math
from collections import Counter, defaultdict
from datetime import date

from punting.ingest import archive_source, ingest_payload

VENUES = {'ST': 'Sha Tin', 'HV': 'Happy Valley'}
RACE_FIELDS = {'race_id', 'date', 'venue', 'race_no', 'distance', 'race_class'}
RUN_FIELDS = {
    'race_id', 'horse_no', 'horse_id', 'trainer_id', 'jockey_id',
    'draw', 'actual_weight', 'result', 'win_odds',
}


def _read_csv(body, required):
    reader = csv.DictReader(io.StringIO(body.decode('utf-8-sig'), newline=''))
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError('CSV is missing required source columns')
    if len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError('CSV contains duplicate column headers')
    rows = list(reader)
    if not rows:
        raise ValueError('CSV contains no records')
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError('CSV contains malformed records')
    return rows


def _number(value, field, *, integer=False, minimum=None):
    if value in ('', None):
        return None
    number = float(value)
    if not math.isfinite(number) or (integer and not number.is_integer()):
        raise ValueError(f'Invalid {field}')
    if minimum is not None and number < minimum:
        raise ValueError(f'Invalid {field}')
    return int(number) if integer else number


def _identifier(row, field):
    value = row[field]
    if not value.strip() or len(value) > 100:
        raise ValueError(f'Invalid {field}')
    return value


def _winning_return(race, run):
    """Published HK dividends are gross HKD paid on a HKD 10 winning stake."""
    if _number(run['result'], 'result', integer=True, minimum=1) != 1:
        return None
    horse_no = _number(run['horse_no'], 'horse_no', integer=True, minimum=1)
    for index in (1, 2):
        combination = _number(race.get(f'win_combination{index}'), 'win_combination',
                              integer=True, minimum=1)
        if combination == horse_no:
            dividend = _number(race.get(f'win_dividend{index}'), 'win_dividend', minimum=0)
            return None if dividend is None else dividend / 10
    return None


def normalize_hk_csv(race_body: bytes, run_body: bytes, *, max_races: int | None = None):
    """Build a canonical envelope, selecting complete most-recent races if bounded."""
    if max_races is not None and (isinstance(max_races, bool) or max_races <= 0):
        raise ValueError('max_races must be positive')
    race_rows = _read_csv(race_body, RACE_FIELDS)
    run_rows = _read_csv(run_body, RUN_FIELDS)
    races = {}
    for row in race_rows:
        race_id = _identifier(row, 'race_id')
        if race_id in races:
            raise ValueError('Duplicate race_id')
        if row['venue'] not in VENUES:
            raise ValueError('Unsupported source venue')
        date.fromisoformat(row['date'])
        races[race_id] = row
    race_order = sorted(races, key=lambda key: (
        races[key]['date'], races[key]['venue'], int(races[key]['race_no']), key,
    ))
    selected = set(race_order[-max_races:] if max_races else race_order)
    by_race = defaultdict(list)
    horse_dates = defaultdict(set)
    seen_entries = set()
    seen_horses = set()
    for row in run_rows:
        race_id = _identifier(row, 'race_id')
        horse_id = _identifier(row, 'horse_id')
        entry = race_id, _identifier(row, 'horse_no')
        if race_id not in races:
            raise ValueError('Runner references an unknown race')
        if entry in seen_entries:
            raise ValueError('Duplicate race runner entry')
        if (race_id, horse_id) in seen_horses:
            raise ValueError('Duplicate horse in one race')
        seen_entries.add(entry)
        seen_horses.add((race_id, horse_id))
        by_race[race_id].append(row)
        horse_dates[horse_id].add(date.fromisoformat(races[race_id]['date']))
    if any(not by_race[key] for key in selected):
        raise ValueError('Selected race has no runner records')
    previous_dates = {}
    for horse_id, dates in horse_dates.items():
        previous = None
        for observed in sorted(dates):
            previous_dates[horse_id, observed] = previous
            previous = observed
    meetings = {}
    for race_id in race_order:
        if race_id not in selected:
            continue
        row = races[race_id]
        meeting_key = row['date'], row['venue']
        if meeting_key not in meetings:
            meetings[meeting_key] = {
                'id': f'hk-public:{row["date"]}:{row["venue"]}',
                'date': row['date'], 'track': VENUES[row['venue']], 'state': 'HK',
                'raw_payload': {'source': 'gdaley/hkracing', 'race_rows': []}, 'races': [],
            }
        meeting = meetings[meeting_key]
        meeting['raw_payload']['race_rows'].append(row)
        race = {
            'id': f'hk-public:race:{race_id}',
            'number': _number(row['race_no'], 'race_no', integer=True, minimum=1),
            'distance': _number(row['distance'], 'distance', integer=True, minimum=1),
            'class_grade': row['race_class'], 'raw_payload': row, 'runners': [],
        }
        # Going and rail can vary within a meeting; keep exact values per race.
        winners = Counter(
            _number(run['result'], 'result', integer=True, minimum=1)
            for run in by_race[race_id]
        )[1]
        for run in by_race[race_id]:
            observed = date.fromisoformat(row['date'])
            previous = previous_dates[run['horse_id'], observed]
            pounds = _number(run['actual_weight'], 'actual_weight', minimum=0)
            finish = _number(run['result'], 'result', integer=True, minimum=1)
            runner = {
                'id': f'hk-public:race:{race_id}:runner:{run["horse_no"]}',
                'horse_name': f'HK horse ID {run["horse_id"]}',
                'trainer': f'HK trainer ID {run["trainer_id"]}' if run['trainer_id'] else None,
                'jockey': f'HK jockey ID {run["jockey_id"]}' if run['jockey_id'] else None,
                'barrier': _number(run['draw'], 'draw', integer=True, minimum=1),
                'weight': pounds * 0.45359237 if pounds is not None else None,
                'finish_position': finish,
                'result_status': 'finished' if finish is not None else 'pending',
                'starting_price': _number(run['win_odds'], 'win_odds', minimum=1),
                'settled_win_return': _winning_return(row, run),
                'dead_heat_factor': 1 / winners if finish == 1 and winners else 1.0,
                'dlr': (observed - previous).days if previous else None,
                'kri': None, 'cls_diff': None, 'fml600': None, 'fml200': None,
                'rb': None, 'prse': None, 'run_style': None,
                'raw_payload': run,
            }
            race['runners'].append(runner)
        meeting['races'].append(race)
    return {'meetings': list(meetings.values()), 'source_metadata': {
        'dataset': 'gdaley/hkracing', 'region': 'Hong Kong',
        'historical': True, 'names': 'anonymized source IDs',
        'dlr_method': 'days since previous observed start in the input CSV pair',
        'source_race_count': len(race_rows), 'source_run_count': len(run_rows),
        'selected_race_count': len(selected),
        'sectionals': 'retained in source rows; segment distances not projected as 200m',
    }}


def import_hk_csv(session, race_body: bytes, run_body: bytes, *, max_races=None,
                  races_endpoint='local:races.csv', runs_endpoint='local:runs.csv'):
    """Archive both original byte streams before parsing; then project atomically."""
    source_ids = [
        archive_source(session, races_endpoint, race_body,
                       source_context={'dataset': 'gdaley/hkracing', 'format': 'csv'}),
        archive_source(session, runs_endpoint, run_body,
                       source_context={'dataset': 'gdaley/hkracing', 'format': 'csv'}),
    ]
    payload = normalize_hk_csv(race_body, run_body, max_races=max_races)
    payload['source_metadata']['source_feed_ids'] = source_ids
    return ingest_payload(session, 'derived:hk-public-csv', payload)
