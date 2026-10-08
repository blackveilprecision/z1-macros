"""surface-to-lowest-point against probe-stock's reference and made-up Studio logs (the real one is never read)."""

import bisect
import json
import math
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import ORIGINS, PROBE_STOCK, ROOT, Machine, levels, moves, plunges_over_stock, run

SCRIPT = ROOT / "surface-to-lowest-point" / "surface-to-lowest-point.py"
SURFACE_STOCK = ROOT / "surface-stock" / "surface-stock.py"
WIDTH, LENGTH, HEIGHT, RADIUS, INSET = 65.4, 50.4, 33.0, 3.175 / 2, 3.0
POINTS = 20
# probe-stock's job, spelled out rather than left to its defaults, which are meant to be edited for each stock
STOCK = (
    "--stock-width", WIDTH, "--stock-length", LENGTH, "--stock-height", HEIGHT, "--origin", "topFrontLeft",
    "--probe-grid-x", 5, "--probe-grid-y", 4, "--probe-inset", INSET, "--probe-clearance", 5,
)
FENCE = ("--fence-x", -5, "--fence-y", 25, "--fence-height", 5)

# 20 G54 Z values 3.9 mm apart at the extremes, like stock that is 29.1 to 33 mm thick. Z0 is left on the last
# point; in SPREAD that is 2.668 mm below the highest, in HIGH_LAST it is the highest.
SPREAD = [round(-67.0 - 3.9 * ((i * 7) % POINTS) / (POINTS - 1), 3) for i in range(POINTS)]
HIGH_LAST = sorted(SPREAD)
# With the anchor plate first: bed at -87.3, the plate 5 above it; stock 32.3 thick at its highest, 29.1 at its lowest
FENCED = [-82.3] + sorted(round(-55.0 - 3.2 * i / (POINTS - 1), 3) for i in range(POINTS))
LIFT = 0.5  # the script lifts this far above the floor just cut to move to the next pass's start


def tilted(points):
    """Heights of a plane like the real stock's: highest front-left, 3.2 mm lower at the back right."""
    return [round(-55.0 - 0.017 * x - 0.05 * y, 3) for x, y in points]


def dome(points):
    """Heights highest in the middle and 3.3 mm lower at the corners, so the first passes start far from any edge."""
    return [round(-55.0 - 0.002 * ((x - 32.7) ** 2 + (y - 25.2) ** 2), 3) for x, y in points]


def across(points):
    """Heights falling 3.7 mm left to right and barely front to back, so neighbouring passes along Y end far apart."""
    return [round(-55.0 - 0.06 * x - 0.004 * y, 3) for x, y in points]


def top_model(points, zs):
    """The top the tests hold the job to: bilinear between probe points, flat past them, in the job's Z."""
    xs, ys = sorted({x for x, _ in points}), sorted({y for _, y in points})
    h = {(x, y): z - zs[-1] for (x, y), z in zip(points, zs)}  # the job's Z0 is the last point

    def line(cs, vs, c):
        c = min(max(c, cs[0]), cs[-1])
        i = min(bisect.bisect_right(cs, c) - 1, len(cs) - 2)
        return vs[i] + (vs[i + 1] - vs[i]) * (c - cs[i]) / (cs[i + 1] - cs[i])

    return lambda x, y: line(ys, [line(xs, [h[(cx, cy)] for cx in xs], x) for cy in ys], y)


def passes(program):
    """{pass Z: [(x0, y0, x1, y1)]} for the moves made at each pass's depth, and the Z of every
    G0 that moves sideways after the first pass starts, with the floor of the pass before it."""
    cuts, rapids, layer, at = {}, [], None, None
    for code, x, y, z, line in moves(program):
        if code == "G1" and line.startswith("G1 Z"):
            layer = z
            cuts[z] = []
        elif code == "G1" and layer is not None and z == layer:
            cuts[layer].append((*at, x, y))
        elif code == "G0" and layer is not None and (x, y) != at:
            rapids.append((z, layer))
        at = (x, y)
    return cuts, rapids


def covered(segments, px, py, r):
    """Whether the cutter (radius r) passes over (px, py) on one of the segments."""
    for x0, y0, x1, y1 in segments:
        dx, dy = x1 - x0, y1 - y0
        t = 0.0 if dx == dy == 0 else max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
        if math.hypot(px - x0 - t * dx, py - y0 - t * dy) <= r + 1e-6:
            return True
    return False


class SurfaceToLowestPoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.machine = Machine(self.tmp)

    def probe(self, zs, *args, fence=False):
        """probe-stock's job for the stock, run printing these G54 Z values, and saved."""
        r, _ = self.machine.probe(*STOCK, *(FENCE if fence else ("--no-fence",)), *args, prints=zs)
        self.assertEqual(r.returncode, 0, r.stderr)

    def points(self):
        """The top points, in the order probe-stock's job touches them."""
        out = self.tmp / "points.nc"
        r = self.machine.run(PROBE_STOCK, "job", *STOCK, "--no-fence", "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        return [(x, y) for code, x, y, _, line in moves(out.read_text()) if code == "G0" and "X" in line]

    def generate(self, *args, ok=True):
        out = self.tmp / "job.nc"
        if out.exists():
            out.unlink()
        r = self.machine.run(SCRIPT, "--log", self.machine.logs, "-o", out, *args)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
            return r, out.read_text()
        return r, None

    def test_faces_down_to_the_lowest_point_rounded_down(self):
        self.probe(HIGH_LAST)
        r, program = self.generate()
        self.assertIn("Lowest point 29.10 mm, rounded down to 29 mm: cutting 4 mm below the highest point", r.stdout)
        zs = levels(program)
        self.assertEqual(zs[-1], -4.0)
        self.assertLessEqual(zs[0], 0.2)
        steps = [a - b for a, b in zip(zs, zs[1:])]
        self.assertTrue(all(0 < s <= 0.2 + 1e-9 for s in steps), steps)

    def test_facing_job_cuts_from_where_z0_is(self):
        self.probe(SPREAD)
        r, program = self.generate()
        shift = max(SPREAD) - SPREAD[-1]
        self.assertAlmostEqual(shift, 2.668, places=3)
        self.assertAlmostEqual(levels(program)[0], shift, places=3)  # top_margin 0.2 less one 0.2 pass, raised by shift
        self.assertAlmostEqual(levels(program)[-1], -4.0 + shift, places=3)
        for banned in ("G38", "G10", "T0"):
            self.assertNotIn(banned, program, "the facing job doesn't probe or move Z0")
        self.assertEqual(re.findall(r"^M498 ; G54 Z should read (\S+):", program, re.M), [f"{SPREAD[-1]:.3f}"])
        self.assertLess(program.index("M498"), program.index("T1 M6"), "Z0 is printed before the cutter goes in")
        self.assertIn(f"G54 Z {SPREAD[-1]:.3f}", r.stdout)

    def test_rounds_down_only_when_needed(self):
        self.probe(sorted(-67.0 + (z + 67.0) * 3.5 / 3.9 for z in SPREAD))  # 3.5 mm spread: the lowest is exactly 29.5
        r, program = self.generate()
        self.assertIn("rounded down to 29.5 mm", r.stdout)
        self.assertAlmostEqual(levels(program)[-1], -3.5, places=3)
        r, program = self.generate("--round-to", 1)
        self.assertAlmostEqual(levels(program)[-1], -4.0, places=3)

    def test_asks_for_the_probe_run(self):
        r, _ = self.generate(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("probe-stock.py job", r.stderr)

    def test_stops_when_z0_may_have_moved(self):
        moved = {
            "Studio's Z probe": [("raw", "G10 L20 P0 Z0.000")],
            "another job": [("play", "bracket - op1.cnc"), ("finish",)],
        }
        for what, events in moved.items():
            with self.subTest(what=what):
                self.machine = Machine(self.tmp)
                self.probe(SPREAD)
                self.machine.log(*events)
                r, _ = self.generate(ok=False)
                self.assertEqual(r.returncode, 1)
                self.assertIn("may have moved", r.stderr)

    def test_cuts_from_z0_wherever_it_was_printed_last(self):
        # Studio's Z probe moved Z0 up to the highest point, and probe-stock's where job printed it
        self.probe(SPREAD)
        where = self.tmp / "probe-stock-where.nc"
        self.machine.log(("raw", "G10 L20 P0 Z0.000"))
        self.assertEqual(self.machine.run(PROBE_STOCK, "where", "-o", where).returncode, 0)
        self.machine.play(where, [max(SPREAD)])
        r, program = self.generate()
        self.assertAlmostEqual(levels(program)[-1], -4.0, places=3)  # cut from the highest point, not raised
        self.assertIn(f"G54 Z should read {max(SPREAD):.3f}", program)

    def test_a_finished_facing_job_counts_for_the_next_one(self):
        self.probe(HIGH_LAST)
        self.generate()
        jobs = json.loads((self.tmp / "jobs.json").read_text())
        self.assertEqual(jobs[-1]["effect"]["type"], "top")
        self.machine.play(self.tmp / "job.nc", [HIGH_LAST[-1]])
        r, _ = self.generate(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("already 29.000 mm thick everywhere", r.stderr)
        r, program = self.generate("--final-height", 28)
        self.assertIn("job.nc: top faced flat, 29 mm thick", r.stdout)
        self.assertAlmostEqual(levels(program)[-1], -5.0, places=3, msg="Z0 is still on the highest point as probed")
        self.assertAlmostEqual(levels(program)[0], -4.0 + 0.2 - 0.2, places=3, msg="the first pass starts on the faced top")

    def test_a_stopped_facing_job_doesnt_count(self):
        self.probe(HIGH_LAST)
        self.generate()
        self.machine.play(self.tmp / "job.nc", [HIGH_LAST[-1]], end=("raw", "Aborted by halt"))
        r, _ = self.generate()
        self.assertIn("rounded down to 29 mm", r.stdout)
        self.assertIn("stopped", r.stdout)

    def test_the_last_pass_finishes(self):
        self.probe(HIGH_LAST)
        _, program = self.generate()
        zs = levels(program)
        self.assertAlmostEqual(zs[-1], -4.0, places=3)
        self.assertAlmostEqual(zs[-2], -3.9, places=3, msg="roughing stops 0.1 above the final depth")
        cuts, _ = passes(program)
        finish, rough = cuts[zs[-1]], cuts[zs[-2]]
        rows = lambda segs: sorted({round(x0, 3) for x0, _, x1, _ in segs if x0 == x1})  # noqa: E731 - cutting along Y
        self.assertLessEqual(max(b - a for a, b in zip(rows(finish), rows(finish)[1:])), 3.175 / 2 + 1e-3, "half the cutter")
        self.assertGreater(len(rows(finish)), len(rows(rough)))
        grid = [(x, y) for x in range(0, 66, 2) for y in range(0, 51, 2)]
        for z in (zs[-2], zs[-1]):
            self.assertEqual([p for p in grid if not covered(cuts[z], *p, RADIUS)], [], "both cover the whole top")
        self.assertIn("finishing pass, 0.1 mm deep", program)
        _, program = self.generate("--finish-depth", 0)
        self.assertAlmostEqual(levels(program)[-2], -3.8, places=3, msg="without it, plain 0.2 mm passes")

    def test_final_height_overrides_the_rounding(self):
        self.probe(HIGH_LAST)
        r, program = self.generate("--final-height", 30)
        self.assertEqual(levels(program)[-1], -3.0)
        self.assertIn("Leaving 30 mm, given with --final-height", r.stdout)

    def test_plunges_land_off_the_stock_for_every_origin(self):
        top = max(SPREAD) - SPREAD[-1] + 0.2  # the highest point, above Z0, plus top_margin
        for origin in ORIGINS:
            with self.subTest(origin=origin):
                self.probe(SPREAD, "--origin", origin)
                _, program = self.generate()
                self.assertEqual(plunges_over_stock(program, origin, WIDTH, LENGTH, RADIUS, top=top), [])

    def test_full_passes_cut_the_same_as_surface_stock(self):
        self.probe(HIGH_LAST)  # Z0 left on the highest point, like surface-stock's
        _, ours = self.generate("--full-passes", "--cut-along", "x", "--finish-depth", 0)
        theirs_out = self.tmp / "surface-stock.nc"
        r = run(
            SURFACE_STOCK, "--stock-width", WIDTH, "--stock-length", LENGTH, "--stock-height", HEIGHT,
            "--target-z", -4, "--origin", "topFrontLeft", "-o", theirs_out,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        theirs = theirs_out.read_text()
        self.assertEqual(ours[ours.index("\nM7\n"):], theirs[theirs.index("\nM7\n"):])

    def test_passes_cover_everything_above_them(self):
        points = self.points()
        tops = {"bumpy": SPREAD, "tilted": tilted(points), "dome": dome(points), "across": across(points)}
        for name, zs in tops.items():
            for along in ("y", "x"):
                with self.subTest(top=name, cut_along=along):
                    self.probe(zs)
                    _, program = self.generate("--cut-along", along)
                    top = top_model(points, zs)
                    cuts, rapids = passes(program)
                    zs_cut = sorted(cuts, reverse=True)
                    grid = [(x, y) for x in range(0, 66, 2) for y in range(0, 51, 2)]
                    for z in zs_cut:
                        above = [(x, y) for x, y in grid if top(x, y) > z + 1e-6]
                        missed = [p for p in above if not covered(cuts[z], *p, RADIUS)]
                        self.assertEqual(missed[:5], [], f"material above the pass at Z{z} left uncut")
                    self.assertEqual([p for p in grid if not covered(cuts[zs_cut[-1]], *p, RADIUS)], [], "last pass")
                    for z in zs_cut[:-1]:  # steps across to the next row are made in the clear, not through material
                        for x0, y0, x1, y1 in cuts[z]:
                            if (x0 != x1) == (along == "y"):
                                band = [(x0 + (x1 - x0) * k / 4 + dx, y0 + (y1 - y0) * k / 4 + dy)
                                        for k in range(5) for dx in (-RADIUS, 0, RADIUS) for dy in (-RADIUS, 0, RADIUS)]
                                inside = [q for q in band if 0 <= q[0] <= WIDTH and 0 <= q[1] <= LENGTH]
                                self.assertTrue(all(top(*q) <= z + 1e-6 for q in inside), f"step through material at Z{z}")
                    along_cut = [abs(y1 - y0) if along == "y" else abs(x1 - x0) for z in cuts for x0, y0, x1, y1 in cuts[z]]
                    self.assertGreater(sum(along_cut), 0.8 * sum(abs(x1 - x0) + abs(y1 - y0) for z in cuts for x0, y0, x1, y1 in cuts[z]))
                    self.assertEqual(plunges_over_stock(program, "topFrontLeft", WIDTH, LENGTH, RADIUS, top=1e9), [],
                                     "every pass goes down beyond the stock's edge")
                    low = [(z, floor) for z, floor in rapids if floor is not None and z < floor + LIFT - 1e-6]
                    self.assertEqual(low, [], "moves between passes clear the floor just cut")

    def test_skipping_air_saves_time_on_a_sloped_top(self):
        self.probe(tilted(self.points()))
        seconds = {}
        for mode in ((), ("--full-passes",)):
            _, program = self.generate(*mode)
            seconds[mode] = int(re.search(r"TIME\|seconds=(\d+)", program).group(1))
        self.assertLess(seconds[()], 0.8 * seconds[("--full-passes",)], seconds)

    def test_thickness_from_the_anchor_plate(self):
        self.probe(FENCED, fence=True)
        r, program = self.generate()
        self.assertIn("Thicknesses are from the anchor plate", r.stdout)
        self.assertIn("Lowest point 29.10 mm, rounded down to 29 mm: cutting 3.3 mm below the highest point", r.stdout)
        self.assertAlmostEqual(levels(program)[-1], -3.3, places=3)
        self.assertIn("|height=32.3|", program, "Studio's stock preview gets the probed thickness")

    def test_final_height_with_the_anchor_plate(self):
        self.probe(FENCED, fence=True)
        _, program = self.generate("--final-height", 29.5)
        self.assertAlmostEqual(levels(program)[-1], -2.8, places=3)

    def test_rejects_bad_values(self):
        self.probe(HIGH_LAST)
        bad = (["--final-height", "40"], ["--round-to", "0"], ["--stepover", "4"], ["--pass-depth", "0"],
               ["--finish-depth", "0.3"], ["--finish-stepover", "4"])
        for args in bad:
            with self.subTest(args=args):
                r, _ = self.generate(*args, ok=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("surface-to-lowest-point:", r.stderr)


if __name__ == "__main__":
    unittest.main()
