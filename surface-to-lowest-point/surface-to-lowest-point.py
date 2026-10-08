#!/usr/bin/env python3
"""Face uneven stock down to its lowest point on the Makera Z1, from probe-stock's reference.

The Z1 firmware (Smoothieware fork, 1.1.2) has no #variables, expressions or
loops: it reads the number straight after each letter, so Z[#<depth>] is Z0.
A job can't measure the stock and then decide how deep to cut, so the
measuring is its own job: probe-stock (../probe-stock) probes the top and
saves the heights. This script reads them, rounds the lowest point's
thickness down to round_to, and writes surface-to-lowest-point.nc, which
asks for the cutter (T1) and faces down in passes until the stock is that
thick. It doesn't probe: Z0 stays where it is, and every Z is worked out
from where Studio's log last showed it.

Each pass only covers the part of the top that reaches it; the last covers
everything. Once Studio's log shows the job finished, probe-stock counts the
top as faced, for the next macro.

Edit VARIABLES below (or pass flags, see --help), upload the .nc to the Z1
from Makera Studio and start it with Auto leveling OFF (it bends the cut).
"""

import bisect
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "probe-stock")]
from shared import cli, z1  # noqa: E402
from shared.z1 import fmt  # noqa: E402
import stockref  # noqa: E402

# --- VARIABLES (dimensions are POSITIVE values; the stock's come from probe-stock) ---
VARIABLES = {
    "final_height": 0.0,    # Thickness to leave; 0 = the lowest probed point, rounded down to round_to
    "round_to": 0.5,        # Round the lowest point's thickness down to a multiple of this
    "tool_dia": 3.175,      # Diameter of your facing bit (the included collet is 1/8")
    "stepover": 2.0,        # Max stepover (2.0 = 63% of 3.175, Makera's 6061 value)
    "cut_along": "y",       # Passes run front to back (y; the Z1 is stiffer in Y) or left to right (x)
    "air_margin": 0.3,      # A pass cuts wherever the probed top is within this of it (the top between probe points)
    "pass_depth": 0.2,      # Depth of cut per pass
    "finish_depth": 0.1,    # The last pass is a lighter finishing pass this deep over the whole top; 0 = none
    "finish_stepover": 0.0, # Stepover of the finishing pass; 0 = half the cutter's diameter
    "top_margin": 0.2,      # Start cutting this far above the highest point, for high spots between probe points
    "rpm": 12000,           # Makera library, 3.175*12mm Flat End (Metal) in 6061
    "feed": 500,            # mm/min cutting feed
    "plunge_feed": 200,     # mm/min plunge feed (plunges happen off the stock)
}

LIFT = 0.5            # Between passes, lift this far above the floor just cut to move to the next start
SAMPLE = 0.25         # Spacing of the checks for material along each row

FACE_JOB = "surface-to-lowest-point.nc"


def parse_args():
    p = cli.parser(__doc__)
    cli.add_variables(p, VARIABLES, {"cut_along": ("x", "y")})
    p.add_argument("--full-passes", action="store_true", help="every pass covers the whole top, even where it only cuts air")
    cli.add_log(p)
    cli.add_tool(p)
    p.add_argument("-o", "--out", type=Path)
    return p.parse_args()


def check(v):
    errors = cli.positive(v, ("round_to", "tool_dia", "stepover", "pass_depth"))
    if v["final_height"] < 0:
        errors.append("final_height can't be negative (0 = from the probe run)")
    if v["stepover"] >= v["tool_dia"]:
        errors.append("stepover must be smaller than tool_dia or ridges are left between passes")
    if v["top_margin"] < 0 or v["air_margin"] < 0:
        errors.append("top_margin and air_margin can't be negative")
    if not 0 <= v["finish_depth"] <= v["pass_depth"]:
        errors.append("finish_depth must be between 0 and pass_depth")
    if not 0 <= v["finish_stepover"] < v["tool_dia"]:
        errors.append("finish_stepover must be between 0 and tool_dia")
    cli.stop("surface-to-lowest-point", errors + cli.feeds_and_rpm(v))


