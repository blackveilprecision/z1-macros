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

import argparse
import bisect
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "probe-stock"))
import stockref  # noqa: E402
from stockref import ORIGINS  # noqa: E402

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

SAFE_Z = 15.0         # Retract height, same as Studio's exports
APPROACH = 3.0        # Rapid down to this far above Z0 before plunging
LEAD = 2.0            # Tool edge clearance past the X edges of the stock
FLUTE_LENGTH = 12     # Header only, shown in Studio's tool list
MAX_FEED = 1200       # Z1 limits from Studio's machine table
MAX_RPM = 13000
LIFT = 0.5            # Between passes, lift this far above the floor just cut to move to the next start
SAMPLE = 0.25         # Spacing of the checks for material along each row

FACE_JOB = "surface-to-lowest-point.nc"


def fmt(value):
    s = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for name, default in VARIABLES.items():
        flag = "--" + name.replace("_", "-")
        if name == "cut_along":
            p.add_argument(flag, default=default, choices=("x", "y"))
        else:
            p.add_argument(flag, type=type(default), default=default)
    p.add_argument("--full-passes", action="store_true", help="every pass covers the whole top, even where it only cuts air")
    p.add_argument("--log", type=Path, default=stockref.STUDIO_LOGS, help="Studio's log folder or one log file (default: %(default)s)")
    p.add_argument("--tool", help="take tool_dia, rpm, feeds, pass_depth and stepover from this tool in"
                   " ../tool-library (see tool-library/toollib.py list); options you pass still win")
    p.add_argument("--material", default="Aluminum", help="which of the tool's presets to use (default: Aluminum)")
    p.add_argument("-o", "--out", type=Path)
    return p.parse_args()


def check(v):
    errors = []
    for name in ("round_to", "tool_dia", "stepover", "pass_depth"):
        if v[name] <= 0:
            errors.append(f"{name} must be positive")
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
    if not 0 < v["feed"] <= MAX_FEED or not 0 < v["plunge_feed"] <= MAX_FEED:
        errors.append(f"feeds must be between 0 and {MAX_FEED} mm/min")
    if not 0 < v["rpm"] <= MAX_RPM:
        errors.append(f"rpm must be between 0 and {MAX_RPM}")
    if errors:
        raise SystemExit("surface-to-lowest-point: " + "; ".join(errors))


def to_program(v, points):
    """Stock coordinates (from the front-left corner) -> program coordinates for the chosen origin."""
    ox, oy = ORIGINS[v["origin"]]
    return [(u - ox * v["stock_width"], t - oy * v["stock_length"]) for u, t in points]


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
    """Whether the last pass is a finishing pass: there's a finish depth and more to cut than it."""
    return 0 < v["finish_depth"] < v["top_margin"] + v["stock_height"] - v["final_height"]


def levels(v):
    """Pass depths from the highest point, down to the final one; with finishing, roughing stops finish_depth above it."""
    top, target = v["top_margin"], v["final_height"] - v["stock_height"]
    rough = target + v["finish_depth"] if finishing(v) else target
    count = math.ceil((top - rough) / v["pass_depth"] - 1e-9)
    return [top - k * v["pass_depth"] for k in range(1, count)] + [rough] + ([target] if finishing(v) else [])


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
    """Passes run along a and step across b: (length along, width across, (a, b) -> stock (u, t))."""
    if v["cut_along"] == "y":
        return v["stock_length"], v["stock_width"], lambda a, b: (b, a)
    return v["stock_width"], v["stock_length"], lambda a, b: (a, b)


def rows_across(v, stepover=None):
    """Where the passes run across the stock: both edges and evenly between, no more than stepover apart."""
    _, width, _ = frame(v)
    n = math.ceil(width / (stepover or v["stepover"]) - 1e-9)
    return [width * i / n for i in range(n + 1)], width / n


def material(v, top, bs):
    """For every row, the highest the top reaches under the cutter, sampled along the row."""
    length, width, stock = frame(v)
    r = v["tool_dia"] / 2
    cross = top.us if v["cut_along"] == "y" else top.ts  # the top is straight between these, across a row
    n = math.ceil(length / SAMPLE)
    samples = [length * i / n for i in range(n + 1)]
    profile = []
    for b in bs:
        lo, hi = max(0.0, b - r), min(width, b + r)
        across = [lo, hi] + [c for c in cross if lo < c < hi]
        profile.append([max(top.at(*stock(a, c)) for c in across) for a in samples])
    return samples, profile


