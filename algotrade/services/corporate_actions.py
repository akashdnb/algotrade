"""Discover corporate actions affecting a book, corroborate them, apply them.

The research path has had complete corporate-action machinery since the
backtest was built -- `core.actions` parses factors, `core.adjust` back-adjusts
on read, `core.reconcile` corroborates feed actions against observed price
moves. The **live book** had none of it, so a split was invisible to
`executions -> holdings -> valuation` and TDPOWERSYS reported a phantom -51.9%
for three weeks. This module is the bridge.

**Nothing is trusted on one source.** A parsed factor and an observed price move
must agree before the book is touched:

    feed:   "Face Value Split ... From Rs 2/- Per Share To Re 1/- Per Share"
    prices: prev_close 1,534.80 -> open 775.00, an implied ratio of 1.98
    -> 1.0% apart, so the action is corroborated and applied

A mis-parsed subject therefore cannot corrupt the book unless the price happened
to move by the same wrong ratio on the same day. Uncorroborated actions are
recorded as `refused` and reported -- the "conservative by default" asymmetry
§10.6 already applies to the research panel, now applied to the book.

**Only share-count multipliers are in scope.** Dividends move cash, rights need
a subscription price and a decision to subscribe, and a demerger hands you a
different security. Each is recorded as `refused` with its reason rather than
guessed at, so `reconciliation` can tell a declined action from an unnoticed
one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from fractions import Fraction
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence

import polars as pl

from algotrade.core import actions as core_actions
from algotrade.domain import corporate_actions as ca
from algotrade.io import archive
from algotrade.repositories.base import AlreadyExists
from algotrade.repositories.corporate_actions import APPLIED, REFUSED

log = logging.getLogger(__name__)

#: How far the observed price move may sit from the feed factor. Matches
#: `core.reconcile.FACTOR_TOL`, which is loose on purpose: a 1:10 split shows an
#: implied ratio near 9.56 rather than 10 because the stock also *moved* that
#: day, and tightening this rejects real actions (§10.6).
FACTOR_TOL: float = 0.15

#: Below this the "action" is noise rather than a share-count change. A real
#: split or bonus moves the price by at least a third.
MIN_RATIO: float = 1.05

#: How far back `unhandled` looks. Only has to exceed the gap between two runs
#: of the discovery job; an action predating the position is not this book's
#: concern.
LOOKBACK_DAYS: int = 120

SPLIT = "split"
BONUS = "bonus"
SPLIT_AND_BONUS = "split+bonus"

#: Kinds that carry no ratio and are recorded only so a refusal can be
#: acknowledged. A ratio of 1 is stored against them, which is accurate for the
#: share count they do not change -- a demerger leaves the parent holding
#: untouched and hands you a separate security.
DEMERGER = "demerger"
RIGHTS = "rights"
BUYBACK = "buyback"
CONSOLIDATION = "consolidation"


@dataclass(frozen=True, slots=True)
class Candidate:
    """A feed action on a held symbol, before any decision has been made."""

    symbol: str
    ex_date: date
    ratio: object              # Fraction
    kind: str
    subject: str
    implied_ratio: float | None = None
    deviation: float | None = None

    @property
    def corroborated(self) -> bool:
        return self.deviation is not None and self.deviation <= FACTOR_TOL


@dataclass(slots=True)
class DiscoveryReport:
    applied: list[Candidate] = field(default_factory=list)
    refused: list[tuple[Candidate, str]] = field(default_factory=list)
    already_known: int = 0
    symbols_checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.refused

    def lines(self) -> list[str]:
        out = [
            f"{c.symbol} {c.ex_date} x{c.ratio} ({c.kind}) applied"
            for c in self.applied
        ]
        out += [
            f"{c.symbol} {c.ex_date} x{c.ratio} ({c.kind}) REFUSED -- {why}"
            for c, why in self.refused
        ]
        return out


def _kind(subject: str) -> str:
    """What the subject announces, for the record and the document key."""
    split = core_actions.split_factor(subject)
    bonus = core_actions.bonus_factor(subject)
    if split and bonus:
        return SPLIT_AND_BONUS
    return BONUS if bonus else SPLIT


def _unsupported(subject: str) -> tuple[str, str] | None:
    """The event kind and why it cannot be a share-count ratio, if it cannot.

    Checked before parsing so the refusal names the actual event rather than
    reporting "no factor found", which reads like a parser bug.

    Each is genuinely unautomatable rather than merely unimplemented:

    * **rights** -- the factor is
      `((held x P_cum) + (new x S)) / ((held + new) x P_cum)`. `S` is in the
      subject but `P_cum` is not, and it also depends on whether the holder
      actually subscribed, which no feed knows.
    * **demerger** -- you receive a *different security*. The cost
      apportionment between parent and child is set by the scheme under
      s.49(2C)/(2D) and published by the company weeks later, never by NSE.
      Splitting the basis on a guess is precisely the plausible-wrong-number
      this system refuses.
    * **buyback** -- a tender. Whether you participated is a decision, not an
      entitlement.
    * **consolidation** -- the share count FALLS, so a lot can round to zero
      while still holding cost, which needs a disposal model this does not
      have. `core.actions` already refuses to parse these, and the reason is
      MONNETISPA 2018-08-29: " Capital Reduction Rs 10 To Rs 3.30 /
      Consolidation Rs 3.30 To Rs.10" -- two split-shaped clauses whose net
      effect is nil, where reading the first leg alone yields 3.03x in the
      wrong direction.
    """
    lowered = (subject or "").lower()
    if "rights" in lowered:
        return RIGHTS, ("rights issue -- the factor needs the subscription price "
                        "and whether the holder subscribed, neither of which is "
                        "in the feed")
    if "demerger" in lowered or "de-merger" in lowered or "arrangement" in lowered:
        return DEMERGER, ("demerger -- you receive a different security, and the "
                          "cost apportionment comes from the scheme, not the feed")
    if "buyback" in lowered or "buy back" in lowered:
        return BUYBACK, ("buyback -- a tender, so a human decision rather than "
                         "an entitlement")
    if "capital reduction" in lowered or "consolidat" in lowered:
        return CONSOLIDATION, ("consolidation or capital reduction -- the share "
                               "count FALLS, which needs a disposal model this "
                               "does not have")
    return None


def candidates_from_feed(
    rows: Iterable[dict], *, symbols: set[str], start: date, end: date
) -> tuple[list[Candidate], list[tuple[Candidate, str]]]:
    """Parse feed rows into candidates for the symbols actually held.

    **Sibling actions on one ex-date multiply.** NSE announces a bonus and a
    split on the same ex-date as two separate rows, and `core.actions` composes
    them only when they arrive in a single subject string. Keeping just the
    first is the BAJFINANCE bug from §10.6: a 2:1 split and a 4:1 bonus on
    2025-06-16 compose to 10.0, and taking 2.0 left a phantom -89.9% session in
    a top-liquidity name. So rows are grouped by (symbol, ex_date) and their
    factors multiplied.
    """
    grouped: dict[tuple[str, date], list[dict]] = {}
    refused: list[tuple[Candidate, str]] = []

    for row in rows:
        symbol = (row.get("symbol") or "").strip()
        if symbol not in symbols:
            continue
        raw = (row.get("exDate") or "").strip()
        try:
            ex_date = datetime.strptime(raw, "%d-%b-%Y").date()
        except ValueError:
            continue
        if not (start <= ex_date <= end):
            continue
        grouped.setdefault((symbol, ex_date), []).append(row)

    out: list[Candidate] = []
    for (symbol, ex_date), group in sorted(grouped.items()):
        subject = " + ".join(
            (r.get("subject") or "").strip() for r in group
        )

        if (unsupported := _unsupported(subject)) is not None:
            kind, why = unsupported
            # Ratio 1, not None: it is the truth about the parent's share
            # count, and it lets the refusal be STORED. An unstorable refusal
            # is reported as unhandled on every run forever -- a reconciliation
            # failure no human can clear, which teaches you to ignore the check
            # that catches splits.
            refused.append((
                Candidate(symbol, ex_date, Fraction(1), kind, subject), why
            ))
            continue

        factor = 1.0
        found = False
        for row in group:
            part = core_actions.price_factor(row.get("subject") or "")
            if part:
                factor *= part
                found = True
        if not found:
            continue  # dividend, AGM, and the rest: no share-count change

        ratio = ca.ratio_from_factor(factor)
        if ratio <= 1:
            continue
        out.append(Candidate(symbol, ex_date, ratio, _kind(subject), subject))
    return out, refused


def corroborate(
    candidates: Sequence[Candidate], panel: pl.DataFrame
) -> list[Candidate]:
    """Attach each candidate's observed price move on its ex-date.

    Uses `prev_close / open` rather than `prev_close / close`: the open is the
    first post-adjustment print, so it is the least contaminated by the day's
    own trading. NSE leaves `prev_close` unadjusted on the ex-date, and that
    discontinuity is exactly the evidence wanted.

    A candidate whose ex-date is not in the panel comes back with a null
    deviation and is therefore not corroborated -- absence of evidence is not
    treated as evidence.
    """
    if panel.is_empty():
        return list(candidates)

    wanted = panel.select("symbol", "date", "prev_close", "open")
    lookup = {
        (row["symbol"], row["date"]): row
        for row in wanted.iter_rows(named=True)
    }

    out: list[Candidate] = []
    for c in candidates:
        row = lookup.get((c.symbol, c.ex_date))
        implied = deviation = None
        if row and row["prev_close"] and row["open"] and row["open"] > 0:
            implied = float(row["prev_close"]) / float(row["open"])
            deviation = abs(implied / float(c.ratio) - 1)
        out.append(
            Candidate(c.symbol, c.ex_date, c.ratio, c.kind, c.subject,
                      implied, deviation)
        )
    return out


class CorporateActionService:
    """Finds, corroborates and records actions. The only writer of the book's
    action history."""

    def __init__(
        self,
        *,
        holdings,
        corporate_actions,
        action_dir: Path | str = "data/raw/actions",
        session_dirs: Sequence[str | Path] = ("data/sessions",),
    ) -> None:
        self._holdings = holdings
        self._repo = corporate_actions
        self._action_dir = Path(action_dir)
        self._session_dirs = session_dirs

    def discover(
        self, portfolio_id: str, *, start: date, end: date, now: datetime,
        apply: bool = True,
    ) -> DiscoveryReport:
        """Find and record every action touching this book in [start, end].

        Recording is idempotent: the document key is
        `portfolio:symbol:ex_date:kind`, so a re-run over a window already
        processed hits `AlreadyExists` and changes nothing. That is what makes
        it safe to run daily over a rolling window.
        """
        report = DiscoveryReport()
        symbols = set(self._holdings.symbols(portfolio_id))
        report.symbols_checked = len(symbols)
        if not symbols:
            return report

        rows = self._load_feed(start, end)
        candidates, unsupported = candidates_from_feed(
            rows, symbols=symbols, start=start, end=end
        )
        report.refused.extend(unsupported)
        if not candidates and not unsupported:
            return report

        panel = self._load_panel(candidates, start, end)
        candidates = corroborate(candidates, panel)

        known = self._repo.seen(portfolio_id)
        for candidate in candidates:
            key = self._repo.key(
                portfolio_id, candidate.symbol, candidate.ex_date, candidate.kind
            )
            if key in known:
                report.already_known += 1
                continue
            if not candidate.corroborated:
                report.refused.append((
                    candidate,
                    f"not corroborated by the ex-date prices "
                    f"(implied {candidate.implied_ratio}, feed "
                    f"{float(candidate.ratio):.4f})",
                ))
                continue
            report.applied.append(candidate)

        if apply:
            self._record(portfolio_id, report, now)
        return report

    def unhandled(
        self, portfolio_id: str, *, today: date, lookback_days: int = LOOKBACK_DAYS
    ) -> list[str]:
        """Feed actions on held symbols that are neither applied nor refused.

        What `reconciliation` calls. Writes nothing -- a check that repaired
        what it found could never report a problem, and the repair belongs in
        the job that a human can see run.

        Bounded by `lookback_days` rather than scanning all history: an action
        from before a position was opened is not this book's concern, and the
        window only has to be longer than the gap between two runs of the
        discovery job for nothing to slip through.
        """
        report = self.discover(
            portfolio_id,
            start=today - timedelta(days=lookback_days),
            end=today,
            now=datetime.min,          # unused: apply=False writes nothing
            apply=False,
        )
        return [
            f"{c.symbol} {c.ex_date} x{c.ratio} ({c.kind})"
            for c in report.applied
        ] + [
            f"{c.symbol} {c.ex_date} ({c.kind}) -- {why}"
            for c, why in report.refused
        ]

    def outstanding(self, portfolio_id: str) -> list[str]:
        """Refusals nobody has acknowledged. A standing item, not a new one.

        Separate from `unhandled` because the two mean different things and
        deserve different severities: `unhandled` is "the system has not looked
        at this yet" and is fixed by running the job; this is "the system
        looked, and a human has to finish it". Collapsing them makes a
        demerger an unclearable error.
        """
        return [
            f"{d['symbol']} {d['ex_date']} ({d.get('kind','?')}) -- "
            f"{d.get('refused_reason','')}"
            for d in self._repo.outstanding(portfolio_id)
        ]

    def acknowledge(
        self, portfolio_id: str, *, symbol: str, ex_date: date, kind: str,
        now: datetime, note: str = "",
    ) -> bool:
        """Record that a human has dealt with one refusal."""
        return self._repo.acknowledge(
            self._repo.key(portfolio_id, symbol, ex_date, kind),
            now=now, note=note,
        )

    def _record(
        self, portfolio_id: str, report: DiscoveryReport, now: datetime
    ) -> None:
        for candidate in report.applied:
            action = ca.CorporateAction(
                action_id=self._repo.key(
                    portfolio_id, candidate.symbol, candidate.ex_date,
                    candidate.kind,
                ),
                symbol=candidate.symbol,
                ex_date=candidate.ex_date,
                ratio=candidate.ratio,
                kind=candidate.kind,
                subject=candidate.subject,
            )
            try:
                self._repo.record(
                    portfolio_id=portfolio_id, action=action, status=APPLIED,
                    now=now, implied_ratio=candidate.implied_ratio,
                    deviation=candidate.deviation,
                )
            except AlreadyExists:
                pass  # concurrent run got there first; the key is the guard

        for candidate, why in report.refused:
            action = ca.CorporateAction(
                action_id=self._repo.key(
                    portfolio_id, candidate.symbol, candidate.ex_date,
                    candidate.kind,
                ),
                symbol=candidate.symbol,
                ex_date=candidate.ex_date,
                ratio=candidate.ratio,
                kind=candidate.kind,
                subject=candidate.subject,
            )
            try:
                self._repo.record(
                    portfolio_id=portfolio_id, action=action, status=REFUSED,
                    now=now, refused_reason=why,
                    implied_ratio=candidate.implied_ratio,
                    deviation=candidate.deviation,
                )
            except AlreadyExists:
                pass

    def _load_feed(self, start: date, end: date) -> list[dict]:
        """Every cached feed row touching [start, end].

        Reads the quarterly JSON `io.actions` already maintains rather than
        hitting NSE: the daily job must not depend on a live third-party
        endpoint, and the cache is refreshed by its own step.
        """
        import json

        from algotrade.io import actions as io_actions

        rows: list[dict] = []
        for year, quarter in io_actions.quarters_between(start, end):
            path = io_actions.cache_path(self._action_dir, year, quarter)
            if not path.exists():
                log.warning("no cached corporate actions for %sQ%s", year, quarter)
                continue
            try:
                rows.extend(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                log.error("unreadable action cache %s: %s", path, exc)
        return rows

    def _load_panel(
        self, candidates: Sequence[Candidate], start: date, end: date
    ) -> pl.DataFrame:
        """Just the sessions the candidates need, for corroboration."""
        if not candidates:
            return pl.DataFrame()
        first = min(c.ex_date for c in candidates)
        last = max(c.ex_date for c in candidates)
        try:
            panel, _ = archive.load_panel(
                self._session_dirs, start=first, end=last
            )
        except Exception as exc:  # noqa: BLE001 -- corroboration is best-effort
            log.error("could not load sessions for corroboration: %s", exc)
            return pl.DataFrame()
        return panel
