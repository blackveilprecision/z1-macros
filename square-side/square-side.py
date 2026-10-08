#!/usr/bin/env python3
"""Cut the uneven side of the stock straight, half its height at a time, on the Makera Z1.

It works from probe-stock's reference (../probe-stock): the side across from the
origin in X (the right side for a left origin), touched with the probe rod. It
cuts that side to the narrowest width probed, rounded down to round_to, from
the top down to about half the stock's thickness: roughing in layers from the
top that leave finish_allowance on the wall, then one full-depth finishing pass
that climbs along the side. Clamps beside the side stay below the cut.

Then flip the stock front to back, push it into the same corner, probe it again
with probe-stock and run this again with --final-width set to the width the first
run printed, so the other half comes out the same; the two overlap by `overlap`.

Edit VARIABLES below (or pass flags, see --help), upload the .nc to the Z1
from Makera Studio and start it with Auto leveling OFF.
"""

import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE.parent / "probe-stock")]
from shared import cli, toolpath, z1  # noqa: E402
from shared.z1 import fmt  # noqa: E402
import stockref  # noqa: E402

# --- VARIABLES (dimensions are POSITIVE values; the stock's come from probe-stock) ---
VARIABLES = {
    "final_width": 0.0,       # Width to leave, from the origin's side; 0 = the narrowest probed, rounded down to round_to
    "round_to": 0.5,          # Round the narrowest probed width down to a multiple of this
    "depth": 0.0,             # How far down the side to cut; 0 = half the thickness plus half of overlap
    "overlap": 1.0,           # The cuts before and after flipping overlap this much
    "tool_dia": 6.35,         # WEXWE 1/4" 3 flute (tool-library); --tool takes it and its feeds from there
    "flute_length": 25.4,     # The finishing pass cuts with the flutes the whole depth down
    "pass_depth": 0.2,        # Depth of each roughing layer
    "stepover": 4.0,          # Most material across one roughing pass takes; more is taken in several
    "finish_allowance": 0.2,  # Roughing stops this far outside the final width; one full-depth pass takes it
    "rpm": 12000,
    "feed": 500,              # mm/min, roughing and finishing
    "plunge_feed": 200,       # mm/min; plunges happen off the stock
    "clamp_height": 10.0,     # Tallest clamp beside the side, above the bed; the cutter stays CLAMP_CLEARANCE above it
}

CLAMP_CLEARANCE = 2.0
ANCHOR_HEIGHT = 5.0    # Makera's anchor plate, which the cutter passes over at the front
AIR_MARGIN = 0.3       # Allowance for the side between the probed points when counting roughing passes
STICK_OUT = 5.0        # The cutter has to stick out of the collet the depth plus this
TIP_SLACK = stockref.TIP_SLACK  # The rod's tip and the cut's bottom may be this far apart; see plan()
JOB = "square-side.nc"


def parse_args():
    p = cli.parser(__doc__)
    cli.add_variables(p, VARIABLES)
    cli.add_log(p)
    cli.add_tool(p, "tool_dia, flute_length, rpm, feeds, pass_depth and stepover")
    p.add_argument("-o", "--out", type=Path)
    return p.parse_args()


def check(v):
    errors = cli.positive(v, ("round_to", "tool_dia", "flute_length", "pass_depth", "stepover"))
    for name in ("final_width", "depth", "overlap", "finish_allowance", "clamp_height"):
        if v[name] < 0:
            errors.append(f"{name} can't be negative")
    if v["stepover"] >= v["tool_dia"]:
        errors.append("stepover must be smaller than tool_dia")
    if v["finish_allowance"] >= v["stepover"]:
        errors.append("finish_allowance must be smaller than stepover")
    cli.stop("square-side", errors + cli.feeds_and_rpm(v))