def plan(v, state):
    """The stock as it is now -> (table rows, thickest, lowest thickness, thickness to leave, shift).

    Thicknesses are heights above the bed, which probe-stock measured from the
    anchor plate (or assumed). shift is how far the highest point is above Z0
    now: every Z in the job is raised by it.
    """
    high = max(h for _, _, h in state.top)
    rows = [(*state.program(u, t), h - high, h - state.bed) for u, t, h in state.top]
    thickest, lowest = high - state.bed, min(r[3] for r in rows)
    final = math.floor(lowest / v["round_to"] + 1e-9) * v["round_to"]
    if final <= 0:
        raise SystemExit("surface-to-lowest-point: that leaves nothing; check the probe run (probe-stock.py show)")
    return rows, thickest, lowest, final, high - state.z0


def finishing(v):
    """Whether the last pass is a finishing pass: there's a finish depth (to 0.001) and more to cut than it."""
    return 0 < round(v["finish_depth"], 3) < v["top_margin"] + v["stock_height"] - v["final_height"]


def levels(v):
    """Pass depths from the highest point, down to the final one; with finishing, roughing stops finish_depth above it.

    Rounded to 0.001 as the G-code writes them, so no two are written the same.
    """
    top, target = round(v["top_margin"], 3), round(v["final_height"] - v["stock_height"], 3)
    rough = round(target + round(v["finish_depth"], 3), 3) if finishing(v) else target
    count = math.ceil((top - rough) / v["pass_depth"] - 1e-9)
    zs = [round(top - k * v["pass_depth"], 3) for k in range(1, count)] + [rough] + ([target] if finishing(v) else [])
    return [z for z, below in zip(zs, zs[1:] + [None]) if z != below]


def interp(xs, hs, x):
    """Straight lines through (xs, hs). Past either end, the higher of the end value and the last line carried on."""
    if len(xs) == 1:
        return hs[0]
    if xs[0] < x < xs[-1]:
        i = bisect.bisect_right(xs, x) - 1
        return hs[i] + (hs[i + 1] - hs[i]) * (x - xs[i]) / (xs[i + 1] - xs[i])
    i = 0 if x <= xs[0] else len(xs) - 2
    line = hs[i] + (hs[i + 1] - hs[i]) * (x - xs[i]) / (xs[i + 1] - xs[i])
    return max(hs[0] if x <= xs[0] else hs[-1], line)


class Top:
    """The stock's top in the facing job's Z, from the probe points (stock coordinates).

    Bilinear between the points. Past the outer ones it never guesses low: the higher
    of the edge value and the slope carried on.
    """

    def __init__(self, points):
        self.us = sorted({round(u, 6) for u, _, _ in points})
        self.ts = sorted({round(t, 6) for _, t, _ in points})
        h = {(round(u, 6), round(t, 6)): s for u, t, s in points}
        self.grid = [[h[(u, t)] for u in self.us] for t in self.ts]

    def at(self, u, t):
        return interp(self.ts, [interp(self.us, row, u) for row in self.grid], t)


def frame(v):
    """Passes run along a and step across b: ((lo, hi) along, (lo, hi) across, (a, b) -> stock (u, t))."""
    if v["cut_along"] == "y":
        return v["y_range"], v["x_range"], lambda a, b: (b, a)
    return v["x_range"], v["y_range"], lambda a, b: (a, b)


def ends(v):
    """Where a row starts and ends along a: the cutter clear of the stock by LEAD."""
    (lo, hi), _, _ = frame(v)
    r = v["tool_dia"] / 2
    return lo - (r + z1.LEAD), hi + r + z1.LEAD


def rows_across(v, stepover=None):
    """Where the passes run across the stock: both edges and evenly between, no more than stepover apart."""
    _, (lo, hi), _ = frame(v)
    n = math.ceil((hi - lo) / (stepover or v["stepover"]) - 1e-9)
    return [lo + (hi - lo) * i / n for i in range(n + 1)], (hi - lo) / n