def spans(v, samples, profile, z):
    """For one pass at z: per row, the stretch (lo, hi) along it the cutter has to cover, or None.

    A row is cut wherever the top under the cutter is within air_margin of the pass,
    plus the cutter's radius and LEAD at both ends, so it starts and ends clear.
    """
    length, _, _ = frame(v)
    r = v["tool_dia"] / 2
    out = []
    for row in profile:
        hit = [a for a, top in zip(samples, row) if top + v["air_margin"] > z]
        out.append((max(-(r + LEAD), hit[0] - r - LEAD), min(length + r + LEAD, hit[-1] + r + LEAD)) if hit else None)
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
    length, _, _ = frame(v)
    edge_lo, edge_hi = -(v["tool_dia"] / 2 + LEAD), length + v["tool_dia"] / 2 + LEAD
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


def build(v, note, z0, shift, top=None):
    """The facing job. It doesn't probe: Z0 stays where it is (G54 Z z0, from the log) and
    the highest point is shift above it, so every Z is raised by shift. Only the cutter
    goes in, measured against the probe at the tool change.

    With a top (from the probe run), each pass but the last only covers the rows and
    stretches that still have material above it; without one (--full-passes) every
    pass covers the whole top. With finishing, the last roughing pass covers the
    whole top too, so the finishing pass after it takes the same finish_depth
    everywhere, on rows finish_stepover apart.
    """
    length, width, stock = frame(v)
    r = v["tool_dia"] / 2
    bs, step = rows_across(v)
    full_rows = [(b, (-(r + LEAD), length + r + LEAD)) for b in bs]
    full = zigzag(full_rows, True)
    zs = [z + shift for z in levels(v)]
    if top:
        samples, profile = material(v, top, bs)
    fin_bs, fin_step = rows_across(v, v["finish_stepover"] or v["tool_dia"] / 2)
    fin_rows = [(b, (-(r + LEAD), length + r + LEAD)) for b in fin_bs]

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
        passes.append((n, z, to_program(v, [stock(a, b) for a, b in path])))
        near = path[-1]

    safe_z = SAFE_Z + shift
    seconds = 0.0
    body = []
    x, y = passes[0][2][0]
    approach_z = v["top_margin"] + APPROACH + shift
    body += [f"G0 X{fmt(x)} Y{fmt(y)}", f"S{v['rpm']} M3", f"G0 Z{fmt(safe_z)}", f"G0 Z{fmt(approach_z)}"]
    z_prev, at, floor = approach_z, (x, y), None
    for n, z, pts in passes:
        if pts[0] != at:  # lift off the floor just cut and move to this pass's start, beyond the stock's edge
            (x0, y0), (x, y) = at, pts[0]
            body += [f"G0 Z{fmt(floor + LIFT)}", f"G0 X{fmt(x)} Y{fmt(y)}"]
            seconds += (floor + LIFT - z_prev + math.hypot(x - x0, y - y0)) / MAX_FEED * 60
            z_prev = floor + LIFT
        body.append(f"; Level {n}/{len(zs)} Z{fmt(z)}" + (", finishing" if finishing(v) and n == len(zs) else ""))
        body.append(f"G1 Z{fmt(z)} F{v['plunge_feed']}")
        seconds += abs(z_prev - z) / v["plunge_feed"] * 60
        feed = f" F{v['feed']}"
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            words = (f"X{fmt(x1)}" if x1 != x0 else "") + (f" Y{fmt(y1)}" if y1 != y0 else "")
            body.append(f"G1 {words.strip()}{feed}")
            seconds += math.hypot(x1 - x0, y1 - y0) / v["feed"] * 60
            feed = ""
        z_prev, at, floor = z, pts[-1], z
    body += [f"G0 Z{fmt(safe_z)}", "M9", "M05", "G28", "M02"]

    w, l, h, d = v["stock_width"], v["stock_length"], v["stock_height"], v["tool_dia"]
    ox, oy = ORIGINS[v["origin"]]
    header = [
        ";@MKR|BEGIN",
        ";@MKR|SCHEMA|v=1.0.0",
        ";@MKR|MACHINE|id=Z1|name=Makera Z1",
        ";@MKR|MATERIAL|id=|name3=other|name1=Aluminum Alloys|name2=6061 Aluminum|uuid1=019bfadc-e599-74ef-b4ba-f34413e6f231",
        f";@MKR|STOCK|id=cuboid|length={fmt(w)}|width={fmt(l)}|height={fmt(h)}|diameter=50",
        f";@MKR|ORIGIN|id=0|type_name={v['origin']}|x={fmt((ox - 0.5) * w)}|y={fmt((oy - 0.5) * l)}|z={fmt(h / 2)}",
        ";@MKR|CAM|id=surface-to-lowest-point|name=surface-to-lowest-point.py|v=1.0.0",
        ";@MKR|UNIT|value=mm",
        f";@MKR|MAXFEEDRATE|value={MAX_FEED}",
        f";@MKR|TOOL|number=1|id=|name={fmt(d)}mm Flat End|type=Flat End|handlediameter={fmt(d)}|sticklength=0"
        f"|shoulderlength={FLUTE_LENGTH}|flutelength={FLUTE_LENGTH}|diameter={fmt(d)}|tipdiameter={fmt(d)}"
        "|cornerradius=0|angle=0|halfAngle=0",
        f";@MKR|TIME|seconds={round(seconds)}",
        ";@MKR|TOOLPATH|number=1|tool_number=1|name=[T1]Surface To Lowest Point",
        ";@MKR|END",
        "",
        "; Generated by surface-to-lowest-point.py (github.com/blackveilprecision/z1-macros) - edit the script, not this file.",
        f"; Stock {fmt(w)} x {fmt(l)} mm, {fmt(h)} mm thick at the highest point, origin {v['origin']}.",
        f"; {note}",
        f"; No probing: Z0 stays where it is, G54 Z {z0:.3f}; the highest point is {fmt(shift)} mm above it.",
        f"; Turn auto-leveling OFF. Cuts Z{fmt(v['top_margin'] + shift)} -> Z{fmt(zs[-1])} in {len(zs)} passes"
        f" of <= {fmt(v['pass_depth'])} mm along {v['cut_along'].upper()}, {fmt(step)} mm stepover, {fmt(d)} mm tool.",
        *([f"; The last is a finishing pass, {fmt(v['finish_depth'])} mm deep over the whole top at {fmt(fin_step)} mm stepover."]
          if finishing(v) else []),
        "; " + (
            f"Each pass covers only where the probed top is within {fmt(v['air_margin'])} mm of it; the last covers"
            " the whole top. Every pass starts beyond the stock's edge." if top else "Every pass covers the whole top."
        ),
        "",
        "G90 G21",
        ";@MKR|TOOLPATH_START|toolpath_number=1",
        "",
        "M370 ; clear any auto-leveling grid left from an earlier job",
        f"M498 ; G54 Z should read {z0:.3f}: stop the job if not",
        "",
        f"; T1-{fmt(d)}mm Flat End",
        "",
        "T1 M6",
        "M7",
    ]
    return "\n".join(header + body) + "\n", len(passes), seconds


