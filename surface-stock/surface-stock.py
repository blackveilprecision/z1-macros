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

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from shared import cli, toolpath, z1  # noqa: E402
from shared.z1 import fmt  # noqa: E402

# --- VARIABLES (dimensions are POSITIVE values) ---
VARIABLES = {
    "stock_width": 69.0,    # X-axis dimension of stock
    "stock_length": 50.1,   # Y-axis dimension of stock
    "stock_height": 19.9,   # Thickness at the highest spot (Studio's stock preview)
    "tool_dia": 3.175,      # Diameter of your facing bit (the included collet is 1/8")
    "stepover": 2.0,        # Max stepover (2.0 = 63% of 3.175, Makera's 6061 value)
    "target_z": -3.0,       # Depth to remove below the highest point (at least highest minus lowest thickness)
    "pass_depth": 0.2,      # Max depth of cut per pass; the depth is split into equal passes
    "probe_grid_x": 5,      # Probe points along X (0 on either axis = no probing; set Z0 on the high spot in Studio)
    "probe_grid_y": 4,      # Probe points along Y
    "probe_clearance": 5.0, # Lift between probe points; must exceed highest minus lowest thickness
    "top_margin": 0.2,      # Start cutting this far above Z0, for high spots between probe points or at the edges
    "origin": "topBackRight",  # Studio origin corner the program is written for
    "rpm": 12000,           # Makera library, 3.175*12mm Flat End (Metal) in 6061
    "feed": 500,            # mm/min cutting feed
    "plunge_feed": 200,     # mm/min plunge feed (plunges happen off the stock)
}

PROBE_INSET = 3.0     # Keep probe points this far inside the stock edges


def parse_args():
    p = cli.parser(__doc__)
    cli.add_variables(p, VARIABLES, {"origin": z1.ORIGINS})
    cli.add_tool(p)
    p.add_argument("-o", "--out", type=Path)
    p.add_argument("--probe-only", action="store_true", help="write a probe-grid test with no spindle or cutting")
    return p.parse_args()


def check(v):
    errors = cli.positive(v, ("stock_width", "stock_length", "stock_height", "tool_dia", "stepover", "pass_depth", "probe_clearance"))
    if v["stepover"] >= v["tool_dia"]:
        errors.append("stepover must be smaller than tool_dia or ridges are left between passes")
    if not -v["stock_height"] < round(v["target_z"], 3) < 0:  # as the passes count it
        errors.append("target_z must be negative and less than the stock thickness")
    if v["top_margin"] < 0:
        errors.append("top_margin can't be negative")
    if min(v["probe_grid_x"], v["probe_grid_y"]) < 0:
        errors.append("probe_grid_x and probe_grid_y can't be negative")
    cli.stop("surface-stock", errors + cli.feeds_and_rpm(v))


def probing(v):
    return v["probe_grid_x"] > 0 and v["probe_grid_y"] > 0


def probe_points(v):
    """Grid inside the stock, serpentine, starting at the corner nearest the origin, in program coordinates."""
    w, l = v["stock_width"], v["stock_length"]
    return z1.to_program(v["origin"], w, l, toolpath.probe_grid(v["origin"], w, l, v["probe_grid_x"], v["probe_grid_y"], PROBE_INSET))


