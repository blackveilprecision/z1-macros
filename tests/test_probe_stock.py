"""probe-stock and its stock reference against made-up Studio logs (the real one is never read)."""

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import PROBE_STOCK, REFMZ, ROOT, X0, Y0, Machine, moves

sys.path.insert(0, str(ROOT / "probe-stock"))
import stockref  # noqa: E402

WIDTH, LENGTH, HEIGHT, INSET = 65.4, 50.4, 29.0, 3.0
# Spelled out rather than left to the defaults, which are meant to be edited for each stock
STOCK = (
    "--stock-width", WIDTH, "--stock-length", LENGTH, "--stock-height", HEIGHT, "--origin", "topFrontLeft",
    "--probe-grid-x", 5, "--probe-grid-y", 4, "--probe-inset", INSET, "--probe-clearance", 5,
    "--fence-x", -7.5, "--fence-y", 77, "--fence-height", 5, "--rod-dia", 2, "--rod-tool", 9999, "--side-clearance", 5,
    "--clamp-height", 10,
)
POINTS = 20
BED = -9.952  # above the tool setter, as on the real machine


def z(thickness):
    """The G54 Z an M498 prints after Z0 is set on a surface this far above the bed."""
    return round(BED + thickness + REFMZ, 3)


FENCE = z(5)
TOP = [z(29.0 + 0.01 * (i % 3)) for i in range(POINTS)]  # 29.00 to 29.02 thick


