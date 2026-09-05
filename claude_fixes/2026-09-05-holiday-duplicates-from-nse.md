# NSE serves the previous session's file on holidays, and we committed 27 of them

**2026-09-05.** Found while answering "is the daily data we fetch consistent?"

## The claim that was wrong

`services/ingestion.py` classified a 404 as the holiday signal and said:

> This is the only holiday source in the stack that has not lied.

It lies. On many trading holidays NSE's archive endpoint returns **200 with the
previous session's file** under the requested date's name — the same behaviour
the third-party mirror has, which the panel's integrity guard was already built
to catch.

## What that produced

27 of our own 511 committed session files (5.3%, 9.2 MB) were holiday
duplicates:

```
2024-10-02 Gandhi Jayanti   2025-03-14 Holi          2025-04-18 Good Friday
2024-11-15 Guru Nanak       2025-03-31 Id-ul-Fitr    2025-05-01 Maharashtra Day
2024-12-25 Christmas        2025-04-10 Mahavir J.    2025-08-15 Independence Day
2025-02-26 Mahashivratri    2025-04-14 Ambedkar J.   ... and 16 more
```

**The panel was never wrong.** `archive.load_panel` rejects any file whose rows
disagree with its filename's date, and no rejected date appears in the panel —
checked. Rebuilding after deleting all 27 produced an identical panel: 4,004
sessions, 6,413,238 rows.

What was wrong was everything around it. `ingest` only checked
`destination.exists()`, so each duplicate was recorded as **`fetched`**. That
inflated `new_sessions`, which is what `daily.yml` uses to decide whether to
commit — so the run reported ingesting a session it had not ingested, and
committed yesterday's data to git under a holiday's name.

## The fix

`IngestionService` takes an injected `verify(path, day)`, so the service still
makes no parsing decisions of its own; `nse_ingestion_service` wires in
`read_file`, which is the same guard the panel build uses. A file that is not
that session is deleted and recorded as `holiday`.

A 404 remains trustworthy. A 200 does not, and now has to prove itself.

The 27 existing files are deleted. `missing_sessions` scans forward from the
newest stored session, so removing old ones does not cause them to be
re-fetched forever — at most one extra attempt on a holiday itself, which the
next trading day ends.

## How I got this wrong twice first

I flagged the 75 rejected files as missing data, then retracted the retraction:

1. First reported them as "~6% of 2026's sessions silently absent" — wrong, they
   are holidays.
2. Then attributed all 75 to the third-party mirror and told the user our own
   fetch was clean. Also wrong: I ran `archive.discover([sessions, mirror,
   backfill])`, and **discover lets later directories win**, so for every date
   we shared with the mirror it returned the mirror's path. Per-directory the
   split is 27 ours / 76 theirs.

The lesson is narrow and worth keeping: `discover` is a resolution function,
not an inventory. Asking it "where did this file come from" only works if you
pass it one directory.
