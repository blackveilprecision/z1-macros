#!/usr/bin/env python3
"""Face uneven stock down to its lowest point on the Makera Z1, in two jobs.

The Z1 firmware (Smoothieware fork, 1.1.2) has no #variables, expressions or
loops: it reads the number straight after each letter, so Z[#<depth>] is Z0.
A job can't measure the stock and then decide how deep to cut, so that math
happens here, between two jobs:

1. --probe writes surface-to-lowest-point-probe.nc. It probes a grid on the
   stock with the 3D probe (T0), sets Z0 on each point and prints it to
   Studio's log with M498. No spindle, no cutting.
2. Run the script again without --probe. It reads that run back from Studio's
   log, rounds the lowest point's thickness down to round_to, and writes
   surface-to-lowest-point.nc. That job doesn't probe: Z0 stays where the probe
   job left it, on its last point, and the cut is raised by how far the highest
   point is above that. It asks for the cutter (T1) and faces down in passes
   until the stock is that thick.

Edit VARIABLES below (or pass flags, see --help) and upload each .nc to the Z1
from Makera Studio under its own name: step 2 finds the probe run in the log
by it. Run Studio's corner probe for X/Y first, and start both jobs with Auto
leveling OFF (it bends the cut to follow the uneven top).
"""

import argparse
import bisect
import math
import re
import sys
from pathlib import Path

