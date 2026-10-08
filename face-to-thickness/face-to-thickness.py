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

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from shared import cli, toolpath, z1  # noqa: E402
from shared.z1 import fmt  # noqa: E402

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


def parse_args():
    p = cli.parser(__doc__)
    cli.add_variables(p, VARIABLES, {"origin": z1.ORIGINS})
    cli.add_tool(p)
    p.add_argument("-o", "--out", type=Path)
    p.add_argument("--no-probe", action="store_true", help="skip probing; set Z0 on the top in Studio before running")
    return p.parse_args()


def check(v):
    errors = cli.positive(v, ("stock_width", "stock_length", "stock_height", "final_height", "tool_dia", "stepover", "pass_depth"))
    if round(v["stock_height"] - v["final_height"], 3) <= 0:  # as the passes count it
        errors.append("final_height must be less than stock_height")
    if v["stepover"] >= v["tool_dia"]:
        errors.append("stepover must be smaller than tool_dia or ridges are left between passes")
    cli.stop("face-to-thickness", errors + cli.feeds_and_rpm(v))


def probe_block(v):
    """Set Z0 on the middle of the top, the same way Studio's own Z probe does."""
    [(x, y)] = z1.to_program(v["origin"], v["stock_width"], v["stock_length"], [(v["stock_width"] / 2, v["stock_length"] / 2)])
    return [
        "; Probe the middle of the top with the 3D probe and set Z0 there",
        "T0 M6",
        *z1.z_probe(x, y),
        f"G0 Z{fmt(z1.SAFE_Z)}",
        "",
    ]


def build(v, probe, flute_length=z1.FLUTE_LENGTH):
    w, l, h, d = v["stock_width"], v["stock_length"], v["stock_height"], v["tool_dia"]
    depth = h - v["final_height"]
    path, step = toolpath.raster(w, l, d, v["stepover"])
    zs = toolpath.equal_levels(0.0, -depth, v["pass_depth"])  # from Z0 to the final surface
    seconds = z1.PROBE_SECONDS if probe else 0.0
    body, seconds = z1.face(z1.to_program(v["origin"], w, l, path), zs, v["rpm"], z1.APPROACH, v["plunge_feed"], v["feed"], seconds)

    z0 = (
        "Z0 is probed in the middle of the top (needs the 3D probe, T0)."
        if probe
        else "Set Z0 on the top in Studio before running."
    )
    header = z1.start("face-to-thickness", (w, l, h), v["origin"], d, seconds, [
        f"; Stock {fmt(w)} x {fmt(l)} x {fmt(h)} mm, origin {v['origin']}. {z0}",
        f"; Turn auto-leveling OFF. Faces {fmt(h)} -> {fmt(v['final_height'])} mm thick: Z0 -> Z{fmt(-depth)}"
        f" in {len(zs)} passes of {fmt(depth / len(zs))} mm, {fmt(step)} mm stepover, {fmt(d)} mm tool.",
        f"; Clamps and vise jaws must sit below {fmt(v['final_height'])} mm: {toolpath.reach(v['tool_dia'])}.",
    ], probe=probe_block(v) if probe else (), flute_length=flute_length)
    return "\n".join(header + body) + "\n", len(zs), seconds


def main():
    args = parse_args()
    v = {name: getattr(args, name) for name in VARIABLES}
    flute_length = cli.take_tool(v, args)
    check(v)
    program, passes, seconds = build(v, not args.no_probe, flute_length)
    out = args.out or HERE / "face-to-thickness.nc"
    out.write_text(program)
    depth = v["stock_height"] - v["final_height"]
    print(
        f"wrote {out} ({passes} passes of {fmt(depth / passes)} mm, ~{seconds / 60:.0f} min,"
        f" {fmt(v['stock_height'])} -> {fmt(v['final_height'])} mm thick)"
    )
    print(f"clamps and vise jaws must sit below {fmt(v['final_height'])} mm: {toolpath.reach(v['tool_dia'])}")
    if args.no_probe:
        print("set Z0 on the top in Studio and start with Auto leveling off")
    else:
        print("corner-probe X/Y in Studio, start with Auto leveling off; the job probes Z itself")
    print("upload it to the Z1 from Makera Studio")


if __name__ == "__main__":
    main()