class ProbeStock(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.machine = Machine(self.tmp)
        os.environ["Z1_STOCK"] = str(self.tmp)
        self.addCleanup(os.environ.pop, "Z1_STOCK")

    def job(self, *args, ok=True):
        out = self.tmp / "probe-stock.nc"
        r = self.machine.run(PROBE_STOCK, "job", *STOCK, *args, "-o", out)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
            return out.read_text()
        return r

    def save(self, ok=True):
        r = self.machine.run(PROBE_STOCK, "save", "--log", self.machine.logs)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def assertZ1Safe(self, program):
        for line in program.splitlines():
            code, _, comment = line.partition(";")
            if code.strip():
                self.assertLessEqual(len(line), 63, f"the Z1 drops what's past 63 characters: {line}")
                self.assertNotRegex(comment, r"G9[01]", f"Makera's firmware moves G90/G91 out of comments: {line}")
        for banned in ("M498.2", "G92", "G38.3", "M3", "T1"):
            self.assertNotRegex(program, rf"\b{re.escape(banned)}\b")

    # --- the job ---

    def test_job_touches_the_plate_then_the_top(self):
        program = self.job()
        self.assertZ1Safe(program)
        self.assertEqual(len(re.findall(r"^M498\b", program, re.M)), POINTS + 1)
        self.assertEqual(program.count("G10 L20 P0 Z0"), POINTS + 1)
        self.assertLess(program.index("T0 M6"), program.index("G38.2"))
        self.assertLess(program.index("G90 G0 X-7.5 Y77"), program.index("G90 G0 X3 Y3"), "the anchor plate first")
        self.assertEqual(program.count("G38.2 Z-108"), 2, "the plate and the first point are probed from the top")
        self.assertEqual(program.count("G38.2 Z-2 F100"), POINTS + 1, "every Z0 is set after a slow touch, as Studio's Z probe does")
        self.assertNotRegex(program, r"G10 L20 P0 [XY]", "X0/Y0 are left alone without side touches")
        self.assertEqual(re.findall(r"^T(\d+) M6", program, re.M), ["0"], "only the probe")
        self.assertTrue(program.rstrip().endswith("M02"))
        xy = [(x, y) for code, x, y, _, line in moves(program) if code == "G0" and "X" in line and x != -7.5]
        self.assertEqual(len(set(xy)), POINTS)
        for x, y in xy:
            self.assertTrue(INSET <= x <= WIDTH - INSET and INSET <= y <= LENGTH - INSET, (x, y))

    def test_side_touches_then_the_origin_put_back(self):
        program = self.job("--side-x-points", 3, "--side-y-points", 2, "--side-depth", 15)
        self.assertZ1Safe(program)
        self.assertEqual(len(re.findall(r"^M498\b", program, re.M)), 1 + 3 + 1 + 2 + 1 + 1 + POINTS)
        self.assertEqual(re.findall(r"^T(\d+) M6", program, re.M), ["9999", "0"], "the rod, then the probe")
        rod, probe = program.index("T9999 M6"), program.index("T0 M6")
        self.assertEqual(re.findall(r"^G91 G38\.2 Z-108 F(\d+)", program, re.M), ["100", "500", "500"], "the rigid rod comes down slower")
        self.assertLess(rod, program.index("G10 L20 P0 Z0"), "the rod sets Z0 on the top before its side touches")
        self.assertLess(program.index("G10 L20 P0 Z0"), program.index("G10 L20 P0 X"))
        self.assertLess(program.rindex("G10 L20 P0 Y"), probe, "every side touch is made with the rod")
        self.assertLess(probe, program.index("G90 G0 X-7.5 Y77"), "the anchor plate and the top with the probe")
        sets = re.findall(r"^G10 L20 P0 ([XY]\S+)", program, re.M)
        self.assertEqual(sets, ["X66.4"] * 3 + ["X-1"] + ["Y51.4"] * 2 + ["Y-1"], "each side, then the origin's own")
        starts = re.findall(r"^G0 X(\S+) Y(\S+)\nG1 Z(\S+)", program, re.M)
        self.assertEqual([float(x) for x, _, d in starts[:3]], [71.4] * 3, "the rod comes down 5 mm outside the right side")
        self.assertEqual([float(d) for _, _, d in starts[:3]], [-15] * 3)
        self.assertEqual(starts[3][:2], ("-11", "3"), "out 10 mm from the left side, by the corner, to put X0 back")
        self.assertEqual(starts[6][:2], ("3", "-11"), "Y0 on the front, by the corner")
        self.assertEqual([float(y) for _, y, _ in starts[4:6]], [56.4] * 2)
        for touch in re.findall(r"G38\.2 ([XY])(-?[\d.]+) F100\nG91 G0 \1(-?[\d.]+)", program):
            self.assertLess(float(touch[1]) * float(touch[2]), 0, "backs off the way it came")

    def test_studio_sees_no_probe_search_as_a_position(self):
        # Studio goes to the lowest X/Y in its preview before playing a file, and reads G38.2 in G90 as positions
        program = self.job("--corner", "--side-x-points", 3, "--side-y-points", 2, "--side-depth", 15)
        relative = False
        for line in program.splitlines():
            code = line.split(";")[0]
            relative = "G91" in code or (relative and "G90" not in code)
            if "G38" in code:
                self.assertTrue(relative, f"G38 written in G90: {line}")
        searches = [float(v) for v in re.findall(r"G38\.2 [XY](-?[\d.]+)", program)]
        self.assertGreaterEqual(min(searches), -10, "even read as positions, no further out than the corner touches")

    def test_side_touches_from_the_other_corner(self):
        program = self.job("--origin", "topBackRight", "--side-x-points", 2)
        self.assertEqual(re.findall(r"^G10 L20 P0 ([XY]\S+)", program, re.M), ["X-66.4", "X-66.4", "X1"])
        self.assertIn("G0 X-71.4 Y", program, "the left side is across from a right origin")

    def test_corner_only(self):
        program = self.job("--corner")
        self.assertEqual(re.findall(r"^G10 L20 P0 ([XY]\S+)", program, re.M), ["X-1", "Y-1"])

    def test_refuses_what_it_cant_touch_safely(self):
        bad = {
            "below the clamps": ("--side-x-points", 2, "--side-depth", 19),
            "no corner to put X0 back on": ("--side-x-points", 2, "--origin", "topCenter"),
            "deeper than the stock": ("--side-y-points", 1, "--side-depth", 30, "--clamp-height", 0),
            "no top point": ("--probe-grid-x", 0),
            "the rod as the probe's tool": ("--side-x-points", 2, "--rod-tool", 0),
        }
        for what, args in bad.items():
            with self.subTest(what):
                r = self.job(*args, ok=False)
                self.assertEqual(r.returncode, 1)
                self.assertIn("probe-stock:", r.stderr)

    # --- save ---

    def test_save_measures_the_top_from_the_anchor_plate(self):
        r, _ = self.machine.probe(*STOCK, prints=[FENCE, *TOP])
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = json.loads((self.tmp / "stock.json").read_text())
        self.assertEqual(ref["bed"], BED)
        self.assertEqual([round(h - BED, 3) for _, _, h in ref["top"]], [round(29.0 + 0.01 * (i % 3), 3) for i in range(POINTS)])
        self.assertEqual(ref["top"][0][:2], [INSET, INSET])
        self.assertEqual(ref["machine"]["z"], TOP[-1], "Z0 is left on the last point")
        self.assertIn("29.020  highest", r.stdout)

    def test_save_measures_the_sides(self):
        right = [63.5 + 0.02 * t for t in (3, 25.2, 47.4)]  # how far the right side is from the left, at each Y
        back = 50.3
        left = round(X0 + 0.01, 3)  # the rod finds the left side 0.01 mm from where Studio's corner probe put X0
        rod = TOP[0] + 0.004  # the rod's Z0 on the first top point, 0.004 mm off the probe's there
        prints = [rod]
        prints += [(X0 + x - WIDTH, Y0, rod) for x in right]  # each moves X0 by how far the side is off WIDTH
        prints += [(left, Y0, rod), (left, Y0 + back - LENGTH, rod), (left, Y0, rod)]
        prints += [(left, Y0, h) for h in (FENCE, *TOP)]  # then the probe
        r, _ = self.machine.probe(*STOCK, "--side-x-points", 3, "--side-y-points", 1, "--side-depth", 15, prints=prints)
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = json.loads((self.tmp / "stock.json").read_text())
        self.assertEqual([u for _, _, u in ref["faces"]["x"]], [round(x - 0.01, 3) for x in right], "from the left side as touched")
        self.assertEqual([round(tip - BED, 3) for _, tip, _ in ref["faces"]["x"]], [14.004] * 3, "tip 15 mm below the rod's Z0")
        self.assertEqual(ref["faces"]["y"], [[WIDTH / 2, ref["faces"]["y"][0][1], back]])
        self.assertEqual((ref["machine"]["x"], ref["machine"]["y"], ref["machine"]["z"]), (left, Y0, TOP[-1]))
        self.assertEqual(ref["bed"], BED, "the anchor plate, touched with the probe")
        self.assertEqual(ref["rod_vs_probe"], 0.004)
        self.assertIn("moved X0 by +0.010 mm", r.stdout)
        self.assertIn("0.004 mm apart", r.stdout)

    def test_save_reads_the_newest_run(self):
        self.machine.probe(*STOCK, prints=[FENCE, *(h + 0.5 for h in TOP)])
        r, _ = self.machine.probe(*STOCK, prints=[FENCE, *TOP])
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = json.loads((self.tmp / "stock.json").read_text())
        self.assertEqual(ref["machine"]["z"], TOP[-1])

    def test_save_stops_on_an_incomplete_run(self):
        self.job()
        self.machine.play(self.tmp / "probe-stock.nc", [FENCE, *TOP[:6]], end=("raw", "Aborted by halt"))
        r = self.save(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("logged 7 of 21 touches and stopped: Aborted by halt", r.stderr)
        self.assertFalse((self.tmp / "stock.json").exists())

    def test_save_needs_the_job_written_here(self):
        job = self.tmp / "probe-stock.nc"
        job.write_text(self.job() + "; edited\n")
        self.machine.play(job, [FENCE, *TOP])
        r = self.save(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("isn't one written here", r.stderr)

    def test_save_stops_when_the_probe_missed_the_plate(self):
        r, _ = self.machine.probe(*STOCK, prints=[z(0), *TOP])  # touched the bed beside the plate: 5 mm low
        self.assertEqual(r.returncode, 1)
        self.assertIn("--fence-x", r.stderr)

    def test_without_the_plate_the_thickness_is_assumed(self):
        r, _ = self.machine.probe(*STOCK, "--no-fence", "--stock-height", 33, prints=TOP)
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = json.loads((self.tmp / "stock.json").read_text())
        self.assertAlmostEqual(max(h for _, _, h in ref["top"]) - ref["bed"], 33.0, places=3)
        self.assertIn("assumed", ref["bed_from"])

    # --- the stock as jobs run ---

    def probed(self):
        r, _ = self.machine.probe(*STOCK, prints=[FENCE, *TOP])
        self.assertEqual(r.returncode, 0, r.stderr)

    def cut_job(self, name="face.nc", h=BED + 28.5):
        job = self.tmp / name
        job.write_text(f"; made up {h}\nM498\nG28\nM02\n")
        stockref.record(job, "cut", "top faced", effect={"type": "top", "h": h})
        return job

    def state(self):
        return stockref.load(self.machine.logs)

    def test_a_finished_job_counts(self):
        self.probed()
        self.machine.play(self.cut_job(), [TOP[-1]])
        state = self.state()
        self.assertEqual({round(h - BED, 3) for _, _, h in state.top}, {28.5})
        self.assertEqual([text for _, text in state.history], ["face.nc: top faced"])
        state.check("test")

    def test_a_stopped_job_doesnt(self):
        self.probed()
        self.machine.play(self.cut_job(), [TOP[-1]], end=("raw", "ALARM: Abort during cycle"))
        state = self.state()
        self.assertGreater(min(h for _, _, h in state.top) - BED, 28.9)
        self.assertIn("stopped", state.notes[0])
        state.check("test")

    def test_only_the_version_that_played_counts(self):
        self.probed()
        job = self.cut_job()
        self.machine.log(("upload", job))
        self.cut_job(h=BED + 20)  # written again, but the upload before stays on the machine
        self.machine.log(("play", "face.nc", [TOP[-1]]), ("finish",))
        self.assertEqual({round(h - BED, 3) for _, _, h in self.state().top}, {28.5})

    def test_unknown_jobs_and_studios_probes_may_move_z0(self):
        cases = {
            "another job": lambda: self.machine.log(("play", "bracket - op1.nc"), ("finish",)),
            "Studio's Z probe": lambda: self.machine.log(("raw", "G10 L20 P0 Z0")),
            "a job written here, then Studio's corner probe": lambda: (
                self.machine.play(self.cut_job(), [TOP[-1]]), self.machine.log(("raw", "G10 L20 P0 X1.000"))),
        }
        for what, events in cases.items():
            with self.subTest(what):
                self.machine = Machine(self.tmp)
                self.probed()
                events()
                with self.assertRaises(SystemExit) as e:
                    self.state().check("test")
                self.assertIn("may have moved", str(e.exception))

    def test_a_print_says_where_z0_is_again(self):
        self.probed()
        moved = TOP[-1] + 1.234  # Studio's Z probe put Z0 1.234 mm higher, then the where job printed it
        self.machine.log(("raw", "G10 L20 P0 Z0"))
        where = self.tmp / "probe-stock-where.nc"
        r = self.machine.run(PROBE_STOCK, "where", "-o", where)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.machine.play(where, [moved])
        state = self.state()
        state.check("test")
        self.assertAlmostEqual(state.z0 - BED, 29.01 + 1.234, places=3)

    def test_a_newer_probe_run_has_to_be_saved(self):
        self.probed()
        self.machine.probe(*STOCK, prints=[FENCE, *TOP], save=False)
        with self.assertRaises(SystemExit) as e:
            self.state().check("test")
        self.assertIn("probe-stock.py save", str(e.exception))

    def test_moving_the_origin_stops_the_jobs(self):
        self.probed()
        self.machine.play(self.cut_job(), [(X0 + 0.5, Y0, TOP[-1])])
        with self.assertRaises(SystemExit) as e:
            self.state().check("test")
        self.assertIn("+0.500, +0.000 mm", str(e.exception))

    # --- --top-only, --side-only, --probe-rod-only ---

    def full(self, widths=(63.6, 64.0, 64.3)):
        """A full run with the rod on the right side and the corner, then the probe; returns the side widths."""
        rod = TOP[0]
        prints = [rod] + [(round(X0 + w - WIDTH, 3), Y0, rod) for w in widths] + [(X0, Y0, rod), (X0, Y0, rod)]
        prints += [(X0, Y0, h) for h in (FENCE, *TOP)]
        r, _ = self.machine.probe(*STOCK, "--corner", "--side-x-points", len(widths), prints=prints)
        self.assertEqual(r.returncode, 0, r.stderr)
        return widths

    def saved(self):
        return json.loads((self.tmp / "stock.json").read_text())

    def test_top_only_job(self):
        program = self.job("--top-only")
        self.assertEqual(re.findall(r"^T(\d+) M6", program, re.M), ["0"])
        self.assertEqual(len(re.findall(r"^M498\b", program, re.M)), 1 + POINTS)
        r = self.job("--top-only", "--corner", ok=False)
        self.assertIn("--top-only touches no sides", r.stderr)

    def test_side_only_job(self):
        program = self.job("--side-only", "--side-x-points", 3)
        self.assertEqual(re.findall(r"^T(\d+) M6", program, re.M), ["9999"])
        self.assertEqual(re.findall(r"^G10 L20 P0 ([XY]\S+)", program, re.M), ["X66.4"] * 3 + ["X-1", "Y-1"], "the corner puts X0/Y0 back")
        self.assertNotIn("X-7.5 Y77", program, "no anchor plate")
        self.assertEqual(len(re.findall(r"^M498\b", program, re.M)), 1 + 3 + 2)

    def test_probe_rod_only_job(self):
        program = self.job("--probe-rod-only", "--corner", "--side-x-points", 3)
        self.assertZ1Safe(program)
        self.assertEqual(re.findall(r"^T(\d+) M6", program, re.M), ["9999"], "one tool change")
        self.assertLess(program.index("X-7.5 Y77"), program.index("G90 G0 X3 Y3"), "the anchor plate first")
        self.assertLess(program.index("G90 G0 X3 Y3"), program.index("G10 L20 P0 X"), "Z0 on the top before the sides")
        self.assertEqual(set(re.findall(r"G38\.2 Z-\S+ F(\d+)", program)), {"100"}, "every Z touch at the rod's speed")
        self.assertEqual(len(re.findall(r"^M498\b", program, re.M)), 1 + POINTS + 3 + 2)

    def test_probe_rod_only_saves_everything(self):
        prints = [(X0, Y0, FENCE), TOP[0]] + [(round(X0 + w - WIDTH, 3), Y0, TOP[0]) for w in (63.6, 64.0, 64.3)]
        prints += [(X0, Y0, TOP[0])] * 2 + [(X0, Y0, h) for h in TOP[1:]]
        r, _ = self.machine.probe(*STOCK, "--probe-rod-only", "--corner", "--side-x-points", 3, prints=prints)
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = self.saved()
        self.assertEqual([round(h - BED, 3) for _, _, h in ref["top"]], [round(z - BED - REFMZ, 3) for z in TOP])
        self.assertEqual([u for _, _, u in ref["faces"]["x"]], [63.6, 64.0, 64.3])

    def test_top_only_keeps_the_sides(self):
        widths = self.full()
        r, _ = self.machine.probe(*STOCK, "--top-only", prints=[FENCE] + [h + 0.1 for h in TOP])
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = self.saved()
        self.assertEqual([u for _, _, u in ref["faces"]["x"]], list(widths), "kept from the full run")
        self.assertAlmostEqual(ref["top"][0][2] - (TOP[0] - REFMZ), 0.1, places=3, msg="the new top")
        self.assertIn("the sides kept from the probe run of", r.stdout)

    def test_side_only_keeps_the_top_and_any_cut_since(self):
        self.full()
        self.machine.play(self.cut_job(h=BED + 28.5), [TOP[-1]])  # the top faced since
        rod = TOP[0] - 0.4  # the rod touches the faced top beside the corner
        prints = [rod] + [(round(X0 + w - WIDTH, 3), Y0, rod) for w in (63.5, 63.5, 63.5)] + [(X0, Y0, rod)] * 2
        r, _ = self.machine.probe(*STOCK, "--side-only", "--side-x-points", 3, prints=prints)
        self.assertEqual(r.returncode, 0, r.stderr)
        ref = self.saved()
        self.assertEqual({round(h - BED, 3) for _, _, h in ref["top"]}, {28.5}, "the faced top, not the probed one")
        self.assertEqual([u for _, _, u in ref["faces"]["x"]], [63.5] * 3)
        self.assertIn("The rod read the top beside the corner", r.stdout)
        self.assertIn("the top kept from the probe run of", r.stdout)

    def test_side_only_needs_a_saved_top(self):
        rod = TOP[0]
        r, _ = self.machine.probe(*STOCK, "--side-only", "--side-x-points", 1,
                                  prints=[rod, (X0 - 1, Y0, rod), (X0, Y0, rod), (X0, Y0, rod)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("Run a full probe job", r.stderr)

    def test_partial_runs_refuse_when_the_stock_may_have_changed(self):
        cases = {
            "a job not written here": (lambda: self.machine.log(("play", "bracket.nc"), ("finish",)), [FENCE] + TOP, "may have cut"),
            "X0 moved": (lambda: None, [(X0 + 0.5, Y0, FENCE)] + [(X0 + 0.5, Y0, h) for h in TOP], "+0.500, +0.000"),
        }
        for what, (between, prints, message) in cases.items():
            with self.subTest(what):
                self.machine = Machine(self.tmp)
                self.full()
                between()
                r, _ = self.machine.probe(*STOCK, "--top-only", prints=prints)
                self.assertEqual(r.returncode, 1)
                self.assertIn(message, r.stderr)

    # --- update ---

    def update(self):
        r = self.machine.run(PROBE_STOCK, "update", "--log", self.machine.logs)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_update_writes_in_finished_jobs(self):
        self.probed()
        self.assertIn("stock.json is up to date", self.update().stdout)
        self.machine.play(self.cut_job(), [TOP[-1]])
        r = self.update()
        self.assertIn("wrote in", r.stdout)
        ref = self.saved()
        self.assertEqual({round(h - BED, 3) for _, _, h in ref["top"]}, {28.5}, "the faced top is in stock.json now")
        self.assertEqual(ref["anchor"]["name"], "face.nc")
        self.assertEqual([text for _, text in self.state().history], ["face.nc: top faced"], "counted once")
        self.assertIn("stock.json is up to date", self.update().stdout)

    def test_update_stops_before_what_may_have_moved_z0(self):
        self.probed()
        self.machine.play(self.cut_job(), [TOP[-1]])
        self.machine.log(("play", "bracket.nc"), ("finish",))
        self.update()
        self.assertEqual(self.saved()["anchor"]["name"], "face.nc", "written up to the last job written here")
        with self.assertRaises(SystemExit) as e:
            self.state().check("test")
        self.assertIn("bracket.nc played", str(e.exception), "the next macro still stops on it")

    def test_update_remembers_a_job_not_written_here(self):
        self.full()
        where = self.tmp / "probe-stock-where.nc"
        self.machine.run(PROBE_STOCK, "where", "-o", where)
        self.machine.log(("play", "bracket.nc"), ("finish",))
        self.machine.play(where, [TOP[-1]])  # a print: Z0 is known again, but bracket.nc may have cut the stock
        self.update()
        r, _ = self.machine.probe(*STOCK, "--top-only", prints=[FENCE] + TOP)
        self.assertEqual(r.returncode, 1)
        self.assertIn("may have cut", r.stderr)

    def test_update_saves_a_newer_probe_run(self):
        self.probed()
        self.machine.probe(*STOCK, prints=[FENCE, *(h - 0.2 for h in TOP)], save=False)
        r = self.update()
        self.assertIn("saved the probe run", r.stdout)
        self.assertAlmostEqual(self.saved()["machine"]["z"], TOP[-1] - 0.2, places=3)

    def test_update_skips_a_probe_run_that_stopped(self):
        self.probed()
        self.machine.play(self.cut_job(), [TOP[-1]])
        self.job()
        self.machine.play(self.tmp / "probe-stock.nc", [FENCE, *TOP[:3]], end=("raw", "Aborted by halt"))
        r = self.update()
        self.assertIn("didn't save the newer probe run", r.stdout)
        self.assertEqual(self.saved()["anchor"]["name"], "face.nc", "the cut before it is still written in")

    def test_update_without_stock_json_saves_the_probe_run(self):
        self.machine.probe(*STOCK, prints=[FENCE, *TOP], save=False)
        self.update()
        self.assertTrue((self.tmp / "stock.json").exists())

    def test_show(self):
        self.probed()
        self.machine.play(self.cut_job(), [TOP[-1]])
        r = self.machine.run(PROBE_STOCK, "show", "--log", self.machine.logs)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Top: flat, 28.500 mm thick (20 points)", r.stdout)
        self.assertIn("face.nc: top faced", r.stdout)

    def test_show_needs_a_probe_run(self):
        r = self.machine.run(PROBE_STOCK, "show", "--log", self.machine.logs)
        self.assertEqual(r.returncode, 1)
        self.assertIn("probe-stock.py job", r.stderr)


if __name__ == "__main__":
    unittest.main()
