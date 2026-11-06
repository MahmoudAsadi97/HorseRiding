# Historical public data adapter

I added an importer for the actual **Hong Kong horse racing** dataset published
as [gdaley/hkracing on Kaggle](https://www.kaggle.com/datasets/gdaley/hkracing),
using the accessible [Karen Wong GitHub mirror](https://github.com/karenwky/Predictive_Modeling_Hong_Kong_Horse_Racing).
No Punting Form key is needed. The checked source contains **6,349 races and
79,447 runner records, 2 June 1997–28 August 2005**, at Sha Tin and Happy Valley.
It is historical research data, not a live feed or Western Australian coverage.

The repository contains the adapter and pinned provenance manifest, **not the
source CSVs**. The original dataset license has not been independently verified.
The mirror has an MIT software license; that does not establish a license to
redistribute the original data. Source files downloaded for local evaluation
remain under `.local/`, which Git ignores. Check the original dataset terms
before distributing a copy or using it in a public service.

## Import

From the project root, after installing the project and configuring the database:

```bash
python scripts/import_public_data.py --download
```

This downloads about 11.5 MB from a pinned Git commit, validates both lengths and
SHA-256 hashes, archives the complete original byte streams, and projects the
latest **160 complete races**. The full history is read before selecting races,
so observed previous-start dates can precede the selected sample. Use `--all`
to project all source rows, or `--max-races 40` for a smaller local sample.
Later imports can omit `--download`; checksums are still verified.

```bash
python scripts/import_public_data.py --database-url sqlite:///.local/public-demo.db
```

Choose **HK** in the interface or pass `state=HK` to API queries. WA remains the
default for production filters. Source rows do not have Saturday Ascot meetings.
Repeated imports append source evidence but update the same normalized entities.

## Field mapping and limitations

| Source fields | Normalized representation |
| --- | --- |
| `date`, `venue` | Meeting date; `ST` → Sha Tin, `HV` → Happy Valley, state `HK` |
| `race_id`, `race_no`, `distance`, `race_class` | Prefixed race ID, race number, metres, source class label |
| `horse_id`, `trainer_id`, `jockey_id` | Explicit anonymous labels such as `HK trainer ID 118`; these are not names |
| `horse_no` + `race_id` | Stable race-entry ID; the horse can appear in many races |
| `draw`, `actual_weight` | Barrier and carried weight converted from pounds to kilograms |
| `result` | Finishing position; records with a position have status `finished` |
| `win_odds` | Source rounded closing decimal price in `starting_price` |
| Matching `win_combination1/2`, `win_dividend1/2` | Winner `settled_win_return` = published gross HKD dividend / HKD 10 stake |
| Prior observed date for the same `horse_id` | `DLR`; first observed start remains null |
| All remaining source columns | Unedited `raw_payload` and original CSV archive |

Published winner returns take precedence in ROI and already account for dead
heats. Rounded closing prices are kept separately; they need not equal the
published settlement return. If a published return is unavailable, the existing
price × dead-heat-factor fallback is an estimate from the source close.

`DLR` means days since the previous **observed** start in these source files.
It is not proof that the horse had no intervening race elsewhere. Dataset
boundaries and missing starts can overstate the true gap. This dataset cannot
establish a preparation counter, so `RB` remains null.

`KRI`, `ClsDiff`, `FML200`, `FML600`, `PrSe`, `RunStyle`, class pars, and moisture
variants remain null. A source race-class label is not Punting Form's `ClsDiff`.
Class-dropper and elite-closer scans therefore have no valid HK metric matches.

The CSVs include sectional timing/position columns, but their segments are not
uniform 200m intervals. They remain intact in source rows; the adapter does not
manufacture 200m sectionals, tempo adjustments, or proprietary FML ratings.
Going and rail configuration remain in each race's original row because they
can vary within one meeting.

Each import retains two immutable CSV archive records and a third derived
canonical envelope. Its `source_metadata.source_feed_ids` links to both originals.
Every original CSV column is retained in its runner or race row even when it has
no normalized field. Parsing and join failures leave the source pair archived.
The adapter rejects duplicate races, duplicate race-entry IDs, orphan runners,
unknown venues, missing required columns, and malformed rows.

The exact URLs, commit, byte lengths, hashes, and original row counts are in
[`source.json`](source.json). Tests use synthetic rows rather than redistributed
source records.