def material(v, top, bs):
    """For every row, the highest the top reaches under the cutter, sampled along the row."""
    (a_lo, a_hi), (b_lo, b_hi), stock = frame(v)
    r = v["tool_dia"] / 2
    cross = top.us if v["cut_along"] == "y" else top.ts  # the top is straight between these, across a row
    n = math.ceil((a_hi - a_lo) / SAMPLE)
    samples = [a_lo + (a_hi - a_lo) * i / n for i in range(n + 1)]
    profile = []
    for b in bs:
        lo, hi = max(b_lo, b - r), min(b_hi, b + r)
        across = [lo, hi] + [c for c in cross if lo < c < hi]
        profile.append([max(top.at(*stock(a, c)) for c in across) for a in samples])
    return samples, profile


def spans(v, samples, profile, z):
    """For one pass at z: per row, the stretch (lo, hi) along it the cutter has to cover, or None.

    A row is cut wherever the top under the cutter is within air_margin of the pass,
    plus the cutter's radius and LEAD at both ends, so it starts and ends clear.
    """
    lo, hi = ends(v)
    r = v["tool_dia"] / 2
    out = []
    for row in profile:
        hit = [a for a, top in zip(samples, row) if top + v["air_margin"] > z]
        out.append((max(lo, hit[0] - r - z1.LEAD), min(hi, hit[-1] + r + z1.LEAD)) if hit else None)
    return out


def zigzag(rows, up):
    """One continuous pass over rows [(b, (lo, hi))], (a, b) points, first row towards +a if up.

    Each step across to the next row happens at an end both rows are clear at, and
    rows in between with nothing to cut are crossed there too, so the cutter never
    lifts and only ever meets material sideways.
    """
    pts = []
    for k, (b, (lo, hi)) in enumerate(rows):
        going_up = up if k % 2 == 0 else not up
        prev = rows[k - 1][1] if k else None
        nxt = rows[k + 1][1] if k + 1 < len(rows) else None
        if going_up:
            pts += [(min(lo, prev[0]) if prev else lo, b), (max(hi, nxt[1]) if nxt else hi, b)]
        else:
            pts += [(max(hi, prev[1]) if prev else hi, b), (min(lo, nxt[0]) if nxt else lo, b)]
    return pts


def layer_path(v, bs, row_spans, near):
    """A pass that goes down beyond the stock's edge and leads in sideways.

    Of the four ways to run the zig-zag, the one whose first row has the least clear
    stretch from the edge to its material, then the one starting nearest `near`.
    """
    edge_lo, edge_hi = ends(v)
    rows = [(b, s) for b, s in zip(bs, row_spans) if s]
    best = None
    for order in (rows, rows[::-1]):
        for up in (True, False):
            pts = zigzag(order, up)
            a0, b0 = pts[0]
            lead = a0 - edge_lo if up else edge_hi - a0
            pts[0] = (edge_lo if up else edge_hi, b0)  # go down off the stock, then feed in
            travel = math.hypot(pts[0][0] - near[0], pts[0][1] - near[1]) if near else 0.0
            if best is None or (lead, travel) < best[0]:
                best = ((lead, travel), pts)
    return best[1]


