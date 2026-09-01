"""Server-rendered inline SVG. No JavaScript, no CDN, no canvas.

The data changes once a day. A charting library would mean a CDN script, a
client-side render, and a dependency that can break on its own -- for a line
whose shape is fixed by 09:00 each morning. Inline SVG loads instantly on a
phone, works with JS disabled, and lets the CSP stay at `script-src 'none'`.

**Scale is the whole design problem here, not decoration.**

An auto-scaled chart makes every series fill its box, so an 11-session move of
2.7% looked like a mountain range and a drawdown of 0.02% rendered as a wall of
red. For this dashboard that is not merely ugly, it is the specific failure
§11p priced at 27-40% of terminal wealth: a page that draws small losses large
trains the reflex the Investment Policy exists to prevent.

So:

* **Drawdown is scaled against the expected worst case (-41.6%), not against
  what has happened.** Today's -0.02% is correctly a hairline; the chart only
  fills when the drawdown is genuinely the one you were warned about.
* **NAV is scaled to its own range but the range is LABELLED**, so a jagged
  line is legible as the 2.7% it is rather than read as drama.

Hover values come from SVG `<title>`, which browsers render natively -- an
interaction layer that costs no script and survives `script-src 'none'`.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from decimal import Decimal

#: From INVESTMENT-POLICY.md §4, the 50/50 allocation. The drawdown chart is a
#: gauge against this, which is the only reading that makes a live number mean
#: anything.
EXPECTED_MAX_DRAWDOWN = -0.416

WIDTH = 720
PAD_L, PAD_R, PAD_T, PAD_B = 8, 58, 14, 22


@dataclass(frozen=True, slots=True)
class Point:
    label: str
    value: float


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


def _fmt_rupees(value: float) -> str:
    """Compact, for an axis label where precision is noise."""
    magnitude = abs(value)
    if magnitude >= 1e7:
        return f"₹{value / 1e7:.2f}Cr"
    if magnitude >= 1e5:
        return f"₹{value / 1e5:.2f}L"
    if magnitude >= 1e3:
        return f"₹{value / 1e3:.0f}k"
    return f"₹{value:.0f}"


def _path(points: list[tuple[float, float]]) -> str:
    if not points:
        return ""
    head, *rest = points
    return f"M {head[0]:.1f},{head[1]:.1f}" + "".join(
        f" L {x:.1f},{y:.1f}" for x, y in rest)


def _hotspots(points: list[tuple[float, float]], series: list[Point],
              fmt, height: int) -> str:
    """Transparent hit areas carrying a native tooltip.

    `<title>` is the only hover affordance available under `script-src 'none'`,
    and for a chart read once a day it is enough.
    """
    if len(points) != len(series) or not points:
        return ""
    step = (points[-1][0] - points[0][0]) / max(1, len(points) - 1) or WIDTH
    cells = []
    for (x, _y), point in zip(points, series):
        cells.append(
            f'<rect x="{x - step / 2:.1f}" y="0" width="{step:.1f}" '
            f'height="{height}" fill="transparent">'
            f'<title>{_e(point.label)} · {_e(fmt(point.value))}</title></rect>'
        )
    return "".join(cells)


def _empty(height: int, message: str) -> str:
    return (
        f'<svg class="chart empty" viewBox="0 0 {WIDTH} {height}" '
        f'width="100%" role="img" aria-label="{_e(message)}">'
        f'<text x="{WIDTH / 2}" y="{height / 2}" text-anchor="middle" '
        f'dominant-baseline="middle" class="c-empty">{_e(message)}</text></svg>'
    )


# ------------------------------------------------------------------ line ---


def line_chart(
    series: list[Point],
    *,
    height: int = 190,
    label: str = "value",
    reference: float | None = None,
    reference_label: str = "",
) -> str:
    """A NAV line with a labelled y-range and an optional reference level.

    The range labels are the point: without them a 2.7% move and a 40% move
    draw the identical shape.
    """
    if not series:
        return _empty(height, "no data yet")

    values = [p.value for p in series]
    low, high = min(values), max(values)
    if reference is not None:
        low, high = min(low, reference), max(high, reference)
    if low == high:
        pad = abs(low) * 0.02 or 1.0
        low, high = low - pad, high + pad
    else:
        pad = (high - low) * 0.12
        low, high = low - pad, high + pad

    plot_w = WIDTH - PAD_L - PAD_R
    plot_h = height - PAD_T - PAD_B
    span = high - low

    def y_of(v: float) -> float:
        return PAD_T + (1 - (v - low) / span) * plot_h

    if len(values) == 1:
        pts = [(PAD_L + plot_w / 2, y_of(values[0]))]
    else:
        step = plot_w / (len(values) - 1)
        pts = [(PAD_L + i * step, y_of(v)) for i, v in enumerate(values)]

    rising = values[-1] >= values[0]
    stroke = "var(--c-up)" if rising else "var(--c-down)"

    area = ""
    if len(pts) > 1:
        area = (f'<path d="{_path(pts)} L {pts[-1][0]:.1f},{PAD_T + plot_h:.1f} '
                f'L {pts[0][0]:.1f},{PAD_T + plot_h:.1f} Z" '
                f'fill="{stroke}" opacity="0.10"/>')

    ref = ""
    if reference is not None and low <= reference <= high:
        ry = y_of(reference)
        ref = (
            f'<line x1="{PAD_L}" y1="{ry:.1f}" x2="{PAD_L + plot_w}" y2="{ry:.1f}" '
            f'stroke="var(--c-grid)" stroke-width="1" stroke-dasharray="4 4"/>'
            f'<text x="{PAD_L + plot_w + 6}" y="{ry:.1f}" class="c-axis" '
            f'dominant-baseline="middle">{_e(reference_label)}</text>'
        )

    axis = (
        f'<text x="{PAD_L + plot_w + 6}" y="{PAD_T + 4}" class="c-axis">'
        f'{_e(_fmt_rupees(high - pad))}</text>'
        f'<text x="{PAD_L + plot_w + 6}" y="{PAD_T + plot_h:.0f}" class="c-axis">'
        f'{_e(_fmt_rupees(low + pad))}</text>'
        f'<text x="{PAD_L}" y="{height - 5}" class="c-axis">'
        f'{_e(series[0].label)}</text>'
        f'<text x="{PAD_L + plot_w}" y="{height - 5}" class="c-axis" '
        f'text-anchor="end">{_e(series[-1].label)}</text>'
    )

    dot = (f'<circle cx="{pts[-1][0]:.1f}" cy="{pts[-1][1]:.1f}" r="4" '
           f'fill="{stroke}" stroke="var(--surface)" stroke-width="2"/>')

    return f"""<svg class="chart" viewBox="0 0 {WIDTH} {height}" width="100%"
     role="img" aria-label="{_e(label)}, {_e(_fmt_rupees(values[0]))} to {_e(_fmt_rupees(values[-1]))}">
  {area}{ref}
  <path d="{_path(pts)}" fill="none" stroke="{stroke}" stroke-width="2"
        stroke-linejoin="round" stroke-linecap="round"/>
  {dot}{axis}
  {_hotspots(pts, series, _fmt_rupees, height)}