# --- VARIABLES (dimensions are POSITIVE values) ---
VARIABLES = {
    "stock_width": 65.4,    # X-axis dimension of stock (the widest, if a side is uneven)
    "stock_length": 50.4,   # Y-axis dimension of stock
    "stock_height": 33.0,   # Thickness at the highest probed point (with a fence, only a check on the fence reading)
    "final_height": 0.0,    # Thickness to leave; 0 = the lowest probed point, rounded down to round_to
    "fence_x": -7.5,        # A point on the fence's top, in job coordinates (from the origin corner): Makera's
    "fence_y": 77.0,        #   anchor plate, left arm, between a screw hole and the dowel. --no-fence to skip it
    "fence_height": 5.0,    # Height of the fence's top above the bed the stock sits on
    "round_to": 0.5,        # Round the lowest point's thickness down to a multiple of this
    "tool_dia": 3.175,      # Diameter of your facing bit (the included collet is 1/8")
    "stepover": 2.0,        # Max stepover (2.0 = 63% of 3.175, Makera's 6061 value)
    "cut_along": "y",       # Passes run front to back (y; the Z1 is stiffer in Y) or left to right (x)
    "air_margin": 0.3,      # A pass cuts wherever the probed top is within this of it (the top between probe points)
    "pass_depth": 0.2,      # Depth of cut per pass
    "probe_grid_x": 5,      # Probe points along X
    "probe_grid_y": 4,      # Probe points along Y
    "probe_inset": 3.0,     # Keep probe points this far inside the stock edges
    "probe_clearance": 5.0, # Lift between probe points; must exceed highest minus lowest thickness
    "top_margin": 0.2,      # Start cutting this far above Z0, for high spots between probe points or at the edges
    "origin": "topFrontLeft",  # Studio origin corner: front-left, where the stock's square edges are
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
PROBE_FEED = 300      # Grid probing feed (Studio probes at 500 fast / 100 slow)
FENCE_CHECK = 2.0     # A fence reading this far from stock_height means the probe probably missed the fence
LIFT = 0.5            # Between passes, lift this far above the floor just cut to move to the next start
SAMPLE = 0.25         # Spacing of the checks for material along each row

PROBE_JOB = "surface-to-lowest-point-probe.nc"
FACE_JOB = "surface-to-lowest-point.nc"
STUDIO_LOGS = Path.home() / "Library/Application Support/MakeraStudio/logs"  # macOS; pass --log elsewhere

# Studio's log, e.g.
#   Debug    | 2026-10-04 14:13:27 Sun | :0,  | "[INFO]14:13:27.739 - Playing file: /sd/gcodes/macros/x.nc"
#   Debug    | 2026-10-04 14:13:28 Sun | :0,  | "[INFO]14:13:28.168 - Normal info: EEPRROM Data: G54: -121.866, -143.602, -67.862\n"
PLAYING = re.compile(r'Playing file: (.+?)"?\s*$')
G54 = re.compile(r"EEPRROM Data: G54: (-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)")
STAMP = re.compile(r"\| (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ")
G10_SENT = re.compile(r"Normal info: (G10 L2\d? [^\"\\]*)")  # Studio echoes what it sends, e.g. its own Z probe

# Origin corner -> its position in stock coordinates measured from front-left.
ORIGINS = {
    "topFrontLeft": (0.0, 0.0),
    "topFrontRight": (1.0, 0.0),
    "topBackLeft": (0.0, 1.0),
    "topBackRight": (1.0, 1.0),
    "topCenter": (0.5, 0.5),
}

HERE = Path(__file__).resolve().parent


def fmt(value):
    s = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for name, default in VARIABLES.items():
        flag = "--" + name.replace("_", "-")
        if name in ("origin", "cut_along"):
            p.add_argument(flag, default=default, choices=ORIGINS if name == "origin" else ("x", "y"))
        else:
            p.add_argument(flag, type=float if default is None else type(default), default=default)
    p.add_argument("--probe", action="store_true", help=f"write the probe job ({PROBE_JOB}) instead of the facing job")
    p.add_argument("--full-passes", action="store_true", help="every pass covers the whole top, even where it only cuts air")
    p.add_argument("--no-fence", action="store_true", help="don't probe the fence; the highest point is taken to be --stock-height thick")
    p.add_argument("--log", type=Path, default=STUDIO_LOGS, help="Studio's log folder or one log file (default: %(default)s)")
    p.add_argument("--tool", help="take tool_dia, rpm, feeds, pass_depth and stepover from this tool in"
                   " ../tool-library (see tool-library/toollib.py list); options you pass still win")
    p.add_argument("--material", default="Aluminum", help="which of the tool's presets to use (default: Aluminum)")
    p.add_argument("-o", "--out", type=Path)
    return p.parse_args()


def check(v):
    errors = []
    for name in ("stock_width", "stock_length", "stock_height", "round_to", "tool_dia", "stepover", "pass_depth", "probe_clearance"):
        if v[name] <= 0:
            errors.append(f"{name} must be positive")
    if not 0 <= v["final_height"] < v["stock_height"]:
        errors.append("final_height must be less than stock_height (0 = from the probe run)")
    if v["stepover"] >= v["tool_dia"]:
        errors.append("stepover must be smaller than tool_dia or ridges are left between passes")
    if v["top_margin"] < 0 or v["probe_inset"] < 0:
        errors.append("top_margin and probe_inset can't be negative")
    if (v["fence_x"] is None) != (v["fence_y"] is None):
        errors.append("give both fence_x and fence_y, or neither")
    if v["fence_height"] <= 0:
        errors.append("fence_height must be positive")
    if v["air_margin"] < 0:
        errors.append("air_margin can't be negative")
    if min(v["probe_grid_x"], v["probe_grid_y"]) < 1:
        errors.append("probe_grid_x and probe_grid_y must be at least 1")
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


def spread(size, count, inset):
    if count == 1 or size <= 2 * inset:
        return [size / 2]
    return [inset + (size - 2 * inset) * i / (count - 1) for i in range(count)]


def probe_points(v):
    """Grid inside the stock, serpentine, starting at the corner nearest the origin."""
    ox, oy = ORIGINS[v["origin"]]
    us = spread(v["stock_width"], v["probe_grid_x"], v["probe_inset"])
    ts = spread(v["stock_length"], v["probe_grid_y"], v["probe_inset"])
    us, ts = (us[::-1] if ox > 0.5 else us), (ts[::-1] if oy > 0.5 else ts)
    points = [(u, t) for j, t in enumerate(ts) for u in (us if j % 2 == 0 else us[::-1])]
    return to_program(v, points)


def fenced(v):
    return v["fence_x"] is not None


def studio_probe(x, y):
    """Probe a point the way Studio's own Z probe does, from the top of Z travel, and set Z0 there."""
    return [
        "G53 G0 Z-3",
        f"G90 G0 X{fmt(x)} Y{fmt(y)}",
        "G38.2 Z-108 F500",
        "G91 G0 Z1",
        "G38.2 Z-2 F100",
        "G10 L20 P0 Z0",
        "G90",
        "M498 ; print Z0 (G54) to Studio's log",
    ]


def probe_job(v):
    """Measure every grid point: Z0 is set on each in turn and M498 prints it to Studio's log.

    With a fence, its top is probed first: it sits fence_height above the bed, so
    the log gives the bed's height too and every thickness comes from the probe.
    G38.2 alarms if it touches nothing, so a point more than probe_clearance below
    the one before (or off the stock) stops the job instead of logging a wrong height.
    Each G10 stores Z0 (G54) the way Studio's own Z probe does: one EEPROM write.
    """
    pts = probe_points(v)
    c = v["probe_clearance"]
    fence = ["; The fence's top, fence_height above the bed", *studio_probe(v["fence_x"], v["fence_y"])] if fenced(v) else []
    lines = [
        "; surface-to-lowest-point probe job, generated by surface-to-lowest-point.py --probe - edit the script, not this file.",
        f"; Prints the height of {len(pts)} points{' and the fence' if fence else ''} to Studio's log."
        " No spindle, no cutting. Turn auto-leveling OFF.",
        "; Then run surface-to-lowest-point.py again: it reads the heights from the log and writes the facing job.",
        "",
        "G90 G21",
        "M370 ; clear any auto-leveling grid left from an earlier job",
        "",
        "T0 M6",
        *fence,
        "; The stock",
        *studio_probe(*pts[0]),
    ]
    for x, y in pts[1:]:
        lines += [f"G0 Z{fmt(c)}", f"G0 X{fmt(x)} Y{fmt(y)}", f"G38.2 Z-{fmt(2 * c)} F{PROBE_FEED}", "G10 L20 P0 Z0", "M498"]
    lines += [f"G0 Z{fmt(SAFE_Z)}", "G28", "M02"]
    return "\n".join(lines) + "\n"


def last_probe_run(where):
    """The newest run of the probe job in Studio's logs: (time, log file name, entries).

    Entries, in order from the run's start, each with the time it was logged:
    ("z", Z0) for every M498, during the run and after it; ("g10", line) for every
    G10 Studio sent; ("played", file) for every file played since. plan() takes the
    run's points from them, and the Z0 the facing job will start from.
    """
    files = sorted(where.glob("log_*.txt")) if where.is_dir() else [where] if where.is_file() else []
    run = None
    for path in files:  # oldest first, so a run collects what happened after it, in newer logs too
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                stamp = STAMP.search(line)
                stamp = stamp.group(1) if stamp else "?"
                m = PLAYING.search(line)
                if m:
                    name = m.group(1).strip()
                    if name.rsplit("/", 1)[-1] == PROBE_JOB:
                        run = (stamp, path.name, [])
                    elif run:
                        run[2].append(("played", name, stamp))
                    continue
                if run is None:
                    continue
                m = G54.search(line)
                if m:
                    run[2].append(("z", float(m.group(3)), stamp))
                    continue
                m = G10_SENT.search(line)
                if m:
                    run[2].append(("g10", m.group(1).strip(), stamp))
    return run


def plan(v, run):
    """The probe run -> (table rows, thickest, lowest thickness, thickness to leave, Z0, where Z0 came from,
    highest point above Z0).

    With a fence the first value is the fence's top, so the bed is fence_height
    below it and every thickness is probed. Without one, the highest point is
    taken to be stock_height thick.

    The facing job doesn't probe; it cuts from the Z0 in the machine. That is where
    the probe job left it (its last point), or the last Z0 printed since, e.g. by a
    facing job that probed again. Anything logged after that which could move Z0
    without printing it (a G10 Studio sent, another job) stops the script. A tool
    change doesn't move Z0: the new tool is measured against the probe.
    """
    when, log, entries = run
    pts = probe_points(v)
    expected = len(pts) + fenced(v)
    points = [i for i, (kind, _, _) in enumerate(entries) if kind == "z"]
    if len(points) < expected:
        raise SystemExit(
            f"surface-to-lowest-point: the last probe run ({when}, {log}) logged {len(points)} of {expected} points."
            " It stopped early, or the grid or fence options differ from the ones it was written with;"
            " run the probe job again"
        )
    zs = [entries[i][1] for i in points[:expected]]
    z0, z0_from = zs[-1], "where the probe job left it, on its last point"
    after = entries[points[expected - 1] + 1:]
    prints = [i for i, (kind, _, _) in enumerate(after) if kind == "z"]
    if prints:
        _, z0, stamp = after[prints[-1]]
        z0_from = f"printed at {stamp}, after the probe run"
        after = after[prints[-1] + 1:]
    moved = [
        f"Studio sent {value} ({stamp})" if kind == "g10" else f"{value} played ({stamp})"
        for kind, value, stamp in after
        if kind == "g10" or (kind == "played" and value.rsplit("/", 1)[-1] != FACE_JOB)
    ]
    if moved:
        raise SystemExit(
            f"surface-to-lowest-point: Z0 may have moved since it was last printed: {'; '.join(moved[:3])}."
            " The facing job cuts from the Z0 in the machine, so run the probe job again"
        )
    high = max(zs[fenced(v):])
    bed = zs[0] - v["fence_height"] if fenced(v) else high - v["stock_height"]
    zs = zs[fenced(v):]
    thickest = high - bed
    if fenced(v) and abs(thickest - v["stock_height"]) > FENCE_CHECK:
        raise SystemExit(
            f"surface-to-lowest-point: from the fence, the highest point is {thickest:.2f} mm thick, but stock_height"
            f" is {fmt(v['stock_height'])}. If the probe missed the fence it touched the bed and read"
            f" {fmt(v['fence_height'])} mm low: check --fence-x/--fence-y. If the stock really is that thick,"
            " pass --stock-height close to it"
        )
    rows = [(x, y, z - high, z - bed) for (x, y), z in zip(pts, zs)]
    lowest = min(r[3] for r in rows)
    final = math.floor(lowest / v["round_to"] + 1e-9) * v["round_to"]
    if final <= 0:
        raise SystemExit("surface-to-lowest-point: that leaves nothing; check --stock-height")
    return rows, thickest, lowest, final, z0, z0_from, high - z0


def levels(v):
    top, target = v["top_margin"], v["final_height"] - v["stock_height"]
    count = math.ceil((top - target) / v["pass_depth"] - 1e-9)
    return [top - k * v["pass_depth"] for k in range(1, count)] + [target]


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


def rows_across(v):
    """Where the passes run across the stock: both edges and evenly between, no more than stepover apart."""
    _, width, _ = frame(v)
    n = math.ceil(width / v["stepover"] - 1e-9)
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
    """The facing job. It doesn't probe: Z0 stays where it is (z0, from the log) and the
    highest point is shift above it, so every Z is raised by shift. Only the cutter
    goes in, measured against the probe at the tool change.

    With a top (from the probe run), each pass but the last only covers the rows and
    stretches that still have material above it; without one (--full-passes) every
    pass covers the whole top.
    """
    length, width, stock = frame(v)
    r = v["tool_dia"] / 2
    bs, step = rows_across(v)
    full_rows = [(b, (-(r + LEAD), length + r + LEAD)) for b in bs]
    full = zigzag(full_rows, True)
    zs = [z + shift for z in levels(v)]
    if top:
        samples, profile = material(v, top, bs)

    passes, near = [], None  # (level, z, (a, b) points)
    for n, z in enumerate(zs, 1):
        if not top:
            path = full if n % 2 else full[::-1]  # serpentine: each level starts where the last ended
        elif n == len(zs):
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
        body.append(f"; Level {n}/{len(zs)} Z{fmt(z)}")
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
    if args.no_fence:
        v["fence_x"] = v["fence_y"] = None
    if args.tool:  # the tool library is only needed with --tool, so this script also runs on its own
        sys.path.insert(0, str(HERE.parent / "tool-library"))
        import toollib

        tool, preset = toollib.apply(v, args.tool, args.material)
        print(
            f"tool: {tool.description} ({tool.source}), {preset.name}: {fmt(v['tool_dia'])} mm, {v['rpm']} rpm,"
            f" {v['feed']} mm/min, plunge {v['plunge_feed']}, {fmt(v['pass_depth'])} mm passes, {fmt(v['stepover'])} mm stepover"
        )
    check(v)

    if args.probe:
        out = args.out or HERE / PROBE_JOB
        out.write_text(probe_job(v))
        print(f"wrote {out} ({len(probe_points(v))} points{' and the fence' if fenced(v) else ''}, no cutting)")
        if out.name != PROBE_JOB:
            print(f"note: the next step looks for {PROBE_JOB} in Studio's log; upload it under that name")
        print("upload it, corner-probe X/Y in Studio and run it with Auto leveling off")
        print("then run this script again without --probe to write the facing job from what it measured")
        return

    # The facing job cuts from the Z0 the probe job left, so the probe run is always read.
    run = last_probe_run(args.log)
    if run is None:
        raise SystemExit(
            f"surface-to-lowest-point: no run of {PROBE_JOB} in {args.log}. Write it with --probe and run it"
            " on the machine first (or pass --log if Studio keeps its logs elsewhere)"
        )
    rows, thickest, lowest, final, z0, z0_from, shift = plan(v, run)
    high = max(rows, key=lambda r: r[2])
    low = min(rows, key=lambda r: r[3])
    print(f"Probe run {run[0]} ({run[1]}), {len(rows)} points:")
    print(f"  {'X':>7} {'Y':>7} {'height':>8} {'thickness':>10}")
    for x, y, height, thick in rows:
        mark = "  highest" if (x, y) == high[:2] else "  lowest" if (x, y) == low[:2] else ""
        print(f"  {x:7.1f} {y:7.1f} {height:8.3f} {thick:10.3f}{mark}")
    if fenced(v):
        print(
            f"Thicknesses are measured from the fence (its top {fmt(v['fence_height'])} mm above the bed,"
            f" probed at X{fmt(v['fence_x'])} Y{fmt(v['fence_y'])})."
        )
    else:
        print(
            f"Thickness assumes the stock is {fmt(v['stock_height'])} mm at the highest point (X{fmt(high[0])} Y{fmt(high[1])});"
            " measure it there and pass --stock-height if not."
        )
    given = v["final_height"]
    v["stock_height"] = round(thickest, 3)
    if given > 0:
        if given >= v["stock_height"]:
            raise SystemExit(f"surface-to-lowest-point: --final-height must be less than {thickest:.2f} mm, the highest point")
        print(f"Leaving {fmt(given)} mm, given with --final-height:", end="")
        note = f"Probe run {run[0]}: highest point {thickest:.2f} mm thick, leaving {fmt(given)} mm (--final-height)."
    else:
        v["final_height"] = final
        print(f"Lowest point {lowest:.2f} mm, rounded down to {fmt(final)} mm:", end="")
        note = f"Probe run {run[0]}: lowest point {lowest:.2f} mm thick, leaving {fmt(final)} mm."
    print(f" cutting {fmt(v['stock_height'] - v['final_height'])} mm below the highest point.")
    print(f"Z0 is G54 Z {z0:.3f}, {z0_from}: the highest point is {shift:.3f} mm above it.\n")

    ox, oy = ORIGINS[v["origin"]]
    top = None if args.full_passes else Top(
        [(x + ox * v["stock_width"], y + oy * v["stock_length"], height + shift) for x, y, height, _ in rows]
    )
    program, passes, seconds = build(v, note, z0, shift, top)
    out = args.out or HERE / FACE_JOB
    out.write_text(program)
    saved = "" if args.full_passes else f", full-width passes would take ~{build(v, note, z0, shift)[2] / 60:.0f}"
    print(
        f"wrote {out} ({passes} passes along {v['cut_along'].upper()}, ~{seconds / 60:.0f} min{saved},"
        f" {fmt(v['stock_height'])} -> {fmt(v['final_height'])} mm thick)"
    )
    print(f"clamps and vise jaws must sit below {fmt(v['final_height'])} mm: the cutter runs past the stock edges")
    print("upload it and start it with Auto leveling off, without changing Z0 or running anything else first;")
    print(f"it doesn't probe. Before the cutter goes in, Studio's log should show G54 Z {z0:.3f}: stop the job if not")


if __name__ == "__main__":
    main()
