"""Outbound notifications and their templates.

Three rules the templates follow, all learned from PROJECT.md's behavioural
findings rather than from style preference:

* **Every message states the portfolio and the as-of date.** You will read
  these months later with no context.
* **Drawdown is as prominent as return.** §11p priced quitting during an
  underperformance streak at 27-40% of terminal wealth, and §11o's P4 concluded
  the behavioural risk cannot be sized away. A message that shows only gains
  trains exactly the wrong reflex.
* **The daily message carries no trade list.** The strategy rebalances monthly
  and the rank buffer exists so drift is *not* traded. A daily buy/sell list is
  a machine for tempting you to overtrade, and the entire cost case assumed
  ~21% annual turnover.

Sending never raises. A broken alert channel must not fail a run that otherwise
succeeded -- but it is always recorded as failed, because a silent delivery
failure is invisible by definition (§10.5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from algotrade.domain import money
from algotrade.platform.telegram import TelegramError, escape

log = logging.getLogger(__name__)


def _rupees(value: Decimal | None) -> str:
    """Indian digit grouping: 2,84,310 rather than 284,310."""
    if value is None:
        return "-"
    negative = value < 0
    whole = f"{abs(value):.0f}"
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        whole = ",".join(parts + [tail])
    return ("-" if negative else "") + "₹" + whole


def _pct(value: float | None, *, signed: bool = True) -> str:
    if value is None:
        return "-"
    return f"{value:+.2%}" if signed else f"{value:.2%}"


@dataclass(frozen=True, slots=True)
class DailyFacts:
    """Everything the daily message needs. Assembled by the caller so this
    module stays free of repository access and is trivially testable."""

    portfolio_id: str
    label: str
    as_of: date
    nav: Decimal
    cash: Decimal
    day_change: Decimal | None = None
    day_change_pct: float | None = None
    holdings_count: int = 0
    month_return: float | None = None
    month_benchmark: float | None = None
    ytd_return: float | None = None
    ytd_benchmark: float | None = None
    drawdown: float = 0.0
    peak_date: date | None = None
    #: (symbol, pct) pairs, already sorted.
    best: list[tuple[str, float]] = None  # type: ignore[assignment]
    worst: list[tuple[str, float]] = None  # type: ignore[assignment]
    unexecuted_items: int = 0
    unexecuted_age_days: int = 0
    sessions_to_rebalance: int | None = None
    next_rebalance: date | None = None
    #: Longest historical underperformance streak, for context (§11o P5).
    current_streak_days: int = 0


def render_daily(f: DailyFacts) -> str:
    """Status and drift. No trade list -- see the module docstring."""
    e = escape
    name = f"{f.portfolio_id}" + (f" · {f.label}" if f.label else "")
    lines = [
        f"\U0001F4CA *Daily* · {e(name)} · {e(f.as_of.strftime('%d %b %Y'))}",
        "",
        "```",
        f"NAV        {_rupees(f.nav):<14}"
        + (f"({_rupees(f.day_change)} · {_pct(f.day_change_pct)})"
           if f.day_change is not None else ""),
        f"Cash       {_rupees(f.cash)}",
        f"Holdings   {f.holdings_count} names",
    ]

    if f.month_return is not None:
        excess = (
            f"   ({_pct(f.month_return - f.month_benchmark)})"
            if f.month_benchmark is not None else ""
        )
        lines.append(
            f"Month      {_pct(f.month_return):<12}"
            f"NIFTY {_pct(f.month_benchmark)}{excess}"
        )
    if f.ytd_return is not None:
        excess = (
            f"   ({_pct(f.ytd_return - f.ytd_benchmark)})"
            if f.ytd_benchmark is not None else ""
        )
        lines.append(
            f"YTD        {_pct(f.ytd_return):<12}"
            f"NIFTY {_pct(f.ytd_benchmark)}{excess}"
        )

    peak = f" (peak {f.peak_date:%d %b})" if f.peak_date else ""
    lines.append(f"Drawdown   {_pct(f.drawdown)}{peak}")
    lines.append("```")

    if f.best:
        lines.append("Top   " + e("  ·  ".join(
            f"{s} {p:+.1%}" for s, p in f.best[:3])))
    if f.worst:
        lines.append("Worst " + e("  ·  ".join(
            f"{s} {p:+.1%}" for s, p in f.worst[:3])))

    if f.unexecuted_items:
        lines += ["", e(
            f"⚠️ {f.unexecuted_items} item(s) unexecuted for "
            f"{f.unexecuted_age_days} day(s)"
        )]

    # Context, not a call to action: the strategy trades monthly and the buffer
    # exists so drift is held, not corrected.
    if f.next_rebalance:
        gap = (f" ({f.sessions_to_rebalance} sessions)"
               if f.sessions_to_rebalance is not None else "")
        lines += ["", e(f"Next rebalance: {f.next_rebalance:%d %b %Y}{gap}")]

    # Shown so a streak reads as normal rather than as evidence of breakage.
    # §11o P5: the historical worst is 1,203 days and dilution does not shorten it.
    if f.current_streak_days >= 30:
        lines.append(e(
            f"Behind NIFTY {f.current_streak_days} days "
            f"(historical worst: 1,203)"
        ))

    return "\n".join(lines)


def render_ingest_failure(*, business_date: date, stage: str, detail: str,
                          run_url: str = "", self_heals: bool = True) -> str:
    """Failure alerts state blast radius and whether they self-heal.

    An alert without those is one you learn to ignore.
    """
    e = escape
    lines = [
        f"\U0001F534 *Job failed* · {e(stage)} · {e(str(business_date))}",
        "",
        "```",
        detail[:600],
        "```",
    ]
    if run_url:
        lines.append(e(run_url))
    lines += ["", e("Impact: no snapshot for this session; valuation is stale.")]
    lines.append(e(
        "Self-heals: tomorrow's run backfills automatically. Act now only if "
        "this repeats 2 days running."
        if self_heals else
        "Does NOT self-heal -- manual recovery required."
    ))
    return "\n".join(lines)


def render_stale_data(*, as_of: date, last_session: date, missing: list[date],
                      days_behind: int) -> str:
    e = escape
    listed = ", ".join(str(d) for d in missing[:6])
    if len(missing) > 6:
        listed += f", +{len(missing) - 6} more"
    return "\n".join([
        f"\U0001F7E0 *Stale market data* · {e(str(as_of))}",
        "",
        e(f"Last session ingested: {last_session} ({days_behind} trading days behind)"),
        e(f"Missing: {listed}"),
        "",
        e("All valuation and any rebalance are BLOCKED until this clears."),
        e("Recover: gh workflow run recover.yml "
          f"-f from_date={missing[0]} -f to_date={missing[-1]}") if missing else "",
    ])


class NotificationService:
    """Renders, dedupes, sends, records. Never raises."""

    def __init__(self, *, repository, client, chat_id: str = "") -> None:
        self._repo = repository
        self._client = client
        self._chat_id = chat_id

    def send(
        self,
        *,
        kind: str,
        text: str,
        now: datetime,
        portfolio_id: str | None = None,
        business_date: date | None = None,
        silent: bool = False,
    ) -> str | None:
        """Send once. Returns the notification id, or None if already sent.

        Deduping happens before the network call, so a re-run of the daily job
        cannot post a second summary (§2.8).
        """
        notification_id = self._repo.claim(
            kind=kind, text=text, now=now, portfolio_id=portfolio_id,
            business_date=business_date, chat_id=self._chat_id,
        )
        if notification_id is None:
            log.info("notification %s already sent for %s", kind, business_date)
            return None

        if self._client is None:
            log.warning("no Telegram client configured; %s not sent", kind)
            self._repo.mark_failed(notification_id, error="no client configured")
            return notification_id

        try:
            ids = self._client.send(text, silent=silent)
        except TelegramError as exc:
            # Recorded, not raised: an alerting failure must not fail the run,
            # but it must be visible to the watchdog (§10.5).
            log.error("telegram send failed for %s: %s", kind, exc)
            self._repo.mark_failed(notification_id, error=str(exc))
            return notification_id
        except Exception as exc:  # noqa: BLE001
            log.exception("unexpected error sending %s", kind)
            self._repo.mark_failed(notification_id, error=f"{type(exc).__name__}: {exc}")
            return notification_id

        self._repo.mark_sent(
            notification_id, now=now,
            telegram_message_id=ids[0] if ids else None,
        )
        return notification_id


# ------------------------------------------------- production daily report --


@dataclass(frozen=True, slots=True)
class ReportFacts:
    """Everything the production daily report needs, already computed.

    Assembled by the caller so this module performs no queries and the whole
    template is testable from a literal.
    """

    portfolio_id: str
    label: str
    as_of: date
    nav: Decimal
    cash: Decimal
    positions: int

    day_pnl: Decimal | None = None
    day_return: float | None = None
    since_inception: float | None = None
    drawdown: float = 0.0

    #: (symbol, weight) already sorted, largest first.
    top_holdings: list[tuple[str, float]] = None  # type: ignore[assignment]
    signals_generated: int = 0
    signal_date: date | None = None

    data_fresh: bool = True
    data_detail: str = ""
    reconciliation_ok: bool = True
    reconciliation_failures: list[str] = None  # type: ignore[assignment]

    rebalance_due: bool = False
    next_rebalance: date | None = None
    pending_items: int = 0
    #: Days behind NIFTY, for the §11o P5 context line.
    streak_days: int = 0


RULE = "━" * 16


def render_report(f: ReportFacts) -> str:
    """The message you read every evening for years.

    Deterministic: the same facts always render the same bytes, which is what
    makes it unit-testable and what stops a formatting change from looking like
    a data change.

    Reconciliation status is at the BOTTOM and unmissable when it fails. A
    portfolio whose book disagrees with its trade record has a NAV that means
    nothing, so the failure has to outrank the number above it.
    """
    e = escape
    name = f.label or f.portfolio_id
    lines = [
        e(RULE),
        f"*{e(name)}*",
        e(RULE),
        "",
        "```",
        f"Date        {f.as_of:%d %b %Y}",
        f"NAV         {_rupees(f.nav)}",
        f"Cash        {_rupees(f.cash)}",
        f"Positions   {f.positions}",
    ]

    if f.day_pnl is not None:
        change = _pct(f.day_return) if f.day_return is not None else ""
        lines.append(f"Day P&L     {_rupees(f.day_pnl)}  {change}")
    if f.since_inception is not None:
        lines.append(f"Inception   {_pct(f.since_inception)}")
    # Drawdown is never omitted. §11p priced quitting during a streak at
    # 27-40% of terminal wealth; a report showing only gains trains the wrong
    # reflex.
    lines.append(f"Drawdown    {_pct(f.drawdown)}")
    lines.append("```")

    if f.top_holdings:
        lines += ["", "*Top holdings*", "```"]
        lines += [f"{s:<12} {w:>5.1%}" for s, w in f.top_holdings[:5]]
        lines.append("```")

    lines += ["", "```"]
    signal_note = (
        f"{f.signals_generated}" +
        (f"  ({f.signal_date})" if f.signal_date else "")
    )
    lines.append(f"Signals     {signal_note}")
    lines.append(f"Data        {'OK' if f.data_fresh else 'STALE'}"
                 + (f"  {f.data_detail}" if f.data_detail else ""))
    lines.append(f"Reconcile   {'PASS' if f.reconciliation_ok else 'FAIL'}")
    if f.rebalance_due:
        lines.append("Rebalance   DUE")
    elif f.next_rebalance:
        lines.append(f"Rebalance   {f.next_rebalance:%d %b}")
    lines.append("```")

    if f.pending_items:
        lines += ["", e(f"⚠️ {f.pending_items} recommendation item(s) unexecuted")]

    # Context so a streak reads as normal rather than as breakage.
    if f.streak_days >= 30:
        lines.append(e(
            f"Behind NIFTY {f.streak_days} days (historical worst: 1,203)"))

    if not f.reconciliation_ok:
        lines += ["", e("❌ RECONCILIATION FAILED"), ""]
        lines += [e(f"• {p}") for p in (f.reconciliation_failures or [])[:6]]
        lines += ["", e("NAV above is NOT trustworthy until this is resolved.")]

    lines += ["", e(RULE)]
    return "\n".join(lines)


def render_rebalance(
    *,
    portfolio_id: str,
    period: str,
    set_id: str,
    buys: int,
    sells: int,
    holds: int,
    deployed: Decimal,
    charges: Decimal,
    turnover: float | None,
    entering: list[str],
    leaving: list[str],
    expires_on: date | None,
    adjusting: list[str] | None = None,
) -> str:
    """The monthly rebalance alert. Recommendation only -- never an order.

    **`leaving` is positions being CLOSED, not every sell.** With equal
    weighting a name that stays in the target still has its quantity adjusted,
    so deriving this list from the sell instructions put trims under
    "Exiting" -- ATHERENERG "sell 1" while the target kept 4, SKYGOLD "sell 1"
    while the target kept 8 (rehearsal, 2026-09-05). Read on a phone that says
    close the position, and this is the one message in the system where a
    wording choice moves real money.

    `adjusting` is where those belong: same name, different size.
    """
    e = escape
    lines = [
        e(RULE),
        "*REBALANCE REQUIRED*",
        e(RULE),
        "",
        "```",
        f"Portfolio   {portfolio_id}",
        f"Period      {period}",
        f"Trades      {buys} buys, {sells} sells",
        f"Holds       {holds}",
        f"Deployed    {_rupees(deployed)}",
        f"Charges     {_rupees(charges)}",
    ]
    if turnover is not None:
        lines.append(f"Turnover    {turnover:.1%}")
    lines.append("```")

    if leaving:
        lines += ["", "*Exiting*", "```", ", ".join(leaving[:12]), "```"]
    if entering:
        lines += ["", "*Entering*", "```", ", ".join(entering[:12]), "```"]
    if adjusting:
        lines += ["", "*Adjusting*", "```", ", ".join(adjusting[:12]),
                  "```", "_size change only -- these stay in the book_"]

    lines += ["", "```", f"Set         {set_id}"]
    if expires_on:
        lines.append(f"Expires     {expires_on:%d %b %Y}")
    lines.append("```")
    lines += ["", e("Sells first -- T+1 settlement funds the buys."),
              e("No orders placed. Record fills with record-execution.yml."),
              "", e(RULE)]
    return "\n".join(lines)
