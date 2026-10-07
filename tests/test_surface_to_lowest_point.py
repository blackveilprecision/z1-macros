"""surface-to-lowest-point against made-up Studio logs (the real one is never read)."""

import bisect
import math
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import ORIGINS, ROOT, levels, moves, plunges_over_stock, run

SCRIPT = ROOT / "surface-to-lowest-point" / "surface-to-lowest-point.py"
SURFACE_STOCK = ROOT / "surface-stock" / "surface-stock.py"
PROBE_JOB = "surface-to-lowest-point-probe.nc"
FACE_JOB = "surface-to-lowest-point.nc"
WIDTH, LENGTH, HEIGHT, RADIUS, INSET = 65.4, 50.4, 33.0, 3.175 / 2, 3.0
POINTS = 20
# Spelled out rather than left to the defaults, which are meant to be edited for each stock
STOCK = (
    "--stock-width", WIDTH, "--stock-length", LENGTH, "--stock-height", HEIGHT, "--origin", "topFrontLeft",
    "--probe-grid-x", 5, "--probe-grid-y", 4, "--probe-inset", INSET, "--probe-clearance", 5, "--round-to", 0.5,
)
FENCE = ("--fence-x", -5, "--fence-y", 25, "--fence-height", 5)

# 20 heights 3.9 mm apart at the extremes, like stock that is 29.1 to 33 mm thick. The probe job leaves Z0 on
# its last point; in SPREAD that is 2.668 mm below the highest, in HIGH_LAST it is the highest.
SPREAD = [round(-67.0 - 3.9 * ((i * 7) % POINTS) / (POINTS - 1), 3) for i in range(POINTS)]
HIGH_LAST = sorted(SPREAD)
# Fenced: bed at -87.3, fence top 5 above it; stock 32.3 thick at its highest, 29.1 at its lowest
FENCED = [-82.3] + sorted(round(-55.0 - 3.2 * i / (POINTS - 1), 3) for i in range(POINTS))
LIFT = 0.5  # the script lifts this far above the floor just cut to move to the next pass's start


def probe_xy(test):
    """The probe points, in the order the probe job measures them."""
    _, program = test.generate("--probe")
    return [(x, y) for code, x, y, _, line in moves(program) if code == "G0" and "X" in line]


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


def studio_log(*entries):
    """Studio's log: (job name, [Z0 printed by each M498]) plays a job; ("raw", text) is any other line."""
    def line(text):
        return f'Debug    | 2026-10-06 18:02:11 Tue | :0,  | "[INFO]18:02:11.123 - {text}"\n'

    out = []
    for name, zs in entries:
        if name == "raw":
            out.append(line(f"Normal info: {zs}\\r\\n"))
            continue
        out.append(line(f"Playing file: /sd/gcodes/macros/{name}"))
        for z in zs:
            out += [line(f"Normal info: EEPRROM Data: {k}\\n") for k in ("TOOL:0", "TLO:0.000", "TOOLMZ:-77.481", "REFMZ:-77.481")]
            out.append(line(f"Normal info: EEPRROM Data: G54: -121.866, -143.602, {z:.3f}\\n"))
    return "".join(out)


