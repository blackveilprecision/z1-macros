#!/usr/bin/env python3
"""Generate a multi-pass facing program for the Makera Z1.

The Z1 firmware (Smoothieware fork, 1.1.2) has no #variables, expressions or
O-word loops, so the facing program is expanded here into plain G0/G1 moves in
the same dialect Makera Studio exports (;@MKR| header, T1 M6, M7, G28/M02 end).

Edit VARIABLES below (or pass flags, see --help), run the script, then upload
surface-stock.nc to the Z1 from Makera Studio. (Studio's local GCodes folder is
only its cache of the machine's SD card; the machine plays the uploaded copy.)
--probe-only writes surface-stock-probe-test.nc: the probe grid alone, no
spindle and no cutting, to check probing on the machine first.

The program first probes a grid on the stock with the 3D probe (T0) and sets
Z0 on the highest point it finds, then asks for the cutter (T1) and faces down
from there. Run Studio's corner probe for X/Y first, and start the job with
Auto leveling OFF (it bends the cut to follow the uneven top).
"""

import argparse
import math
import sys
from pathlib import Path

# --- VARIABLES (dimensions are POSITIVE values) ---
VARIABLES = {
    "stock_width": 69.0,    # X-axis dimension of stock
    "stock_length": 50.1,   # Y-axis dimension of stock
    "stock_height": 19.9,   # Thickness at the highest spot (Studio's stock preview)
    "tool_dia": 3.175,      # Diameter of your facing bit (the included collet is 1/8")
    "stepover": 2.0,        # Max stepover (2.0 = 63% of 3.175, Makera's 6061 value)
    "target_z": -3.0,       # Depth to remove below the highest point (at least highest minus lowest thickness)
    "pass_depth": 0.2,      # Depth of cut per pass
    "probe_grid_x": 5,      # Probe points along X (0 on either axis = no probing; set Z0 on the high spot in Studio)
    "probe_grid_y": 4,      # Probe points along Y
    "probe_clearance": 5.0, # Lift between probe points; must exceed highest minus lowest thickness
    "top_margin": 0.2,      # Start cutting this far above Z0, for high spots between probe points or at the edges
    "origin": "topBackRight",  # Studio origin corner the program is written for
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
PROBE_INSET = 3.0     # Keep probe points this far inside the stock edges
PROBE_FEED = 300      # Grid probing feed (Studio probes at 500 fast / 100 slow)

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
        if name == "origin":
            p.add_argument(flag, default=default, choices=ORIGINS)
        else:
            p.add_argument(flag, type=type(default), default=default)
    p.add_argument("--tool", help="take tool_dia, rpm, feeds, pass_depth and stepover from this tool in"
                   " ../tool-library (see tool-library/toollib.py list); options you pass still win")
    p.add_argument("--material", default="Aluminum", help="which of the tool's presets to use (default: Aluminum)")
    p.add_argument("-o", "--out", type=Path)
    p.add_argument("--probe-only", action="store_true", help="write a probe-grid test with no spindle or cutting")
    return p.parse_args()


def check(v):
    errors = []
    for name in ("stock_width", "stock_length", "stock_height", "tool_dia", "stepover", "pass_depth", "probe_clearance"):
        if v[name] <= 0:
            errors.append(f"{name} must be positive")
    if v["stepover"] >= v["tool_dia"]:
        errors.append("stepover must be smaller than tool_dia or ridges are left between passes")
    if not -v["stock_height"] < v["target_z"] < 0:
        errors.append("target_z must be negative and less than the stock thickness")
    if v["top_margin"] < 0:
        errors.append("top_margin can't be negative")
    if min(v["probe_grid_x"], v["probe_grid_y"]) < 0:
        errors.append("probe_grid_x and probe_grid_y can't be negative")
    if not 0 < v["feed"] <= MAX_FEED or not 0 < v["plunge_feed"] <= MAX_FEED:
        errors.append(f"feeds must be between 0 and {MAX_FEED} mm/min")
    if not 0 < v["rpm"] <= MAX_RPM:
        errors.append(f"rpm must be between 0 and {MAX_RPM}")
    if errors:
        raise SystemExit("surface-stock: " + "; ".join(errors))


def probing(v):
    return v["probe_grid_x"] > 0 and v["probe_grid_y"] > 0


def levels(v):
    top = v["top_margin"]
    count = math.ceil((top - v["target_z"]) / v["pass_depth"] - 1e-9)
    return [top - k * v["pass_depth"] for k in range(1, count)] + [v["target_z"]]


def to_program(v, points):
    """Stock coordinates (from the front-left corner) -> program coordinates for the chosen origin."""
    ox, oy = ORIGINS[v["origin"]]
    return [(u - ox * v["stock_width"], t - oy * v["stock_length"]) for u, t in points]


def spread(size, count):
    if count == 1 or size <= 2 * PROBE_INSET:
        return [size / 2]
    return [PROBE_INSET + (size - 2 * PROBE_INSET) * i / (count - 1) for i in range(count)]


def probe_points(v):
    """Grid inside the stock, serpentine, starting at the corner nearest the origin."""
    ox, oy = ORIGINS[v["origin"]]
    us = spread(v["stock_width"], v["probe_grid_x"])
    ts = spread(v["stock_length"], v["probe_grid_y"])
    us, ts = (us[::-1] if ox > 0.5 else us), (ts[::-1] if oy > 0.5 else ts)
    points = [(u, t) for j, t in enumerate(ts) for u in (us if j % 2 == 0 else us[::-1])]
    return to_program(v, points)


def probe_block(v):
    """Set Z0 on the highest grid point using only G38/G10 (no variables on this firmware).

    The first point copies Studio's own Z probe. Every later point probes down
    exactly probe_clearance from probe_clearance above the current Z0, so the
    probe stops on the surface if it is higher than Z0 and at Z0 otherwise;
    G10 L20 P0 Z0 then makes that the new Z0. Z0 can only move up.

    Only G54 Z is touched, as Studio's own Z probe does (each G10 is one
    EEPROM write). G92 is left alone: Studio's "set current position as
    origin" and its 4th-axis toolpaths keep X/Y/Z/A origins there.
    """
    if not probing(v):
        return [], 0.0
    pts = probe_points(v)
    c = fmt(v["probe_clearance"])
    (x, y), rest = pts[0], pts[1:]
    lines = [
        f"; Probe {v['probe_grid_x']}x{v['probe_grid_y']} points with the 3D probe and set Z0 on the highest one",
        "T0 M6",
        "G53 G0 Z-3",
        f"G90 G0 X{fmt(x)} Y{fmt(y)}",
        "G38.2 Z-108 F500",
        "G91 G0 Z1",
        "G38.2 Z-2 F100",
        "G10 L20 P0 Z0",
        "G90",
    ]
    seconds = 30.0
    for (x0, y0), (x, y) in zip(pts, rest):
        lines += [f"G0 Z{c}", f"G0 X{fmt(x)} Y{fmt(y)}", f"G38.3 Z-{c} F{PROBE_FEED}", "G10 L20 P0 Z0"]
        seconds += math.hypot(x - x0, y - y0) / MAX_FEED * 60 + v["probe_clearance"] * (1 / PROBE_FEED + 1 / MAX_FEED) * 60
    lines += [f"G0 Z{fmt(SAFE_Z)}", ""]
    return lines, seconds


def raster(v):
    """Zig-zag along X, stepping in Y, in program coordinates for the chosen origin."""
    w, l, r = v["stock_width"], v["stock_length"], v["tool_dia"] / 2
    x_lo, x_hi = -(r + LEAD), w + r + LEAD  # tool fully off the stock at both ends
    rows = math.ceil(l / v["stepover"] - 1e-9)
    points = []
    for i in range(rows + 1):
        y = l * i / rows  # tool centre runs on both Y edges, so the whole face is covered
        xs = (x_lo, x_hi) if i % 2 == 0 else (x_hi, x_lo)
        points += [(x, y) for x in xs]
    return to_program(v, points), l / rows


def build(v):
    path, step = raster(v)
    zs = levels(v)
    probe, seconds = probe_block(v)

    body = []
    x, y = path[0]
    approach_z = v["top_margin"] + APPROACH
    body += [f"G0 X{fmt(x)} Y{fmt(y)}", f"S{v['rpm']} M3", f"G0 Z{fmt(SAFE_Z)}", f"G0 Z{fmt(approach_z)}"]
    z_prev = approach_z
    for n, z in enumerate(zs, 1):
        pts = path if n % 2 else path[::-1]  # serpentine: each level starts where the last ended
        body.append(f"; Level {n}/{len(zs)} Z{fmt(z)}")
        body.append(f"G1 Z{fmt(z)} F{v['plunge_feed']}")
        seconds += abs(z_prev - z) / v["plunge_feed"] * 60
        feed = f" F{v['feed']}"
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            words = (f"X{fmt(x1)}" if x1 != x0 else "") + (f" Y{fmt(y1)}" if y1 != y0 else "")
            body.append(f"G1 {words.strip()}{feed}")
            seconds += math.hypot(x1 - x0, y1 - y0) / v["feed"] * 60
            feed = ""
        z_prev = z
    body += [f"G0 Z{fmt(SAFE_Z)}", "M9", "M05", "G28", "M02"]

    w, l, h, d = v["stock_width"], v["stock_length"], v["stock_height"], v["tool_dia"]
    ox, oy = ORIGINS[v["origin"]]
    z0 = (
        f"Z0 is probed on the highest of {v['probe_grid_x'] * v['probe_grid_y']} grid points (needs the 3D probe, T0)."
        if probing(v)
        else "Set Z0 on the highest spot in Studio before running."
    )
    header = [
        ";@MKR|BEGIN",
        ";@MKR|SCHEMA|v=1.0.0",
        ";@MKR|MACHINE|id=Z1|name=Makera Z1",
        ";@MKR|MATERIAL|id=|name3=other|name1=Aluminum Alloys|name2=6061 Aluminum|uuid1=019bfadc-e599-74ef-b4ba-f34413e6f231",
        f";@MKR|STOCK|id=cuboid|length={fmt(w)}|width={fmt(l)}|height={fmt(h)}|diameter=50",
        f";@MKR|ORIGIN|id=0|type_name={v['origin']}|x={fmt((ox - 0.5) * w)}|y={fmt((oy - 0.5) * l)}|z={fmt(h / 2)}",
        ";@MKR|CAM|id=surface-stock|name=surface-stock.py|v=1.0.0",
        ";@MKR|UNIT|value=mm",
        f";@MKR|MAXFEEDRATE|value={MAX_FEED}",
        f";@MKR|TOOL|number=1|id=|name={fmt(d)}mm Flat End|type=Flat End|handlediameter={fmt(d)}|sticklength=0"
        f"|shoulderlength={FLUTE_LENGTH}|flutelength={FLUTE_LENGTH}|diameter={fmt(d)}|tipdiameter={fmt(d)}"
        "|cornerradius=0|angle=0|halfAngle=0",
        f";@MKR|TIME|seconds={round(seconds)}",
        ";@MKR|TOOLPATH|number=1|tool_number=1|name=[T1]Surface Stock",
        ";@MKR|END",
        "",
        "; Generated by surface-stock.py (github.com/blackveilprecision/z1-macros) - edit the script, not this file.",
        f"; Stock {fmt(w)} x {fmt(l)} mm, origin {v['origin']}. {z0}",
        f"; Turn auto-leveling OFF. Cuts Z{fmt(v['top_margin'])} -> Z{fmt(v['target_z'])}"
        f" in {len(zs)} passes of <= {fmt(v['pass_depth'])} mm, {fmt(step)} mm stepover, {fmt(d)} mm tool.",
        "",
        "G90 G21",
        ";@MKR|TOOLPATH_START|toolpath_number=1",
        "",
        "M370 ; clear any auto-leveling grid left from an earlier job",
        "",
        *probe,
        f"; T1-{fmt(d)}mm Flat End",
        "",
        "T1 M6",
        "M7",
    ]
    return "\n".join(header + body) + "\n", len(zs), seconds


def build_probe_test(v):
    """Two passes over the same grid, reporting through M498.

    Output from file lines is discarded while a file plays, but M498 prints the
    G54 offset (Z = machine Z of Z0) to Studio's log. Pass 1 sets Z0 on every
    point in turn, so the log gets each point's height; pass 2 is the job's own
    probe block, logged after every point so Z0 can be seen climbing.
    """
    pts = probe_points(v)
    c = v["probe_clearance"]
    probe, _ = probe_block(v)
    measure = [
        "; Pass 1: measure every point (Z0 is set on each one; M498 logs it)",
        "T0 M6",
        "G53 G0 Z-3",
        f"G90 G0 X{fmt(pts[0][0])} Y{fmt(pts[0][1])}",
        "G38.2 Z-108 F500",
        "G91 G0 Z1",
        "G38.2 Z-2 F100",
        "G10 L20 P0 Z0",
        "G90",
        "M498",
    ]
    for x, y in pts[1:]:
        measure += [f"G0 Z{fmt(c)}", f"G0 X{fmt(x)} Y{fmt(y)}", f"G38.2 Z-{fmt(2 * c)} F{PROBE_FEED}", "G10 L20 P0 Z0", "M498"]
    running = [line for p in probe for line in ([p, "M498"] if p.startswith("G10") else [p])]
    lines = [
        "; surface-stock probe test: no spindle, no cutting. Generated by surface-stock.py --probe-only.",
        f"; Logs {len(pts)} point heights, then runs the job's probe block and logs Z0 after each point.",
        "G90 G21",
        "M370 ; clear any auto-leveling grid left from an earlier job",
        "M498",
        "",
        *measure,
        f"G0 Z{fmt(c)}",
        "",
        "; Pass 2: the job's own probe block",
        *running,
        "M02",
    ]
    return "\n".join(lines) + "\n"


def main():
    args = parse_args()
    v = {name: getattr(args, name) for name in VARIABLES}
    if args.tool:  # the tool library is only needed with --tool, so this script also runs on its own
        sys.path.insert(0, str(HERE.parent / "tool-library"))
        import toollib

        tool, preset = toollib.apply(v, args.tool, args.material)
        print(
            f"tool: {tool.description} ({tool.source}), {preset.name}: {fmt(v['tool_dia'])} mm, {v['rpm']} rpm,"
            f" {v['feed']} mm/min, plunge {v['plunge_feed']}, {fmt(v['pass_depth'])} mm passes, {fmt(v['stepover'])} mm stepover"
        )
    check(v)
    if args.probe_only:
        if not probing(v):
            raise SystemExit("surface-stock: --probe-only needs probe_grid_x and probe_grid_y above 0")
        out = args.out or HERE / "surface-stock-probe-test.nc"
        out.write_text(build_probe_test(v))
        print(f"wrote {out}: upload and run it with the 3D probe, Auto leveling off")
        return
    program, passes, seconds = build(v)
    out = args.out or HERE / "surface-stock.nc"
    out.write_text(program)
    print(f"wrote {out} ({passes} passes, ~{seconds / 60:.0f} min, removes {fmt(-v['target_z'])} mm below the highest point)")
    if probing(v):
        print("corner-probe X/Y in Studio, start with Auto leveling off; the job probes Z itself")
    else:
        print("set Z0 on the highest spot in Studio and start with Auto leveling off")
    print("upload it to the Z1 from Makera Studio")


if __name__ == "__main__":
    main()
