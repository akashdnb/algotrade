# Dashboard review — six findings

A deliberate read-through of the dashboard after all eight pages were built.
One finding could have written wrong data; the rest were performance or traps
left armed.

## The one that mattered: `date.today()` is UTC

Seven sites called it. It returns the **server's** date, and the deployment
runs UTC. IST is UTC+5:30, so **between 00:00 and 05:30 IST every "today" the
dashboard computed was a day behind.**

| Site | Consequence |
|---|---|
| `recommendations.py` (×2) | **Writes bad data.** The record-fill form defaults `executed_on` to it — a fill entered at 01:00 IST would have been booked against the previous trading day |
| `executions.py` (×2) | On 1 April before 05:30 IST, defaults to the *previous* financial year — including the CSV tax export |
| `health.py` | Under-reports "sessions behind", wrong in the *reassuring* direction |
| `summary.py`, `holdings.py` | Cosmetic |

The trading system had already settled this, in `clock.py`: *"IST, not UTC,
decides the business date. The exchange's day is an IST calendar day; deriving
it from a UTC timestamp puts every session after 18:30 IST on the wrong
date."* `app/clock.py` now delegates to it — one definition across the jobs and
the dashboard is the point of sharing a repository, and I simply had not used
it.

**Guarded by a grep-test, deliberately.** The defect is invisible except during
the five and a half hours a day when UTC and IST disagree, so no functional
test run at a convenient hour would catch a regression. A test that reads the
source is the honest instrument here.

## Performance

* **N+1 on `/rebalances`** — `get_set()` per row meant 24 Atlas round trips per
  page, and `maxPoolSize=1` makes them strictly sequential. Added
  `RecommendationRepository.get_many()`; now one query.
* **Unbounded reads on `/history`** — the charts fetched every snapshot ever;
  a decade of daily sessions is ~2,500 documents to draw two lines. Windowed to
  five years, which is longer than any drawdown in the archive so it cannot
  hide what the page exists to show. The table still pages through everything.
* **Hover layer** — one `<rect>` per point, ~5,000 DOM nodes at twenty years.
  Dropped past 400 points: the shape is the page's purpose, the readout is a
  convenience.
* **Audit entity filter could not use an index** — `entity_history` leads with
  `entity_type`, which the question does not supply, so it scanned. Added
  `entity_recent (entity_id, ts)` and applied it to Atlas.

## A trap left armed

`/rebalances/{run_id:path}` used the greedy converter — the exact shape that
silently swallowed the `/record` suffix and made the write routes unreachable
last week. Harmless here (no sub-routes), removed anyway.

## Verified clean, and unchanged by the fixes

Auth on all ten routes. 404s not swallowed by the generic exception handler.
No float in any money path — `dec()` still raises on one. Every query backed by
an index. CSRF and origin checks intact. `script-src 'none'` intact.

## What the pattern says

The highest-severity finding was in the **newest** code, in the **only** path
that mutates the book. The read pages have now been looked at repeatedly, by
screenshot and by hand; the write path has been exercised exactly once, against
a scratch database. Its first real use will also be its first real test, which
argues for doing it deliberately at the next rebalance rather than in a hurry.

508 trading tests, 103 dashboard.