def plan(v, state):
    """The cut from the stock as it is now: widths along the side, the width to leave, and the Zs.

    Returns (rows, final, thickness, z_top, z_bottom, roughing offsets, sign): rows are (Y, width,
    tip above the bed) for each probed point; Zs are in the job's coordinates (from Z0 now); each
    roughing offset is how far outside the final width a pass leaves the side; sign is +1 when the
    side is on the right of the origin, -1 on the left.
    """
    ox, oy = z1.ORIGINS[state.origin]
    if ox == 0.5:
        raise SystemExit("square-side: the stock was probed from its center; it needs a corner origin")
    sides = state.faces.get("x") or []
    if not sides:
        raise SystemExit(
            "square-side: the reference has no side touches. Probe it with the rod first, for example"
            " ../probe-stock/probe-stock.py job --corner --side-x-points 5, then save it"
        )
    rows = []
    for t, tip, u in sides:
        x, y = state.program(u, t)
        rows.append((y, abs(x), tip - state.bed))
    narrowest = min(w for _, w, _ in rows)
    final = v["final_width"] or math.floor(narrowest / v["round_to"] + 1e-9) * v["round_to"]
    widest = max(w for _, w, _ in rows)
    if final >= widest:
        raise SystemExit(f"square-side: the side is {widest:.3f} wide at most; a final width of {fmt(final)} cuts nothing")

    top = max(h for _, _, h in state.top)
    thickness = top - state.bed
    depth = v["depth"] or thickness / 2 + v["overlap"] / 2
    if depth >= thickness:
        raise SystemExit(f"square-side: depth {fmt(depth)} is the whole {thickness:.2f} mm thickness")
    lowest = thickness - depth
    floor = max(v["clamp_height"], ANCHOR_HEIGHT) + CLAMP_CLEARANCE
    if lowest < floor - 1e-9:
        raise SystemExit(
            f"square-side: cutting {fmt(depth)} down leaves the cutter {lowest:.2f} mm above the bed, under"
            f" {fmt(floor)}: {fmt(CLAMP_CLEARANCE)} mm above the {fmt(max(v['clamp_height'], ANCHOR_HEIGHT))} mm"
            " clamps it passes over. Cut less deep (--depth), and flip the stock to cut the rest"
        )
    if depth > v["flute_length"] - 1:
        raise SystemExit(
            f"square-side: the finishing pass cuts {fmt(depth)} mm down the side, more than the"
            f" {fmt(v['flute_length'])} mm flutes allow; cut less deep (--depth) or use a longer cutter"
        )
    excess = widest - final + AIR_MARGIN - v["finish_allowance"]
    n = max(1, math.ceil(excess / v["stepover"] - 1e-9))
    offsets = [v["finish_allowance"] + excess * (n - 1 - k) / n for k in range(n)]
    z_top = top - state.z0
    return rows, final, thickness, z_top, z_top - depth, offsets, 1.0 if ox == 0 else -1.0


def build(v, state, note, final, z_top, z_bottom, offsets, sign):
    """The job: roughing layers that zig-zag along the side, then one full-depth finishing pass that climbs.

    Every pass starts and ends beyond the front or back of the stock, so the cutter
    only goes down where there's nothing under it and meets the side from the end.
    """
    ox, oy = z1.ORIGINS[state.origin]
    r = v["tool_dia"] / 2
    y0, y1 = -oy * state.length - r - z1.LEAD, (1 - oy) * state.length + r + z1.LEAD  # LEAD past the front and back
    x_at = lambda offset: sign * (final + offset + r)  # noqa: E731
    # Climb milling on a clockwise spindle: along +Y with the side to the cutter's left (-X), -Y to its right
    climb = (y0, y1) if sign > 0 else (y1, y0)
    zs = toolpath.equal_levels(z_top, z_bottom, v["pass_depth"])
    safe_z, approach = z_top + z1.SAFE_Z, z_top + z1.APPROACH  # from the top

    seconds = 0.0
    at_y = y0
    body = z1.spindle_on(x_at(offsets[0]), at_y, v["rpm"], safe_z, approach)
    z_prev = approach
    for n, z in enumerate(zs, 1):
        body.append(f"; Layer {n}/{len(zs)} Z{fmt(z)}")
        body.append(f"G1 Z{fmt(z)} F{v['plunge_feed']}")
        seconds += abs(z_prev - z) / v["plunge_feed"] * 60
        for k, offset in enumerate(offsets):
            if k:  # step in to the next pass at the end, beyond the stock
                body.append(f"G1 X{fmt(x_at(offset))} F{v['feed']}")
                seconds += abs(offsets[k - 1] - offset) / v["feed"] * 60
            at_y = y1 if at_y == y0 else y0
            body.append(f"G1 Y{fmt(at_y)} F{v['feed']}")
            seconds += (y1 - y0) / v["feed"] * 60
        if len(offsets) > 1 and n < len(zs):  # back out to the first pass for the next layer
            body.append(f"G1 X{fmt(x_at(offsets[0]))} F{v['feed']}")
        z_prev = z

    body.append("; Finishing pass, the full depth, climbing")
    body += [f"G0 Z{fmt(approach)}", f"G0 X{fmt(x_at(0))} Y{fmt(climb[0])}", f"G1 Z{fmt(z_bottom)} F{v['plunge_feed']}",
             f"G1 Y{fmt(climb[1])} F{v['feed']}"]
    seconds += (approach - z_bottom) / v["plunge_feed"] * 60 + (y1 - y0) / v["feed"] * 60 + (y1 - y0) / z1.MAX_FEED * 60
    body += z1.end(safe_z)

    w, l, d = state.width, state.length, v["tool_dia"]
    thickness = z_top + state.z0 - state.bed
    header = z1.start("square-side", (w, l, thickness), state.origin, d, seconds, [
        f"; {note}",
        f"; No probing: Z0 stays where it is, G54 Z {state.machine.z:.3f}. Turn auto-leveling OFF.",
        f"; {len(zs)} roughing layers of <= {fmt(v['pass_depth'])} mm, {len(offsets)} pass(es) each, leaving"
        f" {fmt(v['finish_allowance'])} mm; then one full-depth finishing pass. Every pass starts beyond the stock.",
    ], g54_z=state.machine.z, flute_length=v["flute_length"])
    return "\n".join(header + body) + "\n", len(zs), seconds


