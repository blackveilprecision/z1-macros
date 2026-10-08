#!/usr/bin/env python3
"""Generate a program that faces flat stock down to a set thickness on the Makera Z1.

The Z1 firmware (Smoothieware fork, 1.1.2) has no #variables, expressions or
O-word loops, so the facing program is expanded here into plain G0/G1 moves in
the same dialect Makera Studio exports (;@MKR| header, T1 M6, M7, G28/M02 end).

Edit VARIABLES below (or pass flags, see --help), run the script, then upload
face-to-thickness.nc to the Z1 from Makera Studio. (Studio's local GCodes
folder is only its cache of the machine's SD card; the machine plays the
uploaded copy.)

The program probes the middle of the top with the 3D probe (T0) and sets Z0
there, then asks for the cutter (T1) and faces the whole top down by
stock_height - final_height in equal passes. The top must already be flat
(run surface-stock first if it isn't). Run Studio's corner probe for X/Y
first, and start the job with Auto leveling OFF.
"""

import argparse
import math
import sys
from pathlib import Path

# --- VARIABLES (dimensions are POSITIVE values) ---
VARIABLES = {
    "stock_width": 69.0,    # X-axis dimension of stock
    "stock_length": 50.4,   # Y-axis dimension of stock
    "stock_height": 17.0,   # Thickness now; measure it, the cut depth is worked out from it
    "final_height": 10.0,   # Thickness to leave
    "tool_dia": 3.175,      # Diameter of your facing bit (the included collet is 1/8")
    "stepover": 2.0,        # Max stepover (2.0 = 63% of 3.175, Makera's 6061 value)
    "pass_depth": 0.2,      # Max depth of cut per pass; the depth is split into equal passes
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
PROBE_SECONDS = 30.0  # Rough time for the tool change and Studio-style Z probe

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
    p.add_argument("--no-probe", action="store_true", help="skip probing; set Z0 on the top in Studio before running")
    return p.parse_args()


def check(v):
    errors = []
    for name in ("stock_width", "stock_length", "stock_height", "final_height", "tool_dia", "stepover", "pass_depth"):
        if v[name] <= 0:
            errors.append(f"{name} must be positive")
    if v["final_height"] >= v["stock_height"]:
        errors.append("final_height must be less than stock_height")
    if v["stepover"] >= v["tool_dia"]:
        errors.append("stepover must be smaller than tool_dia or ridges are left between passes")
    if not 0 < v["feed"] <= MAX_FEED or not 0 < v["plunge_feed"] <= MAX_FEED:
        errors.append(f"feeds must be between 0 and {MAX_FEED} mm/min")
    if not 0 < v["rpm"] <= MAX_RPM:
        errors.append(f"rpm must be between 0 and {MAX_RPM}")
    if errors:
        raise SystemExit("face-to-thickness: " + "; ".join(errors))


def levels(v):
    """Equal passes from Z0 down to the final surface, none deeper than pass_depth.

    Equal passes avoid a thin last pass that rubs instead of cutting.
    """
    depth = v["stock_height"] - v["final_height"]
    count = math.ceil(depth / v["pass_depth"] - 1e-9)
    return [-depth * k / count for k in range(1, count + 1)]


def to_program(v, points):
    """Stock coordinates (from the front-left corner) -> program coordinates for the chosen origin."""
    ox, oy = ORIGINS[v["origin"]]
    return [(u - ox * v["stock_width"], t - oy * v["stock_length"]) for u, t in points]


def probe_block(v):
    """Set Z0 on the middle of the top, the same way Studio's own Z probe does.

    Only G54 Z is touched (one EEPROM write). G92 is left alone: Studio's
    "set current position as origin" and its 4th-axis toolpaths keep X/Y/Z/A
    origins there.
    """
    [(x, y)] = to_program(v, [(v["stock_width"] / 2, v["stock_length"] / 2)])
    return [
        "; Probe the middle of the top with the 3D probe and set Z0 there",
        "T0 M6",
        "G53 G0 Z-3",
        f"G90 G0 X{fmt(x)} Y{fmt(y)}",
        "G38.2 Z-108 F500",
        "G91 G0 Z1",
        "G38.2 Z-2 F100",
        "G10 L20 P0 Z0",
        "G90",
        f"G0 Z{fmt(SAFE_Z)}",
        "",
    ]


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


def build(v, probe):
    path, step = raster(v)
    zs = levels(v)
    seconds = PROBE_SECONDS if probe else 0.0

    body = []
    x, y = path[0]
    body += [f"G0 X{fmt(x)} Y{fmt(y)}", f"S{v['rpm']} M3", f"G0 Z{fmt(SAFE_Z)}", f"G0 Z{fmt(APPROACH)}"]
    z_prev = APPROACH
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
    depth = h - v["final_height"]
    ox, oy = ORIGINS[v["origin"]]
    z0 = (
        "Z0 is probed in the middle of the top (needs the 3D probe, T0)."
        if probe
        else "Set Z0 on the top in Studio before running."
    )
    header = [
        ";@MKR|BEGIN",
        ";@MKR|SCHEMA|v=1.0.0",
        ";@MKR|MACHINE|id=Z1|name=Makera Z1",
        ";@MKR|MATERIAL|id=|name3=other|name1=Aluminum Alloys|name2=6061 Aluminum|uuid1=019bfadc-e599-74ef-b4ba-f34413e6f231",
        f";@MKR|STOCK|id=cuboid|length={fmt(w)}|width={fmt(l)}|height={fmt(h)}|diameter=50",
        f";@MKR|ORIGIN|id=0|type_name={v['origin']}|x={fmt((ox - 0.5) * w)}|y={fmt((oy - 0.5) * l)}|z={fmt(h / 2)}",
        ";@MKR|CAM|id=face-to-thickness|name=face-to-thickness.py|v=1.0.0",
        ";@MKR|UNIT|value=mm",
        f";@MKR|MAXFEEDRATE|value={MAX_FEED}",
        f";@MKR|TOOL|number=1|id=|name={fmt(d)}mm Flat End|type=Flat End|handlediameter={fmt(d)}|sticklength=0"
        f"|shoulderlength={FLUTE_LENGTH}|flutelength={FLUTE_LENGTH}|diameter={fmt(d)}|tipdiameter={fmt(d)}"
        "|cornerradius=0|angle=0|halfAngle=0",
        f";@MKR|TIME|seconds={round(seconds)}",
        ";@MKR|TOOLPATH|number=1|tool_number=1|name=[T1]Face To Thickness",
        ";@MKR|END",
        "",
        "; Generated by face-to-thickness.py (github.com/blackveilprecision/z1-macros) - edit the script, not this file.",
        f"; Stock {fmt(w)} x {fmt(l)} x {fmt(h)} mm, origin {v['origin']}. {z0}",
        f"; Turn auto-leveling OFF. Faces {fmt(h)} -> {fmt(v['final_height'])} mm thick: Z0 -> Z{fmt(-depth)}"
        f" in {len(zs)} passes of {fmt(depth / len(zs))} mm, {fmt(step)} mm stepover, {fmt(d)} mm tool.",
        f"; Clamps and vise jaws must sit below {fmt(v['final_height'])} mm: the cutter runs past the stock edges.",
        "",
        "G90 G21",
        ";@MKR|TOOLPATH_START|toolpath_number=1",
        "",
        "M370 ; clear any auto-leveling grid left from an earlier job",
        "",
        *(probe_block(v) if probe else []),
        f"; T1-{fmt(d)}mm Flat End",
        "",
        "T1 M6",
        "M7",
    ]
    return "\n".join(header + body) + "\n", len(zs), seconds


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
    program, passes, seconds = build(v, probe=not args.no_probe)
    out = args.out or HERE / "face-to-thickness.nc"
    out.write_text(program)
    depth = v["stock_height"] - v["final_height"]
    print(
        f"wrote {out} ({passes} passes of {fmt(depth / passes)} mm, ~{seconds / 60:.0f} min,"
        f" {fmt(v['stock_height'])} -> {fmt(v['final_height'])} mm thick)"
    )
    print(f"clamps and vise jaws must sit below {fmt(v['final_height'])} mm: the cutter runs past the stock edges")
    if args.no_probe:
        print("set Z0 on the top in Studio and start with Auto leveling off")
    else:
        print("corner-probe X/Y in Studio, start with Auto leveling off; the job probes Z itself")
    print("upload it to the Z1 from Makera Studio")


if __name__ == "__main__":
    main()