</svg>"""


# -------------------------------------------------------------- drawdown ---


def drawdown_chart(
    series: list[Point],
    *,
    height: int = 190,
    expected: float = EXPECTED_MAX_DRAWDOWN,
) -> str:
    """Depth from the peak, scaled against the drawdown you were warned about.

    The y-axis runs 0 to `expected` (-41.6%) whatever has actually happened, so
    a shallow drawdown looks shallow. Auto-scaling here drew today's -0.02% as
    a full red box, which is precisely the misreading the Investment Policy
    exists to prevent.

    If a drawdown ever exceeds the expectation the axis follows it, because at
    that point the exceedance IS the news.
    """
    if not series:
        return _empty(height, "no data yet")

    values = [min(0.0, p.value) for p in series]
    worst = min(values)
    floor = min(expected, worst * 1.05)

    # Wider than the NAV chart's: this gutter has to hold "-41.6% expected",
    # which overflowed the card edge at pad_r.
    pad_r = 104
    plot_w = WIDTH - PAD_L - pad_r
    plot_h = height - PAD_T - PAD_B

    def y_of(v: float) -> float:
        return PAD_T + (v / floor) * plot_h if floor else PAD_T

    if len(values) == 1:
        pts = [(PAD_L + plot_w / 2, y_of(values[0]))]
    else:
        step = plot_w / (len(values) - 1)
        pts = [(PAD_L + i * step, y_of(v)) for i, v in enumerate(values)]

    fill = (f'<path d="{_path(pts)} L {pts[-1][0]:.1f},{PAD_T} '
            f'L {pts[0][0]:.1f},{PAD_T} Z" fill="var(--c-down)" opacity="0.14"/>')

    # When the drawdown is shallower than expected, `floor == expected` and the
    # bottom of the axis IS the expectation -- so they are labelled once. Two
    # labels at the same y collided and rendered as overlapping text.
    at_floor = abs(floor - expected) < 1e-9
    floor_label = (f"{floor * 100:.1f}% expected" if at_floor
                   else f"{floor * 100:.0f}%")

    expected_line = ""
    if not at_floor:
        expected_y = PAD_T + (expected / floor) * plot_h
        if PAD_T <= expected_y <= PAD_T + plot_h:
            expected_line = (
                f'<line x1="{PAD_L}" y1="{expected_y:.1f}" x2="{PAD_L + plot_w}" '
                f'y2="{expected_y:.1f}" stroke="var(--c-grid)" stroke-width="1" '
                f'stroke-dasharray="4 4"/>'
                f'<text x="{PAD_L + plot_w + 6}" y="{expected_y:.1f}" '
                f'class="c-axis" dominant-baseline="middle">expected</text>'
            )

    axis = (
        f'<line x1="{PAD_L}" y1="{PAD_T}" x2="{PAD_L + plot_w}" y2="{PAD_T}" '
        f'stroke="var(--c-grid)" stroke-width="1"/>'
        f'<text x="{PAD_L + plot_w + 6}" y="{PAD_T + 4}" class="c-axis">0%</text>'
        f'<text x="{PAD_L + plot_w + 6}" y="{PAD_T + plot_h:.0f}" class="c-axis">'
        f'{_e(floor_label)}</text>'
        f'<text x="{PAD_L}" y="{height - 5}" class="c-axis">{_e(series[0].label)}</text>'
        f'<text x="{PAD_L + plot_w}" y="{height - 5}" class="c-axis" '
        f'text-anchor="end">{_e(series[-1].label)}</text>'
    )

    return f"""<svg class="chart" viewBox="0 0 {WIDTH} {height}" width="100%"
     role="img" aria-label="Drawdown, worst {worst * 100:.2f} percent against an expected {expected * 100:.1f} percent">
  {fill}{expected_line}
  <path d="{_path(pts)}" fill="none" stroke="var(--c-down)" stroke-width="2"
        stroke-linejoin="round"/>
  {axis}
  {_hotspots(pts, series, lambda v: f"{v * 100:.2f}%", height)}
</svg>"""


# ------------------------------------------------------------ weight bar ---


def weight_bar(weight: float, *, target: float = 1 / 30) -> str:
    """A position's weight against the equal-weight target.

    Flagged past 1.5x, because an oversized position is usually an unexecuted
    sell -- the thing worth spotting in thirty near-identical rows. The tone is
    carried by a label too, never by colour alone.
    """
    ratio = (weight / target) if target else 0.0
    width = max(1.5, min(ratio / 2.0, 1.0) * 100)
    tone = "over" if ratio > 1.5 else ("under" if ratio < 0.5 else "at")
    return (f'<span class="wbar {tone}" role="img" '
            f'aria-label="{weight * 100:.1f}% of NAV, {tone} target">'
            f'<i style="width:{width:.1f}%"></i></span>')


def to_points(rows: list[dict], *, x: str, y: str) -> list[Point]:
    """Rows with a null `y` are skipped, not zeroed -- a missing benchmark is
    not a benchmark of nought."""
    out: list[Point] = []
    for row in rows:
        value = row.get(y)
        if value is None:
            continue
        if isinstance(value, Decimal):
            value = float(value)
        out.append(Point(label=str(row.get(x, "")), value=float(value)))
    return out