def build(v, note, z0, shift, top=None, flute_length=z1.FLUTE_LENGTH):
    """The facing job. It doesn't probe: Z0 stays where it is (G54 Z z0, from the log) and
    the highest point is shift above it, so every Z is raised by shift. Only the cutter
    goes in, measured against the probe at the tool change.

    With a top (from the probe run), each pass but the last only covers the rows and
    stretches that still have material above it; without one (--full-passes) every
    pass covers the whole top. With finishing, the last roughing pass covers the
    whole top too, so the finishing pass after it takes the same finish_depth
    everywhere, on rows finish_stepover apart.
    """
    _, _, stock = frame(v)
    bs, step = rows_across(v)
    full_rows = [(b, ends(v)) for b in bs]
    full = zigzag(full_rows, True)
    zs = [z + shift for z in levels(v)]
    if top:
        samples, profile = material(v, top, bs)
    fin_bs, fin_step = rows_across(v, v["finish_stepover"] or v["tool_dia"] / 2)
    fin_rows = [(b, ends(v)) for b in fin_bs]

    passes, near = [], None  # (level, z, (a, b) points)
    for n, z in enumerate(zs, 1):
        if finishing(v) and n == len(zs):
            path = layer_path(v, fin_bs, [s for _, s in fin_rows], near)
        elif not top:
            path = full if n % 2 else full[::-1]  # serpentine: each level starts where the last ended
        elif n >= len(zs) - finishing(v):
            path = layer_path(v, bs, [s for _, s in full_rows], near)
        else:
            row_spans = spans(v, samples, profile, z)
            if not any(row_spans):
                continue
            path = layer_path(v, bs, row_spans, near)
        passes.append((n, z, z1.to_program(v["origin"], v["stock_width"], v["stock_length"], [stock(a, b) for a, b in path])))
        near = path[-1]

    safe_z = z1.SAFE_Z + shift
    seconds = 0.0
    x, y = passes[0][2][0]
    approach_z = v["top_margin"] + z1.APPROACH + shift
    body = z1.spindle_on(x, y, v["rpm"], safe_z, approach_z)
    z_prev, at, floor = approach_z, (x, y), None
    for k, (n, z, pts) in enumerate(passes, 1):
        if pts[0] != at:  # lift off the floor just cut and move to this pass's start, beyond the stock's edge
            (x0, y0), (x, y) = at, pts[0]
            body += [f"G0 Z{fmt(floor + LIFT)}", f"G0 X{fmt(x)} Y{fmt(y)}"]
            seconds += (floor + LIFT - z_prev + math.hypot(x - x0, y - y0)) / z1.MAX_FEED * 60
            z_prev = floor + LIFT
        body.append(f"; Level {k}/{len(passes)} Z{fmt(z)}" + (", finishing" if finishing(v) and n == len(zs) else ""))
        lines, seconds = z1.cut(z, z_prev, pts, v["plunge_feed"], v["feed"], seconds)
        body += lines
        z_prev, at, floor = z, pts[-1], z
    body += z1.end(safe_z)

    w, l, h, d = v["stock_width"], v["stock_length"], v["stock_height"], v["tool_dia"]
    wide, deep = (hi - lo for lo, hi in (v["x_range"], v["y_range"]))
    header = z1.start("surface-to-lowest-point", (w, l, h), v["origin"], d, seconds, [
        f"; Stock {fmt(w)} x {fmt(l)} mm, {fmt(h)} mm thick at the highest point, origin {v['origin']}.",
        *([f"; Passes cover {fmt(wide)} x {fmt(deep)} mm, out to the sides the probe rod touched."] if (wide, deep) != (w, l) else []),
        f"; {note}",
        f"; No probing: Z0 stays where it is, G54 Z {z0:.3f}; the highest point is {fmt(shift)} mm above it.",
        f"; Turn auto-leveling OFF. Cuts Z{fmt(passes[0][1])} -> Z{fmt(zs[-1])} in {len(passes)} passes"
        f" of <= {fmt(v['pass_depth'])} mm along {v['cut_along'].upper()}, {fmt(step)} mm stepover, {fmt(d)} mm tool.",
        *([f"; The last is a finishing pass, {fmt(v['finish_depth'])} mm deep over the whole top at {fmt(fin_step)} mm stepover."]
          if finishing(v) else []),
        "; " + (
            f"Each pass covers only where the probed top is within {fmt(v['air_margin'])} mm of it; the last covers"
            " the whole top. Every pass starts beyond the stock's edge." if top else "Every pass covers the whole top."
        ),
    ], g54_z=z0, flute_length=flute_length)
    return "\n".join(header + body) + "\n", len(passes), seconds


