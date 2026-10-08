#!/usr/bin/env python3
"""Probe the stock once, for every macro that cuts from it.

1. `probe-stock.py job` writes probe-stock.nc. On metal stock it can start with
   the probe rod (T9999): the stock's sides and its corner, for its size and
   X0/Y0. Then the probe (T0) touches the anchor plate (for the bed's height)
   and a grid on the top. --top-only leaves out the rod and --side-only the
   probe; with --probe-rod-only the rod does it all. Each touch sets Z0, X0 or Y0
   there and M498 prints it to Studio's log. No spindle, no cutting.
2. `probe-stock.py save` reads that run back from Studio's log into stock.json;
   a --top-only or --side-only run keeps the rest from the saved one.
   `probe-stock.py update` saves a newer run too, and writes every job that
   finished since into stock.json.
3. Macros such as surface-to-lowest-point write their jobs from stock.json, and
   note what each one will change. Once Studio's log shows a job finished, the
   change counts: `probe-stock.py show` prints the stock as it is now.

`probe-stock.py where` writes a job that only prints where X0/Y0/Z0 are, for
after something else may have moved them. Run Studio's corner probe for X/Y
first (or touch the corner here with --corner), and start every job with Auto
leveling OFF.

Edit VARIABLES below (or pass flags, see `probe-stock.py job --help`) and upload
each .nc to the Z1 from Makera Studio.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parent), str(HERE)]
from shared import cli, toolpath, z1  # noqa: E402
from shared.z1 import fmt  # noqa: E402
import stockref  # noqa: E402
from stockref import PROBE_JOB, WHERE_JOB  # noqa: E402

# --- VARIABLES (dimensions are POSITIVE values) ---
VARIABLES = {
    "stock_width": 65.4,     # X-axis dimension of stock (the widest, if a side is uneven)
    "stock_length": 50.4,    # Y-axis dimension of stock
    "stock_height": 29.0,    # Thickness, roughly: checks the anchor plate reading, and keeps the rod above the clamps and the plate
    "origin": "topFrontLeft",  # Studio origin corner: front-left, where the stock's square edges are
    "fence_x": -7.5,         # A point on the anchor plate's top, in job coordinates (from the origin corner):
    "fence_y": 77.0,         #   left arm, between a screw hole and the dowel. --no-fence to skip it
    "fence_height": 5.0,     # Height of the anchor plate's top above the bed the stock sits on
    "probe_grid_x": 5,       # Points on the top along X
    "probe_grid_y": 4,       # Points on the top along Y
    "probe_inset": 3.0,      # Keep points this far inside the stock's edges
    "probe_clearance": 5.0,  # Lift between points; must exceed highest minus lowest thickness
    "side_x_points": 0,      # Points on the X side across from the origin (the right side for a left origin), rod only
    "side_y_points": 0,      # Points on the Y side across from the origin (the back for a front origin), rod only
    "side_depth": 2.0,       # Rod tip this far below the top for those; it touches the side wherever it sticks out most above
    "side_clearance": 5.0,   # Rod comes down this far outside a side across from the origin, then searches up to twice that; at most 5
    "rod_dia": 2.0,          # The probe rod's diameter where it touches (Studio's corner probe sets X/Y 1 mm from the touch)
    "rod_tool": 9999,        # The probe rod's tool number; not the probe's T0, so the job can ask for the swap
    "clamp_height": 10.0,    # Tallest clamp beside the sides (0 = none): side touches stay above it
}

SIDE_FAST = 100      # Studio's corner probe: fast touch, back off, slow touch
SIDE_SLOW = 50
BACK_OFF = 1.0
CORNER_DEPTH = 2.0   # Studio's corner probe: from the top touch, out CORNER_OUT, down CORNER_DEPTH, feed in
CORNER_OUT = 10.0
CLEARANCE = 1.0      # Rod tip at least this far above a clamp or the anchor plate
FENCE_CHECK = 2.0    # A thickness from the anchor plate this far from stock_height means the probe missed the plate
TOP_MOVED = 0.2      # A --side-only rod reading the saved top this far off: the stock sits higher or lower. The rod and
                     # the probe read the same point 0.03 mm apart on the machine
PLATE_SIDES = ("left side", "front")  # beside Makera's anchor plate, front-left; the clamps are on the right and back

PROBE_TOOL = 0       # The probe. A tool change to the tool already in does nothing, so the rod needs another number
SPECIAL = {PROBE_TOOL: "the probe", 8888: "the laser"}  # tool numbers the rod can't take

NAMES = {("x", 0.0): "right side", ("x", 1.0): "left side", ("y", 0.0): "back", ("y", 1.0): "front"}  # across from the origin


def parse_args():
    p = cli.parser(__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    job = sub.add_parser("job", help=f"write the probe job, {PROBE_JOB}", allow_abbrev=False)  # not inherited
    cli.add_variables(job, VARIABLES, {"origin": z1.ORIGINS})
    job.add_argument("--no-fence", action="store_true", help="don't touch the anchor plate; the highest point is taken to be --stock-height thick")
    job.add_argument("--corner", action="store_true", help="touch the origin corner's two sides with the rod and set X0/Y0 there, as Studio's corner probe does")
    part = job.add_mutually_exclusive_group()
    part.add_argument("--top-only", action="store_true", help="only the anchor plate and the top; save keeps the saved side touches")
    part.add_argument("--side-only", action="store_true", help="only the rod's side and corner touches; save keeps the saved top")
    job.add_argument("--probe-rod-only", action="store_true", help="the rod does the top and the anchor plate too: one tool, metal stock only")
    job.add_argument("-o", "--out", type=Path)
    for name, text in (("save", "read the last probe run from Studio's log into stock.json"),
                       ("update", "bring stock.json up to date: save a newer probe run, and write in every job that finished"),
                       ("show", "print the stock as it is now"),
                       ("where", f"write {WHERE_JOB}, which only prints X0/Y0/Z0 to Studio's log")):
        s = sub.add_parser(name, help=text, allow_abbrev=False)
        if name == "where":
            s.add_argument("-o", "--out", type=Path)
        else:
            cli.add_log(s)
    return p.parse_args()


def check(v, corner, rod_only=False):
    errors = cli.positive(v, ("stock_width", "stock_length", "stock_height", "probe_clearance", "side_depth", "side_clearance",
                              "rod_dia", "fence_height"))
    if v["probe_inset"] < 0 or v["clamp_height"] < 0:
        errors.append("probe_inset and clamp_height can't be negative")
    if min(v["probe_grid_x"], v["probe_grid_y"]) < 1:
        errors.append("probe_grid_x and probe_grid_y must be at least 1: Z0 is set on the top")
    if min(v["side_x_points"], v["side_y_points"]) < 0:
        errors.append("side_x_points and side_y_points can't be negative")
    sides = v["side_x_points"] or v["side_y_points"]
    if (sides or corner) and v["origin"] == "topCenter":
        errors.append("touching the sides needs a corner origin: X0/Y0 are set back on the origin's sides afterwards")
    elif sides or corner:  # each touch against what sits beside the side it touches; --no-fence leaves the plate there
        for axis, count in (("x", v["side_x_points"]), ("y", v["side_y_points"])):
            o = z1.ORIGINS[v["origin"]][axis == "y"]
            for side, depth, cause, touched in (
                (NAMES[(axis, o)], v["side_depth"], f"side_depth {fmt(v['side_depth'])}", count),
                (NAMES[(axis, 1.0 - o)], CORNER_DEPTH, f"the corner touch {fmt(CORNER_DEPTH)} mm down", count or corner),
            ):
                below, what = (v["fence_height"], "anchor plate") if side in PLATE_SIDES else (v["clamp_height"], "clamps")
                tip = v["stock_height"] - depth
                if touched and below and tip < below + CLEARANCE:
                    errors.append(f"{cause} puts the rod tip {fmt(tip)} mm above the bed beside the {side},"
                                  f" not {fmt(CLEARANCE)} mm above the {fmt(below)} mm {what} there")
    if sides and v["side_depth"] >= v["stock_height"]:
        errors.append("side_depth must be less than stock_height")
    if (sides or corner) and v["stock_height"] <= CORNER_DEPTH:
        errors.append(f"stock_height must be more than {fmt(CORNER_DEPTH)}: the corner touches go that far down")
    if sides and v["side_clearance"] > CORNER_OUT / 2:
        errors.append(f"side_clearance can't be more than {fmt(CORNER_OUT / 2)}: the rod searches twice that, and the"
                      f" searches are kept within the corner touches' {fmt(CORNER_OUT)} mm (see the README)")
    if (sides or corner or rod_only) and (v["rod_tool"] in SPECIAL or v["rod_tool"] < 0):
        errors.append(f"rod_tool can't be T{v['rod_tool']}, {SPECIAL.get(v['rod_tool'], 'no tool')}")
    cli.stop("probe-stock", errors)


def studio_probe(x, y, fast=z1.PROBE_FAST):
    """Touch a point the way Studio's own Z probe does, and print G54 to Studio's log.

    Studio comes down at 500 mm/min with the probe; the rod is rigid, and its corner probe takes it down at 100.
    """
    return [*z1.z_probe(x, y, fast, g91=True), "M498 ; print G54 to Studio's log"]


def side_touch(v, axis, face, out, cross, depth, gap):
    """Touch a side with the rod from outside it, the way Studio's corner probe does, and set that axis there.

    face: the side's position in job coordinates if the stock were exactly its size; out: +1 or -1, the
    way out of the stock. The axis is set to face + out * radius at the touch, so the print is where the
    origin would be if the side were exactly at `face`: how far it is from there is how far the side is
    from `face`. A side touched at the origin (face 0) puts X0 or Y0 on it. The rod comes down gap outside
    the side and searches up to twice that.
    """
    r, c = v["rod_dia"] / 2, v["probe_clearance"]
    start = face + out * (r + gap)
    xy = f"X{fmt(start)} Y{fmt(cross)}" if axis == "X" else f"X{fmt(cross)} Y{fmt(start)}"
    return [
        f"G0 Z{fmt(c)}",
        f"G0 {xy}",
        f"G1 Z{fmt(-depth)} F{z1.PROBE_FEED}",
        f"G91 G38.2 {axis}{fmt(-out * 2 * gap)} F{SIDE_FAST}",
        f"G91 G0 {axis}{fmt(out * BACK_OFF)}",
        f"G38.2 {axis}{fmt(-out * 2 * gap)} F{SIDE_SLOW}",
        f"G10 L20 P0 {axis}{fmt(face + out * r)}",
        "M498",
        f"G91 G0 {axis}{fmt(out * BACK_OFF)}",
        "G90",
    ]


def top_touch(v, x, y, search):
    """Touch a top point from probe_clearance above Z0: fast to the touch, back off, then slow, as Studio's Z
    probe does (a faster touch stops lower), and set Z0 there."""
    c = v["probe_clearance"]
    return [
        f"G0 Z{fmt(c)}", f"G0 X{fmt(x)} Y{fmt(y)}", f"G91 G38.2 Z-{fmt(2 * c)} F{search}",
        f"G91 G0 Z{fmt(BACK_OFF)}", f"G38.2 Z-{fmt(2 * BACK_OFF)} F{z1.PROBE_SLOW}", "G10 L20 P0 Z0", "G90", "M498",
    ]


def probe_job(v, fenced, corner, only=None, rod_only=False):
    """The probe job's G-code and its plan: what each M498 it prints measured, in order.

    With side or corner touches, the probe rod goes in first. It sets Z0 on the
    top at the first grid point, so the side touches go to the right depth, then
    touches the sides. A side touched across from the origin moves X0 (or Y0) by
    however far that side is from where the stock's size puts it, so once those
    are done the origin's own side is touched to put it back. Then the probe goes
    in for the anchor plate and the top: Z0 is set on each top point in turn and
    ends on the last. With rod_only the rod does all of it, the anchor plate first,
    and every touch on the top comes down at the rod's speed.

    only: "top" leaves out the rod's side and corner touches, "sides" leaves out the
    anchor plate and the top grid; probe-stock.py save keeps the rest from the
    saved reference.

    Every tool is measured on the tool setter at its tool change, and every
    height is kept above the tool setter (G54 Z less REFMZ), so they line up.
    Every G38.2 is written after G91. The firmware takes its distances as
    relative either way, but Studio's preview reads them as positions in G90 and
    sends the machine to the lowest X/Y it finds before the job starts: `G38.2
    Y-20` sent it to Y-20, past the soft limit.
    G38.2 alarms if it touches nothing, which stops the job instead of logging a
    wrong value. Each G10 is one write to the machine's EEPROM, as with Studio's
    own probes.
    """
    ox, oy = z1.ORIGINS[v["origin"]]
    touches, lines = [], []
    pts = toolpath.probe_grid(v["origin"], v["stock_width"], v["stock_length"], v["probe_grid_x"], v["probe_grid_y"], v["probe_inset"])
    xy = z1.to_program(v["origin"], v["stock_width"], v["stock_length"], pts)  # the same points in the job's coordinates
    sides = (v["side_x_points"] or v["side_y_points"] or corner) and only != "top"
    tops = only != "sides"
    plate = fenced and tops

    def side_and_corner_touches():
        out = []
        for axis, o, size, across, count in (("x", ox, v["stock_width"], v["stock_length"], v["side_x_points"]),
                                             ("y", oy, v["stock_length"], v["stock_width"], v["side_y_points"])):
            if not (count or corner):
                continue
            near_out = -1.0 if o == 0 else 1.0
            cross_origin = (oy if axis == "x" else ox) * across
            if count:
                far = -near_out * size
                out.append(f"; The {NAMES[(axis, o)]}, {fmt(v['side_depth'])} mm down")
                for s in toolpath.spread(across, count, v["probe_inset"]):
                    out.extend(side_touch(v, axis.upper(), far, -near_out, s - cross_origin, v["side_depth"], v["side_clearance"]))
                    touches.append({"kind": "side", "axis": axis, "at": s, "depth": v["side_depth"], "face": far})
            near = NAMES[(axis, 1.0 - o)]
            out.append(f"; {axis.upper()}0 {'back ' if count else ''}on the {near}")
            near_corner = pts[0][1] if axis == "x" else pts[0][0]  # beside the first top touch, like Studio's corner probe
            out.extend(side_touch(v, axis.upper(), 0.0, near_out, near_corner - cross_origin, CORNER_DEPTH, CORNER_OUT))
            touches.append({"kind": "origin", "axis": axis})
        return out

    if rod_only or (sides and not tops):
        # One tool, the rod: the anchor plate, the first top point (Z0 for the side depths), the sides, the rest of the top
        lines += ["; The probe rod for everything, on metal stock only" if tops else "; The probe rod: the sides and the corner",
                  f"T{v['rod_tool']} M6"]
        if plate:
            touches.append({"kind": "fence"})
            lines += ["; The anchor plate's top, fence_height above the bed", *studio_probe(v["fence_x"], v["fence_y"], fast=SIDE_FAST)]
        lines += ["; The top beside the corner: Z0 for the side touches' depth", *studio_probe(*xy[0], fast=SIDE_FAST)]
        touches.append({"kind": "top" if tops else "rod_top", "u": pts[0][0], "t": pts[0][1]})
        if sides:
            lines += side_and_corner_touches()
        if tops:
            lines.append("; The rest of the top")
            for x, y in xy[1:]:
                lines += top_touch(v, x, y, SIDE_FAST)
            touches += [{"kind": "top", "u": u, "t": t} for u, t in pts[1:]]
    else:
        if sides:
            lines += ["; The probe rod: the sides and the corner", f"T{v['rod_tool']} M6",
                      "; Z0 on the top, for the side touches' depth", *studio_probe(*xy[0], fast=SIDE_FAST)]
            touches.append({"kind": "rod_top", "u": pts[0][0], "t": pts[0][1]})
            lines += side_and_corner_touches()
            lines.append(f"G0 Z{fmt(z1.SAFE_Z)}")
        lines += ["; The probe: the anchor plate and the top", f"T{PROBE_TOOL} M6"]
        if plate:
            touches.append({"kind": "fence"})
            lines += ["; The anchor plate's top, fence_height above the bed", *studio_probe(v["fence_x"], v["fence_y"])]
        lines += ["; The top", *studio_probe(*xy[0])]
        for x, y in xy[1:]:
            lines += top_touch(v, x, y, z1.PROBE_FEED)
        touches += [{"kind": "top", "u": u, "t": t} for u, t in pts]

    named = [f"{n} on the {NAMES[(a, o)]}" for a, o, n in (("x", ox, v["side_x_points"]), ("y", oy, v["side_y_points"])) if n and sides]
    origins = [t["axis"] for t in touches if t["kind"] == "origin"]
    origin_text = ["the corner"] if len(origins) == 2 else [f"{a.upper()}0 on the {NAMES[(a, 1.0 - (ox if a == 'x' else oy))]}" for a in origins]
    what = ", ".join([*named, *origin_text, *(["the anchor plate"] if plate else []),
                      f"{len(pts)} points on the top" if tops else "the top beside the corner"])
    tools = (f"the probe rod (T{v['rod_tool']}) only" if rod_only or not tops
             else f"the probe rod (T{v['rod_tool']}), then the probe (T0)" if sides else "the probe (T0)")
    header = [
        "; probe-stock job, generated by probe-stock.py job - edit the script, not this file.",
        f"; Prints {what} to Studio's log with M498, with {tools}. No spindle, no cutting. Turn auto-leveling OFF.",
        "; Then run probe-stock.py save: it reads them from the log into stock.json for the macros.",
        *([f"; --{'top' if only == 'top' else 'side'}-only: save keeps the saved {'side touches' if only == 'top' else 'top'}."]
          if only else []),
        *(["; The probe rod only finds metal stock."] if sides or rod_only else []),
        "",
        "G90 G21",
        "M370 ; clear any auto-leveling grid left from an earlier job",
        "",
    ]
    lines += [f"G0 Z{fmt(z1.SAFE_Z)}", "G28", "M02"]
    plan = {
        "stock": {"width": v["stock_width"], "length": v["stock_length"], "height": v["stock_height"], "origin": v["origin"]},
        "fence": {"x": v["fence_x"], "y": v["fence_y"], "height": v["fence_height"]} if plate else None,
        "rod_dia": v["rod_dia"],
        "only": only,
        "touches": touches,
    }
    return "\n".join(header + lines) + "\n", plan, f"{what}, with {tools}"


def newest_probe_run(runs, log):
    """The newest probe run in `runs` (Studio's log, read), picked as stockref.fold() picks them."""
    by_md5 = {e["md5"]: e for e in stockref.jobs()}
    runs = [r for r in runs if stockref.is_probe(r, by_md5.get(r.md5))]
    if not runs:
        raise SystemExit(
            f"probe-stock: no probe run in {log}. Write the job (probe-stock.py job) and run it on the machine"
            " first, or pass --log if Studio keeps its logs elsewhere"
        )
    return runs[-1]


def measure(run, plan):
    """The reference stock.json keeps, from a probe run's prints and the plan of the job that made them."""
    prints, touches = run.prints(), plan["touches"]
    if len(prints) < len(touches):
        raise SystemExit(
            f"probe-stock: the probe run of {run.stamp} ({run.log}) logged {len(prints)} of {len(touches)} touches"
            f" and {'stopped: ' + run.end if run.end and run.end != 'finished' else 'ended early'}. Run it again"
        )
    s = plan["stock"]
    ox, oy = z1.ORIGINS[s["origin"]]
    pairs = list(zip(touches, prints))
    origin = {t["axis"]: (p.x if t["axis"] == "x" else p.y) for t, p in pairs if t["kind"] == "origin"}
    top = [[t["u"], t["t"], round(p.z0, 3)] for t, p in pairs if t["kind"] == "top"]
    faces = {"x": [], "y": []}
    for t, p in pairs:
        if t["kind"] == "side":  # see side_touch(): the print is the origin moved by how far the side is off `face`
            moved = (p.x if t["axis"] == "x" else p.y) - origin[t["axis"]]
            pos = t["face"] + moved + (ox * s["width"] if t["axis"] == "x" else oy * s["length"])
            faces[t["axis"]].append([t["at"], round(p.z0 - t["depth"], 3), round(pos, 3)])
    high = max((h for _, _, h in top), default=None)
    if not top:  # a --side-only run: save takes the top and the bed from the saved reference
        bed, bed_from = None, None
    elif plan["fence"]:
        fence = next(p for t, p in pairs if t["kind"] == "fence")
        bed = fence.z0 - plan["fence"]["height"]
        f = plan["fence"]
        bed_from = f"the anchor plate at X{fmt(f['x'])} Y{fmt(f['y'])}, {fmt(f['height'])} mm above the bed"
        thickest = high - bed
        if abs(thickest - s["height"]) > FENCE_CHECK:
            raise SystemExit(
                f"probe-stock: from the anchor plate, the highest point is {thickest:.2f} mm thick, but the job was"
                f" written for {fmt(s['height'])}. If the probe missed the plate it touched the bed and read"
                f" {fmt(f['height'])} mm low: check --fence-x/--fence-y. If the stock really is that thick, write the"
                " job again with --stock-height close to it"
            )
    else:
        bed = high - s["height"]
        bed_from = f"assumed: the highest point is {fmt(s['height'])} mm thick (no anchor plate touch)"
    last = prints[len(touches) - 1]
    rod = next(((t, p) for t, p in pairs if t["kind"] == "rod_top"), None)
    probe = next((p for t, p in pairs if rod and t["kind"] == "top" and (t["u"], t["t"]) == (rod[0]["u"], rod[0]["t"])), None)
    return {
        "format": 1,
        "probe": {"name": run.name, "stamp": run.stamp, "log": run.log, "line": run.line, "md5": run.md5},
        "stock": s,
        "rod_dia": plan.get("rod_dia"),
        "bed": None if bed is None else round(bed, 3),
        "bed_from": bed_from,
        "top": top,
        "faces": faces,
        "machine": {"x": last.x, "y": last.y, "z": last.z, "refmz": last.refmz, "stamp": last.stamp},
        "moved": {axis: round(origin[axis] - (prints[0].x if axis == "x" else prints[0].y), 3) for axis in origin},
        "rod_vs_probe": round(rod[1].z0 - probe.z0, 3) if probe else None,
    }


def merge(reference, plan, run, log, runs):
    """A --top-only or --side-only run, with the rest from the saved reference as it was just before the run:
    any job written here that finished since is counted. Refuses when the stock may have moved or been cut."""
    only, s = plan["only"], plan["stock"]
    try:
        before = stockref.load(log, until=(run.log, run.line), runs=runs)
    except SystemExit:
        if only == "sides":
            raise SystemExit("probe-stock: a --side-only run keeps the top from the saved reference, and there isn't one."
                             " Run a full probe job (or --top-only) first") from None
        return reference  # a --top-only run on its own: no side touches
    stop = "Run a full probe job instead (probe-stock.py job without --top-only or --side-only)"
    if before.unknown:
        raise SystemExit(f"probe-stock: {before.unknown} since the saved run; it may have cut the stock, so what this run"
                         f" didn't touch can't be kept. {stop}")
    if (before.origin, before.width, before.length) != (s["origin"], s["width"], s["length"]):
        raise SystemExit(f"probe-stock: this run was written for a different stock or origin than the saved one. {stop}")
    m = reference["machine"]
    corner = (m["x"], m["y"])  # a --top-only run leaves X0/Y0 alone; a --side-only run sets them on the corner
    dx, dy = corner[0] - before.origin_xy[0], corner[1] - before.origin_xy[1]
    if max(abs(dx), abs(dy)) > stockref.ORIGIN_MOVED:
        raise SystemExit(f"probe-stock: X0/Y0 are {dx:+.3f}, {dy:+.3f} mm from the saved run's: the stock (or the origin)"
                         f" moved, so the saved {'side touches' if only == 'top' else 'top'} no longer line up. {stop}")
    p = before.probe
    if only == "top":
        reference["origin_xy"] = list(before.origin_xy)  # the corner as the rod last found it
        reference["faces"] = {axis: [list(x) for x in pts] for axis, pts in before.faces.items()}
        reference["sides_from"] = p.get("sides_from") or p["stamp"]
    else:
        reference.update(top=[list(x) for x in before.top], bed=before.bed, bed_from=before.bed_from)
        reference["top_from"] = before.probe.get("top_from") or p["stamp"]
        rod = next((pr for t, pr in zip(plan["touches"], run.prints()) if t["kind"] == "rod_top"), None)
        saved = next((h for u, t, h in before.top if rod and (u, t) == (plan["touches"][0]["u"], plan["touches"][0]["t"])), None)
        if rod and saved is not None:
            reference["rod_vs_saved_top"] = round(rod.z0 - saved, 3)
            if abs(rod.z0 - saved) > TOP_MOVED:
                raise SystemExit(f"probe-stock: the rod read the top beside the corner {rod.z0 - saved:+.3f} mm from the saved"
                                 f" top there: the stock sits higher or lower, so the saved top no longer holds. {stop}")
    reference["probe"].update({k: reference.get(k) for k in ("top_from", "sides_from") if reference.get(k)})
    return reference


def save_newest(runs, log):
    """Save the newest probe run in Studio's log as stock.json; a --top-only or --side-only one is merged."""
    run = newest_probe_run(runs, log)
    entry = next((e for e in stockref.jobs() if e["md5"] == run.md5 and e["kind"] == "probe"), None)
    if entry is None:
        raise SystemExit(
            f"probe-stock: can't tell which {run.name} played at {run.stamp}: "
            + ("Studio's log has no upload of it before then." if run.md5 is None else "it isn't one written here.")
            + " Write the job again (probe-stock.py job), upload it and run it"
        )
    reference = measure(run, entry["plan"])
    if entry["plan"].get("only"):
        reference = merge(reference, entry["plan"], run, log, runs)
    stockref.save(reference)
    return reference


def update(log):
    """Bring stock.json up to date with Studio's log.

    A probe run newer than the saved one is saved (merged, for --top-only and
    --side-only). Then every job written here that finished since is written into
    stock.json, up to the last run after which X0/Y0/Z0 are known: anything after
    that (a job not written here, Studio's own probes) is still read from the log
    by the next macro, which stops on it as before.
    """
    runs = stockref.read_logs(log)
    path = stockref.where() / "stock.json"
    try:
        state = stockref.load(log, runs=runs)
    except SystemExit:
        state = None
    if state is None or state.newer_probe:
        try:
            print(f"saved the probe run of {save_newest(runs, log)['probe']['stamp']}")
        except SystemExit as e:
            if state is None:
                raise
            print(f"didn't save the newer probe run: {str(e).removeprefix('probe-stock: ')}")
    state = stockref.load(log, runs=runs)
    reference = json.loads(path.read_text(encoding="utf-8"))
    settled = state.settled
    anchor = reference.get("anchor") or reference["probe"]
    if settled and (settled["anchor"]["log"], settled["anchor"]["line"]) != (anchor["log"], anchor["line"]):
        new = settled["history"][len(reference.get("history", [])):]
        reference.update(settled, origin_xy=list(state.origin_xy))
        stockref.save(reference)
        for stamp, text in new:
            print(f"wrote in {stamp}  {text}")
        print(f"stock.json now runs up to {settled['anchor']['name']} at {settled['anchor']['stamp']}")
        state = stockref.load(log, runs=runs)
    else:
        print("stock.json is up to date")
    print()
    describe(state)


def describe(state):
    """Print the stock as it is now."""
    ox, oy = z1.ORIGINS[state.origin]
    p = state.probe
    print(f"Stock {fmt(state.width)} x {fmt(state.length)} mm, origin {state.origin}, probed {p['stamp']} ({p['log']})")
    for part, key in (("top", "top_from"), ("sides", "sides_from")):
        if p.get(key):
            print(f"  the {part} kept from the probe run of {p[key]}")
    print(f"Bed: {-state.bed:.3f} mm below the tool setter, from {state.bed_from}")
    highest, lowest = max(state.top, key=lambda p: p[2]), min(state.top, key=lambda p: p[2])
    if highest[2] - lowest[2] < 0.001:
        print(f"Top: flat, {highest[2] - state.bed:.3f} mm thick ({len(state.top)} points)")
    else:
        print(f"Top, {len(state.top)} points:")
        print(f"  {'X':>7} {'Y':>7} {'height':>8} {'thickness':>10}")
        for u, t, h in state.top:
            x, y = state.program(u, t)
            mark = "  highest" if (u, t, h) == highest else "  lowest" if (u, t, h) == lowest else ""
            print(f"  {x:7.1f} {y:7.1f} {h - highest[2]:8.3f} {h - state.bed:10.3f}{mark}")
    for axis, o in (("x", ox), ("y", oy)):
        pts = state.faces.get(axis) or []
        if not pts:
            continue
        print(f"The {NAMES[(axis, o)]}, touched with the rod (where it sticks out most, from the tip up to the top):")
        print(f"  {'Y' if axis == 'x' else 'X':>7} {'X' if axis == 'x' else 'Y':>8} {'size':>8} {'tip above bed':>14}")
        for s, tip, pos in pts:
            x, y = state.program(pos, s) if axis == "x" else state.program(s, pos)
            at, face = (y, x) if axis == "x" else (x, y)
            print(f"  {at:7.1f} {face:8.3f} {abs(face):8.3f} {tip - state.bed:14.3f}")
    m = state.machine
    print(f"G54 now: X {m.x:.3f} Y {m.y:.3f} Z {m.z:.3f} (printed {m.stamp}); Z0 is {state.z0 - state.bed:.3f} mm above the bed")
    for stamp, text in state.history:
        print(f"  {stamp}  {text}")
    for note in state.notes:
        print(f"note: {note}")
    for problem in (state.newer_probe and f"{state.newer_probe}: run probe-stock.py save",
                    state.unsure and f"X0/Y0/Z0 may have moved: {state.unsure}"):
        if problem:
            print(f"warning: {problem}")


def main():
    args = parse_args()
    if args.command == "where":
        out = args.out or HERE / WHERE_JOB
        out.write_text(
            "; Generated by probe-stock.py where - edit the script, not this file.\n"
            "; Prints X0/Y0/Z0 (G54) to Studio's log, so the macros know where they are.\n"
            "; Studio first moves to X0 Y0 at the top of Z travel; the job itself only prints, then goes to\n"
            "; clearance with G28, which is what Studio's log shows as the job finishing.\n"
            "M498\nG28\nM02\n"
        )
        stockref.record(out, "where", "printed G54")
        print(f"wrote {out}: upload it and run it, then run the macro again")
        return

    if args.command == "show":
        describe(stockref.load(args.log))
        return

    if args.command == "update":
        update(args.log)
        return

    if args.command == "save":
        runs = stockref.read_logs(args.log)
        reference = save_newest(runs, args.log)
        state = stockref.load(args.log, runs=runs)
        describe(state)
        moved = {a: d for a, d in reference["moved"].items() if abs(d) > 0.001}
        if moved:
            print("The corner touches moved " + ", ".join(f"{a.upper()}0 by {d:+.3f} mm" for a, d in moved.items()))
        if reference.get("rod_vs_saved_top") is not None:
            print(f"The rod read the top beside the corner {reference['rod_vs_saved_top']:+.3f} mm from the saved top there")
        if reference["rod_vs_probe"] is not None:
            print(f"The rod and the probe touched the same top point {abs(reference['rod_vs_probe']):.3f} mm apart"
                  " (how well the tool setter lines the two up)")
        print(f"saved {stockref.where() / 'stock.json'}")
        return

    v = {name: getattr(args, name) for name in VARIABLES}
    fenced = not args.no_fence
    only = "top" if args.top_only else "sides" if args.side_only else None
    if only == "top" and (args.corner or v["side_x_points"] or v["side_y_points"]):
        raise SystemExit("probe-stock: --top-only touches no sides; leave out --corner and --side-x-points/--side-y-points")
    corner = args.corner or only == "sides"  # the side touches move X0/Y0, so the corner puts them back
    check(v, corner, args.probe_rod_only)
    program_text, plan, what = probe_job(v, fenced, corner, only, args.probe_rod_only)
    out = args.out or HERE / PROBE_JOB
    out.write_text(program_text)
    stockref.record(out, "probe", f"probed {what}", plan=plan)
    print(f"wrote {out}: {what}; no cutting")
    if only:
        print(f"probe-stock.py save keeps the saved {'side touches' if only == 'top' else 'top'} with this run's")
    if corner or v["side_x_points"] or v["side_y_points"] or args.probe_rod_only:
        print("the probe rod only finds metal stock")
        if (v["side_x_points"] or v["side_y_points"]) and v["side_depth"] > CORNER_DEPTH and only != "top":
            print(f"warning: the rod goes {fmt(v['side_depth'])} mm down beside the stock, {fmt(v['side_clearance'])} mm out."
                  f" The collet nut comes down {fmt(v['side_depth'])} mm below the top with it: the rod has to stick out"
                  f" more than {fmt(v['side_depth'] + 5)} mm or the nut hits the stock")
    print("upload it and run it with Auto leveling off, then run probe-stock.py save")


if __name__ == "__main__":
    main()
