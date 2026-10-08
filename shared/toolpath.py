"""Toolpath math the macros share: pass depths, probe grids and the facing raster.

Points are in stock coordinates, from the front-left corner; z1.to_program() turns them into the job's.
"""

import math

from .z1 import LEAD, ORIGINS, fmt


def equal_levels(top, bottom, step):
    """Equal passes from top down to bottom, none deeper than step.

    Equal passes avoid a thin last pass that rubs instead of cutting. top and bottom
    are rounded to 0.001 first, as fmt() writes them, so -3.0004 isn't one more pass at Z-3.
    """
    top, bottom = round(top, 3), round(bottom, 3)
    n = math.ceil((top - bottom) / step - 1e-9)
    return [top - (top - bottom) * k / n for k in range(1, n + 1)]


def spread(size, count, inset):
    """count positions across size, inset from both ends; the middle when there's one or no room."""
    if count == 1 or size <= 2 * inset:
        return [size / 2]
    return [inset + (size - 2 * inset) * i / (count - 1) for i in range(count)]


def probe_grid(origin, width, length, nx, ny, inset):
    """Points on the top, serpentine from the corner nearest the origin."""
    ox, oy = ORIGINS[origin]
    us, ts = spread(width, nx, inset), spread(length, ny, inset)
    us, ts = (us[::-1] if ox > 0.5 else us), (ts[::-1] if oy > 0.5 else ts)
    return [(u, t) for j, t in enumerate(ts) for u in (us if j % 2 == 0 else us[::-1])]


def raster(width, length, tool_dia, stepover):
    """Zig-zag along X, stepping in Y: (points, the stepover used)."""
    w, l, r = width, length, tool_dia / 2
    x_lo, x_hi = -(r + LEAD), w + r + LEAD  # tool fully off the stock at both ends
    rows = math.ceil(l / stepover - 1e-9)
    points = []
    for i in range(rows + 1):
        y = l * i / rows  # tool centre runs on both Y edges, so the whole face is covered
        xs = (x_lo, x_hi) if i % 2 == 0 else (x_hi, x_lo)
        points += [(x, y) for x in xs]
    return points, l / rows


def reach(tool_dia):
    """How far raster()'s cutter reaches past the X and Y edges, for the clamps: it clears the stock by LEAD at
    each end of a row, so its far side is a diameter plus LEAD out, and its centre runs on the Y edges."""
    return f"the cutter reaches {fmt(tool_dia + LEAD)} mm past the X edges and {fmt(tool_dia / 2)} mm past the Y edges"
