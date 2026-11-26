"""A small engraved process map and the paper ledger's measured line chart."""

from functools import lru_cache
from html import escape
from math import cos, exp, sin

PAPER = "#f5f1e9"


def _surface(t, depth):
    # Oblique sections through three intersecting ridges, back to front.
    def peak(center, width):
        return exp(-((t - center) / width) ** 2)

    ridge = (
        65 * peak(.22 + .025 * sin(depth * 6), .095) * (.43 + .57 * sin(depth * 2.6))
        + 184 * peak(.55 + .07 * sin(depth * 4.8), .125) * (.20 + .80 * sin(depth * 2.8))
        + 95 * peak(.78 + .035 * cos(depth * 5), .15) * (.45 + .55 * cos(depth * 3))
    )
    ripple = (9 * sin(29 * t + depth * 5) + 4 * sin(57 * t - depth * 8)) * sin(t * 3.14)
    return (10 + t * 690 + depth * 124, 324 - t * 38 - depth * 72 - ridge - ripple)


@lru_cache(maxsize=1)
def process_landscape():
    lines = []
    highlighted = 42
    # Each section masks the linework behind it like a printed relief map.
    for index in range(69, -1, -1):
        depth = index / 69
        pts = [_surface(step / 144, depth) for step in range(145)]
        path = 'M' + ' L'.join(f'{x:.1f},{y:.1f}' for x, y in pts)
        color = "#ad482d" if index == highlighted else "#30594b"
        width = "1.7" if index == highlighted else ".72"
        lines.append(f'<path d="{path}" fill="{PAPER}" stroke="{color}" stroke-width="{width}"/>')
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 830 348" aria-hidden="true">'
            + ''.join(lines) + '</svg>').encode()


def process_map():
    links = []
    for label, href, t, depth, lift in [
        ("数据", "/research/atlas/evidence", .22, .15, 34),
        ("因子", "/research/atlas/factors", .55, .56, 46),
        ("验证", "/research/factor-suites", .83, .32, 32),
    ]:
        x, y = _surface(t, depth)
        links.append(f'<a href="{href}" class="atlas-pin" style="left:{x/8.3:.2f}%;bottom:{(348-y)/3.48:.2f}%;--pin:{lift}px">{label}</a>')
    return ('''<figure class="atlas-landscape"><div class="atlas-relief"><img src="/assets/research-relief.svg" width="830" height="348" alt=""/>
''' + ''.join(links) + '''</div><figcaption><span>研究流程示意</span><span class="atlas-ruler" aria-hidden="true"></span><span>由数据至验证</span></figcaption></figure>''')


def sparkline(values):
    if len(values) < 2:
        return '<span class="atlas-missing">无净值序列</span>'
    lo, hi = min(values), max(values)
    indices = chart_indices(len(values), 100)
    points = ' '.join(f'{i/(len(values)-1)*200:.1f},{23-(values[i]-lo)/(hi-lo)*20 if hi>lo else 12:.1f}' for i in indices)
    return (f'<svg class="atlas-spark" viewBox="0 0 200 26" preserveAspectRatio="none" aria-hidden="true">'
            f'<polyline points="{points}"/></svg><span class="atlas-extent">首末净值 {values[0]:.4f} 至 {values[-1]:.4f}</span>')


def chart_indices(size, limit):
    if size <= limit:
        return range(size)
    return [round(i * (size - 1) / (limit - 1)) for i in range(limit)]


def observation_labels(rows):
    dates = [row[0][:10] for row in rows]
    if len(dates) == len(set(dates)):
        return dates
    return [row[0].replace("T", " ").removesuffix("+00:00").removesuffix("Z") for row in rows]


def equity_chart(rows):
    """Each line is indexed to its own first mark on the same reported grid."""
    if not rows:
        return '<div class="atlas-chart-empty"><p>等待纸面账本</p><span>选择已发布的策略报告，或读取内置账本示例。</span></div>'
    values = [v for row in rows for v in row[1:] if v is not None]
    low, high = min(values), max(values)
    padding = max((high-low)*.08, .005)
    low, high = low - padding, high + padding
    width, height = 800, 244
    def y(value):
        return (high - value) / (high - low) * height
    grid, labels = [], []
    for i in range(5):
        position = i * height / 4
        grid.append(f'<path d="M0,{position:.2f}H{width}" class="atlas-gridline"/>')
        labels.append(f'<span>{high - i*(high-low)/4:.3f}</span>')
    lines = []
    for column, name in enumerate(("primary", "comparison", "market"), 1):
        if column >= len(rows[0]) or rows[0][column] is None:
            continue
        points = ' '.join(f'{i/max(1,len(rows)-1)*width:.2f},{y(rows[i][column]):.2f}' for i in chart_indices(len(rows), 1000))
        lines.append(f'<polyline class="atlas-line atlas-line--{name}" points="{points}"/>')
    labels_by_date = observation_labels(rows)
    dates = [labels_by_date[i] for i in chart_indices(len(rows), 4)]
    return (f'<div class="atlas-plot"><div class="atlas-scale">{"".join(labels)}</div>'
            f'<svg viewBox="0 0 {width} {height}" preserveAspectRatio="none" role="img" aria-label="纸面账本归一化净值，逐期数据见下表">{"".join(grid + lines)}'
            '<line data-atlas-cursor hidden x1="0" x2="0" y1="0" y2="244"/></svg>'
            f'<div class="atlas-dates">{"".join(f"<span>{escape(d)}</span>" for d in dates)}</div></div>')