class SurfaceToLowestPoint(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.logs = self.tmp / "logs"
        self.logs.mkdir()

    def write_log(self, *entries, name="log_2026-10-06-00-00-02.txt"):
        (self.logs / name).write_text(studio_log(*entries), encoding="utf-8")

    def generate(self, *args, ok=True, fence=False):
        out = self.tmp / "job.nc"
        if out.exists():
            out.unlink()
        r = run(SCRIPT, "--log", self.logs, "-o", out, *STOCK, *(FENCE if fence else ("--no-fence",)), *args)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
            return r, out.read_text()
        return r, None

    def test_probe_job_measures_every_point(self):
        _, program = self.generate("--probe")
        self.assertEqual(len(re.findall(r"^M498\b", program, re.M)), POINTS)
        self.assertEqual(program.count("G10 L20 P0 Z0"), POINTS)
        self.assertIn("G90 G0 X3 Y3", program, "first point is probe-inset in from the front-left corner")
        self.assertNotIn("G38.3", program, "G38.2 alarms instead of logging a point it missed")
        self.assertNotRegex(program, r"\bM0?3\b")
        self.assertNotIn("T1", program)
        self.assertTrue(program.rstrip().endswith("M02"))
        xy = [(x, y) for code, x, y, _, _ in moves(program) if code == "G0" and x is not None]
        self.assertEqual(len(set(xy)), POINTS)
        for x, y in xy:
            self.assertTrue(INSET <= x <= WIDTH - INSET and INSET <= y <= LENGTH - INSET, (x, y))

    def test_faces_down_to_the_lowest_point_rounded_down(self):
        self.write_log((PROBE_JOB, HIGH_LAST))
        r, program = self.generate()
        self.assertIn("Lowest point 29.10 mm, rounded down to 29 mm: cutting 4 mm below the highest point", r.stdout)
        zs = levels(program)
        self.assertEqual(zs[-1], -4.0)
        self.assertLessEqual(zs[0], 0.2)
        steps = [a - b for a, b in zip(zs, zs[1:])]
        self.assertTrue(all(0 < s <= 0.2 + 1e-9 for s in steps), steps)

    def test_facing_job_cuts_from_where_the_probe_job_left_z0(self):
        self.write_log((PROBE_JOB, SPREAD))
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
        zs = sorted(-67.0 + (z + 67.0) * 3.5 / 3.9 for z in SPREAD)  # 3.5 mm spread: the lowest is exactly 29.5
        self.write_log((PROBE_JOB, zs))
        r, program = self.generate()
        self.assertIn("rounded down to 29.5 mm", r.stdout)
        self.assertAlmostEqual(levels(program)[-1], -3.5, places=3)
        r, program = self.generate("--round-to", 1)
        self.assertAlmostEqual(levels(program)[-1], -4.0, places=3)

    def test_uses_the_newest_run(self):
        self.write_log((PROBE_JOB, [z - 0.5 for z in HIGH_LAST]), name="log_2026-10-05-00-00-08.txt")
        newer = [-68.2] + [-67.0] * (POINTS - 1)  # 1.2 mm spread: 31.8 -> 31.5
        self.write_log((PROBE_JOB, HIGH_LAST), ("other job.nc", [-80.0] * 3), (PROBE_JOB, newer))
        r, program = self.generate()
        self.assertIn("rounded down to 31.5 mm", r.stdout)
        self.assertAlmostEqual(levels(program)[-1], -1.5, places=3)

    def test_stops_on_an_incomplete_run(self):
        self.write_log((PROBE_JOB, SPREAD[:7]))
        r, _ = self.generate(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("logged 7 of 20 points", r.stderr)

    def test_asks_for_the_probe_run(self):
        r, _ = self.generate(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--probe", r.stderr)
        r, _ = self.generate("--final-height", 30, ok=False)
        self.assertIn("--probe", r.stderr, "the facing job needs the probe run for Z0 even with --final-height")

    def test_stops_when_z0_may_have_moved_since_the_run(self):
        moved = {
            "Studio's Z probe": [("raw", "G10 L20 P0 Z0.000")],
            "another job": [("bracket - op1.cnc", [])],
            "a G10 after the last Z0 print": [(FACE_JOB, [max(SPREAD)]), ("raw", "G10 L20 P0 Z0.000")],
        }
        for what, entries in moved.items():
            with self.subTest(what=what):
                self.write_log((PROBE_JOB, SPREAD), *entries)
                r, _ = self.generate(ok=False)
                self.assertEqual(r.returncode, 1)
                self.assertIn("Z0 may have moved", r.stderr)

    def test_uses_the_last_z0_printed_after_the_run(self):
        # A facing job that probed again moved Z0 up to the highest point and printed it
        self.write_log((PROBE_JOB, SPREAD), ("raw", "G10 L20 P0 Z0.000"), (FACE_JOB, [max(SPREAD)]))
        r, program = self.generate()
        self.assertAlmostEqual(levels(program)[-1], -4.0, places=3)  # cut from the highest point, not raised
        self.assertIn(f"G54 Z should read {max(SPREAD):.3f}", program)
        self.assertIn("printed at", r.stdout)

    def test_the_facing_job_itself_doesnt_count_as_moving_z0(self):
        # e.g. started, then stopped during the tool change: it printed the same Z0
        self.write_log((PROBE_JOB, HIGH_LAST), (FACE_JOB, [HIGH_LAST[-1]]), ("raw", "M493.2 T1"))
        self.generate()

    def test_final_height_overrides_the_rounding(self):
        self.write_log((PROBE_JOB, HIGH_LAST))
        r, program = self.generate("--final-height", 30)
        self.assertEqual(levels(program)[-1], -3.0)
        self.assertIn("Leaving 30 mm, given with --final-height", r.stdout)

    def test_plunges_land_off_the_stock_for_every_origin(self):
        self.write_log((PROBE_JOB, SPREAD))
        top = max(SPREAD) - SPREAD[-1] + 0.2  # the highest point, above Z0, plus top_margin
        for origin in ORIGINS:
            with self.subTest(origin=origin):
                _, program = self.generate("--origin", origin)
                self.assertEqual(plunges_over_stock(program, origin, WIDTH, LENGTH, RADIUS, top=top), [])

    def test_full_passes_cut_the_same_as_surface_stock(self):
        self.write_log((PROBE_JOB, HIGH_LAST))  # Z0 left on the highest point, like surface-stock's
        _, ours = self.generate("--full-passes", "--cut-along", "x")
        theirs_out = self.tmp / "surface-stock.nc"
        r = run(
            SURFACE_STOCK, "--stock-width", WIDTH, "--stock-length", LENGTH, "--stock-height", HEIGHT,
            "--target-z", -4, "--origin", "topFrontLeft", "-o", theirs_out,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        theirs = theirs_out.read_text()
        self.assertEqual(ours[ours.index("\nM7\n"):], theirs[theirs.index("\nM7\n"):])

    def test_passes_cover_everything_above_them(self):
        points = probe_xy(self)
        tops = {"bumpy": SPREAD, "tilted": tilted(points), "dome": dome(points), "across": across(points)}
        for name, zs in tops.items():
            for along in ("y", "x"):
                with self.subTest(top=name, cut_along=along):
                    self.write_log((PROBE_JOB, zs))
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
        self.write_log((PROBE_JOB, tilted(probe_xy(self))))
        seconds = {}
        for mode in ((), ("--full-passes",)):
            _, program = self.generate(*mode)
            seconds[mode] = int(re.search(r"TIME\|seconds=(\d+)", program).group(1))
        self.assertLess(seconds[()], 0.8 * seconds[("--full-passes",)], seconds)

    def test_probe_job_touches_the_fence_first(self):
        _, program = self.generate("--probe", fence=True)
        self.assertEqual(len(re.findall(r"^M498\b", program, re.M)), POINTS + 1)
        self.assertLess(program.index("G90 G0 X-5 Y25"), program.index("G90 G0 X3 Y3"))
        self.assertEqual(program.count("G38.2 Z-108"), 2, "fence and first stock point are probed from the top")

    def test_thickness_from_the_fence(self):
        self.write_log((PROBE_JOB, FENCED))
        r, program = self.generate(fence=True)
        self.assertIn("measured from the fence", r.stdout)
        self.assertIn("Lowest point 29.10 mm, rounded down to 29 mm: cutting 3.3 mm below the highest point", r.stdout)
        self.assertAlmostEqual(levels(program)[-1], -3.3, places=3)
        self.assertIn("|height=32.3|", program, "Studio's stock preview gets the probed thickness")

    def test_final_height_with_a_fence(self):
        self.write_log((PROBE_JOB, FENCED))
        _, program = self.generate("--final-height", 29.5, fence=True)
        self.assertAlmostEqual(levels(program)[-1], -2.8, places=3)

    def test_stops_when_the_probe_missed_the_fence(self):
        self.write_log((PROBE_JOB, [-87.3] + FENCED[1:]))  # touched the bed beside the fence: 5 mm low
        r, _ = self.generate(ok=False, fence=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--fence-x", r.stderr)

    def test_a_fenced_run_needs_the_fence_reading(self):
        self.write_log((PROBE_JOB, SPREAD))  # 20 values: written without the fence
        r, _ = self.generate(ok=False, fence=True)
        self.assertEqual(r.returncode, 1)
        self.assertIn("logged 20 of 21 points", r.stderr)

    def test_rejects_bad_values(self):
        bad = (["--final-height", "40"], ["--round-to", "0"], ["--probe-grid-x", "0"], ["--stepover", "4"], ["--fence-height", "0"])
        for args in bad:
            with self.subTest(args=args):
                r, _ = self.generate(*args, ok=False)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("surface-to-lowest-point:", r.stderr)


if __name__ == "__main__":
    unittest.main()