def main():
    args = parse_args()
    v = {name: getattr(args, name) for name in VARIABLES}
    if args.tool:
        tool, preset = cli.apply_tool(v, args)
        print(
            f"tool: {tool.description} ({tool.source}), {preset.name}: {fmt(v['tool_dia'])} mm, {fmt(v['flute_length'])} mm"
            f" flutes, {v['rpm']} rpm, {v['feed']} mm/min, plunge {v['plunge_feed']}, {fmt(v['pass_depth'])} mm layers"
        )
    check(v)

    state = stockref.load(args.log)
    state.check("square-side")
    rows, final, thickness, z_top, z_bottom, offsets, sign = plan(v, state)
    side = "right" if sign > 0 else "left"
    print(f"Probe run {state.probe['stamp']}: the {side} side, touched with the rod (widest from the tip up to the top):")
    print(f"  {'Y':>7} {'width':>8} {'to cut':>7} {'tip above bed':>14}")
    for y, width, tip in rows:
        print(f"  {y:7.1f} {width:8.3f} {width - final:7.3f} {tip:14.3f}")
    depth = z_top - z_bottom
    lowest = thickness - depth
    tip = max(t for _, _, t in rows)
    if tip > lowest + TIP_SLACK:
        print(f"The rod touched the side {tip - lowest:.1f} mm above the bottom of the cut. Below its tip the side is taken"
              f" to be no wider than it read; each {fmt(v['pass_depth'])} mm layer takes whatever is there.")
    given = "--final-width" if v["final_width"] else f"the narrowest, {min(w for _, w, _ in rows):.3f}, rounded down"
    print(f"Width to leave: {fmt(final)} mm ({given}).")
    print(f"Thickness {thickness:.3f} mm: cutting {depth:.2f} mm down from the top, to {lowest:.2f} mm above the bed.")
    note = (f"The {side} side to {fmt(final)} mm wide, {depth:.2f} mm down from the top ({lowest:.2f} mm above the bed),"
            f" from the probe run of {state.probe['stamp']}.")

    program, count, seconds = build(v, state, note, final, z_top, z_bottom, offsets, sign)
    out = args.out or HERE / JOB
    out.write_text(program)
    stockref.record(out, "cut", f"{side} side cut to {fmt(final)} mm wide, {depth:.2f} mm down",
                    effect={"type": "side", "axis": "x", "width": round(final, 3), "bottom": round(z_bottom + state.z0, 3)})
    print(f"wrote {out} ({count} layers, ~{seconds / 60:.0f} min)")
    print(f"the cutter has to stick out of the collet more than {fmt(math.ceil(depth + STICK_OUT))} mm;"
          f" clamps beside the side must be under {lowest - CLAMP_CLEARANCE:.1f} mm")
    print(f"upload it and start it with Auto leveling off. Before the cutter goes in, Studio's log should show"
          f" G54 Z {state.machine.z:.3f}: stop the job if not")
    print("after the first half: flip the stock front to back, push it into the same corner, probe it again"
          f" (probe-stock) and run this again with --final-width {fmt(final)} for the other half")


if __name__ == "__main__":
    main()