def probe_block(v):
    """Set Z0 on the highest grid point using only G38/G10 (no variables on this firmware).

    The first point copies Studio's own Z probe. Every later point probes down
    exactly probe_clearance from probe_clearance above the current Z0, so the
    probe stops on the surface if it is higher than Z0 and at Z0 otherwise;
    G10 L20 P0 Z0 then makes that the new Z0. Z0 can only move up.

    Only G54 Z is touched, as Studio's own Z probe does (each G10 is one
    EEPROM write).
    """
    if not probing(v):
        return [], 0.0
    pts = probe_points(v)
    c = fmt(v["probe_clearance"])
    (x, y), rest = pts[0], pts[1:]
    lines = [
        f"; Probe {v['probe_grid_x']}x{v['probe_grid_y']} points with the 3D probe and set Z0 on the highest one",
        "T0 M6",
        *z1.z_probe(x, y),
    ]
    seconds = z1.PROBE_SECONDS
    for (x0, y0), (x, y) in zip(pts, rest):
        lines += [f"G0 Z{c}", f"G0 X{fmt(x)} Y{fmt(y)}", f"G38.3 Z-{c} F{z1.PROBE_FEED}", "G10 L20 P0 Z0"]
        seconds += math.hypot(x - x0, y - y0) / z1.MAX_FEED * 60 + v["probe_clearance"] * (1 / z1.PROBE_FEED + 1 / z1.MAX_FEED) * 60
    lines += [f"G0 Z{fmt(z1.SAFE_Z)}", ""]
    return lines, seconds


def build(v, flute_length=z1.FLUTE_LENGTH):
    w, l, h, d = v["stock_width"], v["stock_length"], v["stock_height"], v["tool_dia"]
    path, step = toolpath.raster(w, l, d, v["stepover"])
    zs = toolpath.equal_levels(v["top_margin"], v["target_z"], v["pass_depth"])
    probe, seconds = probe_block(v)
    body, seconds = z1.face(z1.to_program(v["origin"], w, l, path), zs, v["rpm"], v["top_margin"] + z1.APPROACH,
                            v["plunge_feed"], v["feed"], seconds)

    z0 = (
        f"Z0 is probed on the highest of {v['probe_grid_x'] * v['probe_grid_y']} grid points (needs the 3D probe, T0)."
        if probing(v)
        else "Set Z0 on the highest spot in Studio before running."
    )
    header = z1.start("surface-stock", (w, l, h), v["origin"], d, seconds, [
        f"; Stock {fmt(w)} x {fmt(l)} mm, origin {v['origin']}. {z0}",
        f"; Turn auto-leveling OFF. Cuts Z{fmt(v['top_margin'])} -> Z{fmt(v['target_z'])}"
        f" in {len(zs)} passes of {fmt((v['top_margin'] - v['target_z']) / len(zs))} mm, {fmt(step)} mm stepover, {fmt(d)} mm tool.",
        f"; Clamps must sit below the finished surface: {toolpath.reach(v['tool_dia'])}.",
    ], probe=probe, flute_length=flute_length)
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
        *z1.z_probe(*pts[0]),
        "M498",
    ]
    for x, y in pts[1:]:
        measure += [f"G0 Z{fmt(c)}", f"G0 X{fmt(x)} Y{fmt(y)}", f"G38.2 Z-{fmt(2 * c)} F{z1.PROBE_FEED}", "G10 L20 P0 Z0", "M498"]
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
    flute_length = cli.take_tool(v, args)
    check(v)
    if args.probe_only:
        if not probing(v):
            raise SystemExit("surface-stock: --probe-only needs probe_grid_x and probe_grid_y above 0")
        out = args.out or HERE / "surface-stock-probe-test.nc"
        out.write_text(build_probe_test(v))
        print(f"wrote {out}: upload and run it with the 3D probe, Auto leveling off")
        return
    program, passes, seconds = build(v, flute_length)
    out = args.out or HERE / "surface-stock.nc"
    out.write_text(program)
    print(f"wrote {out} ({passes} passes, ~{seconds / 60:.0f} min, removes {fmt(-v['target_z'])} mm below the highest point)")
    print(f"clamps must sit below the finished surface: {toolpath.reach(v['tool_dia'])}")
    if probing(v):
        print("corner-probe X/Y in Studio, start with Auto leveling off; the job probes Z itself")
    else:
        print("set Z0 on the highest spot in Studio and start with Auto leveling off")
    print("upload it to the Z1 from Makera Studio")


if __name__ == "__main__":
    main()