def main():
    args = parse_args()
    v = {name: getattr(args, name) for name in VARIABLES}
    flute_length = cli.take_tool(v, args)
    check(v)

    state = stockref.load(args.log)
    state.check("surface-to-lowest-point")
    v.update(stock_width=state.width, stock_length=state.length, origin=state.origin)
    # Passes cover the stock out to the sides the rod touched, where they're past the sizes typed for the probe job
    xs = [0.0, state.width, *(u for _, _, u in state.faces.get("x", []))]
    ys = [0.0, state.length, *(t for _, _, t in state.faces.get("y", []))]
    v.update(x_range=(min(xs), max(xs)), y_range=(min(ys), max(ys)))
    rows, thickest, lowest, final, shift = plan(v, state)
    high = max(rows, key=lambda r: r[2])
    low = min(rows, key=lambda r: r[3])
    print(f"Probe run {state.probe['stamp']} ({state.probe['log']}), {len(rows)} points:")
    print(f"  {'X':>7} {'Y':>7} {'height':>8} {'thickness':>10}")
    for x, y, height, thick in rows:
        mark = "  highest" if (x, y) == high[:2] else "  lowest" if (x, y) == low[:2] else ""
        print(f"  {x:7.1f} {y:7.1f} {height:8.3f} {thick:10.3f}{mark}")
    print(f"Thicknesses are from {state.bed_from}.")
    wide, deep = (hi - lo for lo, hi in (v["x_range"], v["y_range"]))
    if (wide, deep) != (state.width, state.length):
        print(f"The sides touched with the rod are past {fmt(state.width)} x {fmt(state.length)} mm:"
              f" passes cover {fmt(wide)} x {fmt(deep)} mm.")
    for stamp, text in state.history:
        print(f"Since then: {stamp} {text}")
    for note in state.notes:
        print(f"note: {note}")

    given = v["final_height"]
    v["stock_height"] = round(thickest, 3)
    if given > 0:
        if given >= v["stock_height"]:
            raise SystemExit(f"surface-to-lowest-point: --final-height must be less than {thickest:.2f} mm, the highest point")
        print(f"Leaving {fmt(given)} mm, given with --final-height:", end="")
        note = f"Probe run {state.probe['stamp']}: highest point {thickest:.2f} mm thick, leaving {fmt(given)} mm (--final-height)."
    else:
        if final >= thickest - 1e-6:
            raise SystemExit(
                f"surface-to-lowest-point: the top is already {thickest:.3f} mm thick everywhere it was probed, so"
                f" rounding down to {fmt(v['round_to'])} mm leaves nothing to cut. Pass --final-height to go thinner"
            )
        v["final_height"] = final
        print(f"Lowest point {lowest:.2f} mm, rounded down to {fmt(final)} mm:", end="")
        note = f"Probe run {state.probe['stamp']}: lowest point {lowest:.2f} mm thick, leaving {fmt(final)} mm."
    print(f" cutting {fmt(v['stock_height'] - v['final_height'])} mm below the highest point.")
    z0 = state.machine.z
    print(f"Z0 is G54 Z {z0:.3f} (printed {state.machine.stamp}): the highest point is {shift:.3f} mm above it.\n")

    top = None if args.full_passes else stockref_top(state)
    program, passes, seconds = build(v, note, z0, shift, top, flute_length)
    out = args.out or HERE / FACE_JOB
    out.write_text(program)
    stockref.record(out, "cut", f"top faced flat, {fmt(v['final_height'])} mm thick",
                    effect={"type": "top", "h": round(state.bed + v["final_height"], 3)})
    saved = "" if args.full_passes else f", full-width passes would take ~{build(v, note, z0, shift)[2] / 60:.0f}"
    print(
        f"wrote {out} ({passes} passes along {v['cut_along'].upper()}, ~{seconds / 60:.0f} min{saved},"
        f" {fmt(v['stock_height'])} -> {fmt(v['final_height'])} mm thick)"
    )
    along, across = ("front and back", "left and right") if v["cut_along"] == "y" else ("left and right", "front and back")
    print(f"clamps and vise jaws must sit below {fmt(v['final_height'])} mm within {fmt(v['tool_dia'] + z1.LEAD)} mm of the"
          f" {along} edges and {fmt(v['tool_dia'] / 2)} mm of the {across}: the cutter runs that far past them")
    print("upload it and start it with Auto leveling off, without changing Z0 or running anything else first,")
    print("and with the probe or no tool in: the tool change is the only stop, and with T1 already in it doesn't stop.")
    print(f"It doesn't probe. Before you confirm the tool change, Studio's log should show G54 Z {z0:.3f}: stop the job if not")


def stockref_top(state):
    """The top in the facing job's Z (from Z0 as it is now), stock coordinates."""
    return Top([(u, t, h - state.z0) for u, t, h in state.top])


if __name__ == "__main__":
    main()
