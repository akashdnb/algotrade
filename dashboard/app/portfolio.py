"""Which portfolio a request is about.

Several portfolios live in one database -- a momentum book, a shadow book that
measures the cost of your discretion, a second strategy -- so the portfolio is
a property of the *request*, chosen with `?portfolio=`, and `PORTFOLIO_ID` is
only the default.

**The database is NOT selectable, deliberately.** That is the paper/prod
boundary, and the trading system makes it structural rather than a setting:
separate databases per environment, so a forgotten filter cannot mix paper
with real money, and `validation` refuses a `live_` portfolio outside prod in
three places. A dropdown would undo that twice over -- one credential would
need to reach both databases, so a leaked environment variable would reach
real money, and choosing an environment would become a runtime accident
rather than a deployment decision. Prod gets its own deployment, its own
scoped credential and its own URL.
"""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import HTTPException, Request, status

from app.repositories.portfolios import PortfolioRepository
from app.settings import Settings

#: Query parameter carrying the selection between pages.
PARAM = "portfolio"


class Selection:
    """The active portfolio, the alternatives, and how to build links."""

    __slots__ = ("active", "options", "default")

    def __init__(self, active: str, options: list[dict], default: str) -> None:
        self.active = active
        self.options = options
        self.default = default

    @property
    def is_default(self) -> bool:
        return self.active == self.default

    @property
    def many(self) -> bool:
        """A picker for one portfolio is furniture, not a control."""
        return len(self.options) > 1

    @property
    def label(self) -> str:
        for option in self.options:
            if option["_id"] == self.active:
                return option.get("label") or self.active
        return self.active

    def url(self, path: str, **params: str) -> str:
        """A link that keeps the current portfolio.

        Omitted when it is the default, so the common case has clean URLs and
        a bookmarked link does not pin a portfolio you later stop using.
        """
        query = dict(params)
        if not self.is_default:
            query[PARAM] = self.active
        return f"{path}?{urlencode(query)}" if query else path


def resolve(request: Request, settings: Settings, db) -> Selection:
    """Pick the portfolio for this request, refusing anything unknown.

    Validated against the database rather than merely accepted: an unchecked
    id would render an empty page for a typo -- indistinguishable from a
    portfolio that genuinely holds nothing.
    """
    repo = PortfolioRepository(db)
    options = repo.list_all()
    known = {doc["_id"] for doc in options}

    requested = (request.query_params.get(PARAM) or "").strip()
    if requested:
        if requested not in known:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"No portfolio {requested!r} in {settings.mongodb_database}",
            )
        active = requested
    else:
        active = settings.portfolio_id

    # The configured default may not exist yet -- a fresh database, or a
    # PORTFOLIO_ID typo. Fall back to whatever is there rather than 500, and
    # let the page show the truth.
    if active not in known and options:
        active = options[0]["_id"]

    return Selection(active=active, options=options,
                     default=settings.portfolio_id)
