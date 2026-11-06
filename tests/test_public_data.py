"""Synthetic CSV rows exercise the public-source adapter without redistributing data."""
import csv
import io

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from punting.db import create_schema, make_engine
from punting.models import Meeting, RawFeed, Runner, Sectional200m


def csv_bytes(rows):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


@pytest.fixture
def source_pair():
    races = [
        {'race_id': '2', 'date': '2001-01-08', 'venue': 'HV', 'race_no': '1',
         'distance': '1200', 'race_class': '4', 'config': 'A', 'going': 'GOOD'},
        {'race_id': '1', 'date': '2001-01-01', 'venue': 'ST', 'race_no': '1',
         'distance': '1400', 'race_class': '5', 'config': 'B', 'going': 'GOOD'},
    ]
    runs = [
        {'race_id': '2', 'horse_no': '1', 'horse_id': '10', 'trainer_id': '3',
         'jockey_id': '7', 'draw': '2', 'actual_weight': '132', 'result': '1',
         'win_odds': '2.5', 'time1': '24.3', 'unknown_original_column': 'keep me'},
        {'race_id': '1', 'horse_no': '1', 'horse_id': '10', 'trainer_id': '3',
         'jockey_id': '7', 'draw': '3', 'actual_weight': '130', 'result': '2',
         'win_odds': '5.0', 'time1': '14.1', 'unknown_original_column': 'unmodified'},
    ]
    return races, runs


@pytest.fixture
def session():
    engine = make_engine('sqlite://')
    create_schema(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_csv_adapter_derives_only_observed_history_and_labels_anonymous_ids(source_pair):
    from punting.public_data import normalize_hk_csv

    races, runs = source_pair
    payload = normalize_hk_csv(csv_bytes(races), csv_bytes(runs))
    meetings = payload['meetings']
    assert [m['state'] for m in meetings] == ['HK', 'HK']
    assert [m['track'] for m in meetings] == ['Sha Tin', 'Happy Valley']
    first = meetings[0]['races'][0]['runners'][0]
    later = meetings[1]['races'][0]['runners'][0]
    assert first['dlr'] is None
    assert later['dlr'] == 7
    assert later['horse_name'] == 'HK horse ID 10'
    assert later['trainer'] == 'HK trainer ID 3'
    assert later['weight'] == pytest.approx(59.87419284)
    assert later['starting_price'] == 2.5
    assert later['finish_position'] == 1
    assert later['raw_payload'] == runs[0]
    assert all(later.get(field) is None for field in ('kri', 'fml200', 'fml600', 'rb', 'cls_diff'))
    assert later.get('sectionals', []) == []


def test_sample_selects_complete_recent_races_but_history_uses_all_input(source_pair):
    from punting.public_data import normalize_hk_csv

    races, runs = source_pair
    payload = normalize_hk_csv(csv_bytes(races), csv_bytes(runs), max_races=1)
    assert len(payload['meetings']) == 1
    assert payload['meetings'][0]['date'] == '2001-01-08'
    assert payload['meetings'][0]['races'][0]['runners'][0]['dlr'] == 7


def test_published_winning_dividend_is_separate_from_rounded_close(source_pair):
    from punting.public_data import normalize_hk_csv

    races, runs = source_pair
    for row in races:
        row.update(win_combination1='', win_dividend1='')
    races[0].update(win_combination1='1', win_dividend1='25.5')
    payload = normalize_hk_csv(csv_bytes(races), csv_bytes(runs))
    winner = payload['meetings'][1]['races'][0]['runners'][0]
    loser = payload['meetings'][0]['races'][0]['runners'][0]
    assert winner['settled_win_return'] == 2.55
    assert winner['starting_price'] == 2.5
    assert loser['settled_win_return'] is None


def test_numeric_winner_strings_still_receive_payout_and_dead_heat_adjustment(source_pair):
    from punting.public_data import normalize_hk_csv

    races, runs = source_pair
    for row in races:
        row.update(win_combination1='', win_dividend1='')
    races[0].update(win_combination1='1', win_dividend1='13.0')
    runs[0]['result'] = '1.0'
    second = runs[0].copy()
    second.update(horse_no='2', horse_id='11', result=' 1 ')
    runs.append(second)
    payload = normalize_hk_csv(csv_bytes(races), csv_bytes(runs))
    winners = payload['meetings'][1]['races'][0]['runners']
    assert [runner['dead_heat_factor'] for runner in winners] == [0.5, 0.5]
    assert winners[0]['settled_win_return'] == 1.3


def test_same_horse_cannot_appear_twice_in_one_race(source_pair):
    from punting.public_data import normalize_hk_csv

    races, runs = source_pair
    duplicate = runs[0].copy()
    duplicate['horse_no'] = '2'
    runs.append(duplicate)
    with pytest.raises(ValueError, match='Duplicate'):
        normalize_hk_csv(csv_bytes(races), csv_bytes(runs))


def test_csv_import_preserves_both_files_and_replay_is_idempotent(session, source_pair):
    from punting.public_data import import_hk_csv

    races, runs = source_pair
    race_body, run_body = csv_bytes(races), csv_bytes(runs)
    import_hk_csv(session, race_body, run_body)
    import_hk_csv(session, race_body, run_body)
    assert session.scalar(select(func.count()).select_from(Runner)) == 2
    assert session.scalar(select(func.count()).select_from(Meeting)) == 2
    assert session.scalar(select(func.count()).select_from(Sectional200m)) == 0
    feeds = session.scalars(select(RawFeed).order_by(RawFeed.id)).all()
    assert len(feeds) == 6
    assert feeds[0].raw_body == race_body
    assert feeds[1].raw_body == run_body
    assert feeds[2].payload['source_metadata']['source_feed_ids'] == [feeds[0].id, feeds[1].id]


@pytest.mark.parametrize('failure', ['duplicate', 'orphan', 'unknown_venue', 'missing_header'])
def test_invalid_csv_pair_is_archived_without_projecting(session, source_pair, failure):
    from punting.public_data import import_hk_csv

    races, runs = source_pair
    if failure == 'duplicate':
        runs.append(runs[0].copy())
    elif failure == 'orphan':
        runs[0]['race_id'] = 'not-a-race'
    elif failure == 'unknown_venue':
        races[0]['venue'] = 'UNKNOWN'
    else:
        for row in runs:
            del row['horse_id']
    race_body, run_body = csv_bytes(races), csv_bytes(runs)
    with pytest.raises(ValueError):
        import_hk_csv(session, race_body, run_body)
    assert session.scalar(select(func.count()).select_from(Runner)) == 0
    feeds = session.scalars(select(RawFeed).order_by(RawFeed.id)).all()
    assert [f.raw_body for f in feeds] == [race_body, run_body]