def main():
    args = parse_args()
    v = {name: getattr(args, name) for name in VARIABLES}
    if args.tool:  # the tool library is only needed with --tool, so this script also runs without it
        sys.path.insert(0, str(HERE.parent / "tool-library"))
        import toollib

        tool, preset = toollib.apply(v, args.tool, args.material)
        print(
            f"tool: {tool.description} ({tool.source}), {preset.name}: {fmt(v['tool_dia'])} mm, {v['rpm']} rpm,"
            f" {v['feed']} mm/min, plunge {v['plunge_feed']}, {fmt(v['pass_depth'])} mm passes, {fmt(v['stepover'])} mm stepover"
        )
    check(v)

    state = stockref.load(args.log)
    state.check("surface-to-lowest-point")
    v.update(stock_width=state.width, stock_length=state.length, origin=state.origin)
    rows, thickest, lowest, final, shift = plan(v, state)
    high = max(rows, key=lambda r: r[2])
    low = min(rows, key=lambda r: r[3])
    print(f"Probe run {state.probe['stamp']} ({state.probe['log']}), {len(rows)} points:")
    print(f"  {'X':>7} {'Y':>7} {'height':>8} {'thickness':>10}")
    for x, y, height, thick in rows:
        mark = "  highest" if (x, y) == high[:2] else "  lowest" if (x, y) == low[:2] else ""
        print(f"  {x:7.1f} {y:7.1f} {height:8.3f} {thick:10.3f}{mark}")
    print(f"Thicknesses are from {state.bed_from}.")
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
    program, passes, seconds = build(v, note, z0, shift, top)
    out = args.out or HERE / FACE_JOB
    out.write_text(program)
    stockref.record(out, "cut", f"top faced flat, {fmt(v['final_height'])} mm thick",
                    effect={"type": "top", "h": round(state.bed + v["final_height"], 3)})
    saved = "" if args.full_passes else f", full-width passes would take ~{build(v, note, z0, shift)[2] / 60:.0f}"
    print(
        f"wrote {out} ({passes} passes along {v['cut_along'].upper()}, ~{seconds / 60:.0f} min{saved},"
        f" {fmt(v['stock_height'])} -> {fmt(v['final_height'])} mm thick)"
    )
    print(f"clamps and vise jaws must sit below {fmt(v['final_height'])} mm: the cutter runs past the stock edges")
    print("upload it and start it with Auto leveling off, without changing Z0 or running anything else first;")
    print(f"it doesn't probe. Before the cutter goes in, Studio's log should show G54 Z {z0:.3f}: stop the job if not")


def stockref_top(state):
    """The top in the facing job's Z (from Z0 as it is now), stock coordinates."""
    return Top([(u, t, h - state.z0) for u, t, h in state.top])


if __name__ == "__main__":
    main()
