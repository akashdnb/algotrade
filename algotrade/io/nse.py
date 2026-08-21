"""Fetch bhavcopy files directly from NSE's archive.

Used to backfill sessions absent from the mirrored archive (PROJECT.md §10.8).

Deliberately does not depend on `nsefin`, for two reasons found by testing it:

  * Its version metadata is unreliable -- `pip show` reports 0.1.1 while
    `nsefin.__version__` reports 1.0.0, with a completely different API
    surface. A version pin does not pin the behaviour.
  * Its holiday calendar fails silently. The upstream endpoints 404, the
    exception is swallowed, and `is_nse_trading_holiday("15-08-2022")` returns
    False for Independence Day. A confidently wrong answer is worse than an
    error.

NSE also serves these files dishonestly: the URL ends in `.csv` but the body
is frequently an XLSX inside a ZIP. Content is sniffed rather than trusted.

Downloads are normalised to CSV text and written to disk so that
`bhavcopy.read_file` remains the single parsing path -- there is no second
schema mapping to drift out of sync. The written file also caches the result,
so a session is never fetched twice.
"""

from __future__ import annotations

import io
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Final

import requests

_ARCHIVE: Final[str] = (
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{stamp}.csv"
)
_UA: Final[str] = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)
# NSE 403s archive requests that arrive without a plausible browser referer.
_REFERER: Final[str] = "https://www.nseindia.com/all-reports"

# XLSX is a ZIP; this member is what distinguishes it from a zipped CSV.
_OOXML_MARKER: Final[str] = "[Content_Types].xml"

DEFAULT_DELAY: Final[float] = 1.5
DEFAULT_RETRIES: Final[int] = 3


class NseFetchError(RuntimeError):
    """Raised when a session cannot be retrieved from NSE's archive."""


def filename_for(day: date) -> str:
    """Archive filename for a trading date, matching the mirror's convention."""
    return f"sec_bhavdata_full_{day:%d%m%Y}.csv"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": _UA,
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": _REFERER,
        }
    )
    return s


def _decode(body: bytes) -> str:
    """Turn whatever NSE returned into CSV text.

    Handles three observed shapes: plain CSV, a ZIP containing a CSV, and a ZIP
    that is really an XLSX workbook.
    """
    if body[:2] != b"PK":
        return body.decode("utf-8", "replace")

    archive = zipfile.ZipFile(io.BytesIO(body))
    names = archive.namelist()

    if _OOXML_MARKER in names:
        # Lazily imported: only the XLSX path needs a spreadsheet reader.
        import pandas as pd

        frame = pd.read_excel(io.BytesIO(body), engine="openpyxl", dtype=str)
        return frame.to_csv(index=False)

    inner = next((n for n in names if n.lower().endswith(".csv")), None)
    if inner is None:
        raise NseFetchError(f"zip contains no csv or workbook: {names[:5]}")
    return archive.read(inner).decode("utf-8", "replace")


def fetch_csv(
    day: date,
    *,
    retries: int = DEFAULT_RETRIES,
    delay: float = DEFAULT_DELAY,
    session: requests.Session | None = None,
) -> str:
    """Download one session's bhavcopy from NSE and return it as CSV text.

    Raises:
        NseFetchError: after `retries` attempts, or on a 404 (which usually
            means the date was not a trading session at all).
    """
    url = _ARCHIVE.format(stamp=f"{day:%d%m%Y}")
    own = session or _session()
    last = ""

    for attempt in range(1, retries + 1):
        try:
            response = own.get(url, timeout=45)
        except requests.RequestException as exc:  # network flake
            last = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code == 404:
                raise NseFetchError(f"{day}: 404 (likely not a trading session)")
            if response.ok and response.content:
                return _decode(response.content)
            last = f"HTTP {response.status_code}, {len(response.content)}b"

        if attempt < retries:
            time.sleep(delay * 2 ** (attempt - 1))

    raise NseFetchError(f"{day}: giving up after {retries} attempts ({last})")


def download_to(
    day: date,
    dest_dir: str | Path,
    *,
    overwrite: bool = False,
    session: requests.Session | None = None,
    **kwargs,
) -> Path:
    """Fetch a session and write it as CSV under `dest_dir`.

    The file is named to the mirror's convention so `bhavcopy.read_file` reads
    it with no special casing -- and so its own date guard still applies, which
    matters because NSE serves stale content for non-sessions.

    Returns the path written, or the existing path when already cached.
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / filename_for(day)

    if target.exists() and not overwrite:
        return target

    text = fetch_csv(day, session=session, **kwargs)
    # Write via a temp file so an interrupted run cannot leave a partial CSV
    # that later looks like a valid cached session.
    staging = target.with_suffix(".csv.part")
    staging.write_text(text, encoding="utf-8")
    staging.replace(target)
    return target
