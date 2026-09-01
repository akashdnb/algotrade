"""Server-rendered inline SVG. No JavaScript, no CDN, no canvas.

The data changes once a day. A charting library would mean a CDN script, a
client-side render, and a dependency that can break independently of this
app -- for a line whose shape is fixed by 09:00 each morning. Inline SVG is
~100 lines, renders instantly on a phone, works with JS disabled, and lets the
Content-Security-Policy stay at `script-src 'none'`.

The drawdown chart is deliberately given the same visual weight as the NAV
chart. §11p of the trading system's research priced abandoning the strategy
during a drawdown at 27-40% of terminal wealth; a dashboard that draws gains
large and losses small trains exactly the reflex the Investment Policy exists
to prevent.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Point:
    label: str
    value: float


def _escape(text: str) -> str:
    return html.escape(str(text), quote=True)


def _scale(values: list[float]) -> tuple[float, float]:
    """(low, high) with a little headroom, never a zero-height range."""
    if not values:
        return 0.0, 1.0
    low, high = min(values), max(values)
    if low == high:
        pad = abs(low) * 0.05 or 1.0
        return low - pad, high + pad
    pad = (high - low) * 0.08
    return low - pad, high + pad


def _points(values: list[float], width: float, height: float,
            low: float, high: float) -> list[tuple[float, float]]:
    if not values:
        return []
    span = high - low or 1.0
    if len(values) == 1:
        return [(width / 2, height - ((values[0] - low) / span) * height)]
    step = width / (len(values) - 1)
    return [
        (i * step, height - ((v - low) / span) * height)
        for i, v in enumerate(values)
    ]


def _path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    head, *rest = points
    return "M " + f"{head[0]:.1f},{head[1]:.1f}" + "".join(
        f" L {x:.1f},{y:.1f}" for x, y in rest
    )


def line_chart(
    series: list[Point],
    *,
    width: int = 720,
    height: int = 200,
    title: str = "",
    rebase: bool = False,
) -> str:
    """A single line. `rebase` normalises to 100 at the first point, which is
    the only way to compare two series with different absolute levels."""
    if not series:
        return _empty(width, height, "no data yet")

    values = [p.value for p in series]
    if rebase and values[0]:
        base = values[0]
        values = [v / base * 100.0 for v in values]

    low, high = _scale(values)
    pts = _points(values, width, height, low, high)
    rising = values[-1] >= values[0]
    stroke = "var(--up)" if rising else "var(--down)"

    area = ""
    if len(pts) > 1:
        area = (
            f'<path d="{_path(pts)} L {pts[-1][0]:.1f},{height} '
            f'L {pts[0][0]:.1f},{height} Z" fill="{stroke}" opacity="0.08"/>'
        )

    dot = (f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="3.5" '
           f'fill="{stroke}"/>') if pts else ""

    return f"""<svg class="chart" viewBox="0 0 {width} {height}" width="100%"
     height="{height}" role="img" preserveAspectRatio="none"
     aria-label="{_escape(title or 'chart')}">
  {area}
  <path d="{_path(pts)}" fill="none" stroke="{stroke}" stroke-width="2"
        stroke-linejoin="round" stroke-linecap="round"/>
  {dot}
</svg>"""


def drawdown_chart(
    series: list[Point], *, width: int = 720, height: int = 140
) -> str:
    """Filled area hanging below zero. Zero is pinned to the top edge, because
    a drawdown chart that floats its own baseline makes -2% look like -40%."""
    if not series:
        return _empty(width, height, "no data yet")

    # Drawdowns are <= 0. Depth is measured from the top of the box.
    values = [min(0.0, p.value) for p in series]
    worst = min(values + [-0.01])
    span = abs(worst) * 1.15 or 1.0

    if len(values) == 1:
        pts = [(width / 2, abs(values[0]) / span * height)]
    else:
        step = width / (len(values) - 1)
        pts = [(i * step, abs(v) / span * height) for i, v in enumerate(values)]

    fill = (f'<path d="{_path(pts)} L {pts[-1][0]:.1f},0 L {pts[0][0]:.1f},0 Z" '
            f'fill="var(--down)" opacity="0.16"/>')

    return f"""<svg class="chart" viewBox="0 0 {width} {height}" width="100%"
     height="{height}" role="img" preserveAspectRatio="none"
     aria-label="Drawdown, worst {worst * 100:.1f} percent">
  {fill}
  <path d="{_path(pts)}" fill="none" stroke="var(--down)" stroke-width="2"
        stroke-linejoin="round"/>
  <line x1="0" y1="0" x2="{width}" y2="0" stroke="var(--grid)" stroke-width="1"/>
</svg>"""


def weight_bar(weight: float, *, target: float = 1 / 30) -> str:
    """A position's weight against the equal-weight target.

    Coloured past 1.5x target, because an oversized position is usually an
    unexecuted sell -- which is the thing actually worth spotting in a list of
    thirty near-identical rows.
    """
    ratio = (weight / target) if target else 0.0
    pct = max(0.0, min(ratio / 2.0, 1.0)) * 100
    tone = "over" if ratio > 1.5 else ("under" if ratio < 0.5 else "at")
    return (f'<span class="wbar {tone}" title="{weight * 100:.2f}% of NAV">'
            f'<i style="width:{pct:.1f}%"></i></span>')


def _empty(width: int, height: int, message: str) -> str:
    return (f'<svg class="chart empty" viewBox="0 0 {width} {height}" '
            f'width="100%" height="{height}" role="img" '
            f'aria-label="{_escape(message)}">'
            f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
            f'dominant-baseline="middle" class="empty-label">'
            f'{_escape(message)}</text></svg>')


def to_points(rows: list[dict], *, x: str, y: str) -> list[Point]:
    """Rows with a null `y` are skipped, not coerced to zero -- a missing
    benchmark reading is not a benchmark of nought."""
    out: list[Point] = []
    for row in rows:
        value = row.get(y)
        if value is None:
            continue
        if isinstance(value, Decimal):
            value = float(value)
        out.append(Point(label=str(row.get(x, "")), value=float(value)))
    return out
