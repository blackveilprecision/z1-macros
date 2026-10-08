import json
import re
import tempfile
import unittest
from pathlib import Path

from helpers import ORIGINS, ROOT, footprint, levels, moves, plunges_over_stock, run

SCRIPT = ROOT / "surface-stock" / "surface-stock.py"
WIDTH, LENGTH, RADIUS = 69.0, 50.1, 3.175 / 2
# Spelled out rather than left to the defaults, which are meant to be edited for each stock
STOCK = ("--stock-width", WIDTH, "--stock-length", LENGTH, "--stock-height", 19.9, "--target-z", -3, "--origin", "topBackRight")
PROBE_INSET = 3.0


def probe_xy(program):
    """The X/Y of each probe point, from T0 M6 to T1 M6."""
    block = program[program.index("T0 M6"):program.index("T1 M6")]
    return [tuple(map(float, xy)) for xy in re.findall(r"^(?:G90 )?G0 X(\S+) Y(\S+)$", block, re.M)]


def library(folder, flute_length):
    """A made-up tool library with one flat end mill, for Z1_TOOLS."""
    (folder / "tools.json").write_text(json.dumps({"data": [{
        "type": "flat end mill", "unit": "millimeters", "description": "Test 1/8 Flat",
        "geometry": {"DC": 3.175, "SFDM": 3.175, "NOF": 2, "LCF": flute_length, "OAL": 38},
        "start-values": {"presets": [{"name": "Aluminum", "n": 12000, "v_f": 500, "v_f_plunge": 200, "stepdown": 0.2}]},
    }]}))


class SurfaceStock(unittest.TestCase):
    def generate(self, *args):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "job.nc"
            r = run(SCRIPT, *STOCK, *args, "-o", out)
            self.assertEqual(r.returncode, 0, r.stderr)
            return out.read_text()

    def test_default_job(self):
        program = self.generate()
        self.assertTrue(program.startswith(";@MKR|BEGIN"))
        self.assertTrue(program.rstrip().endswith("M02"))
        self.assertIn("M370", program)
        self.assertLess(program.index("T0 M6"), program.index("T1 M6"), "probe before the cutter")
        zs = levels(program)
        self.assertEqual(zs[-1], -3.0)
        self.assertLessEqual(zs[0], 0.2)
        steps = [a - b for a, b in zip(zs, zs[1:])]
        self.assertTrue(all(0 < s <= 0.2 + 1e-9 for s in steps), steps)

    def test_passes_are_equal(self):
        for target, count in ((-3.0004, 16), (-3.05, 17)):  # one Z-3 pass too many; a 0.05 mm last pass
            with self.subTest(target=target):
                zs = levels(self.generate("--target-z", target))
                steps = [a - b for a, b in zip([0.2] + zs, zs)]
                self.assertEqual(len(zs), count)
                self.assertEqual(len(set(zs)), count)
                self.assertAlmostEqual(zs[-1], round(target, 3), places=3)
                self.assertLess(max(steps) - min(steps), 0.002, steps)  # G-code is rounded to 0.001
                self.assertLessEqual(max(steps), 0.2 + 1e-9)

    def test_probe_points_stay_on_the_stock_for_every_origin(self):
        for origin in ORIGINS:
            with self.subTest(origin=origin):
                program = self.generate("--origin", origin, "--probe-clearance", 4)
                (x0, x1), (y0, y1) = footprint(origin, WIDTH, LENGTH)
                pts = probe_xy(program)
                self.assertEqual(len(pts), 20)
                for x, y in pts:
                    self.assertTrue(x0 + PROBE_INSET - 1e-3 <= x <= x1 - PROBE_INSET + 1e-3, (x, y))
                    self.assertTrue(y0 + PROBE_INSET - 1e-3 <= y <= y1 - PROBE_INSET + 1e-3, (x, y))

    def test_later_probe_points_lift_and_search_the_same_distance(self):
        lines = self.generate().splitlines()
        lines = lines[lines.index("T0 M6"):lines.index("T1 M6")]
        first = lines.index("G10 L20 P0 Z0")
        self.assertEqual([s for s in lines if s.startswith("G38.2")], ["G38.2 Z-108 F500", "G38.2 Z-2 F100"])
        self.assertTrue(all(i < first for i, s in enumerate(lines) if s.startswith("G38.2")), "G38.2 only at the first point")
        searches = [i for i, s in enumerate(lines) if s.startswith("G38.3")]
        self.assertEqual(len(searches), 19)
        for i in searches:
            self.assertRegex(lines[i - 1], r"^G0 X\S+ Y\S+$")
            self.assertEqual(lines[i - 2], "G0 Z5", "the probe is lifted before every X/Y move")
            self.assertEqual(lines[i], "G38.3 Z-5 F300", "and searches down exactly as far")
            self.assertEqual(lines[i + 1], "G10 L20 P0 Z0")

    def test_clamp_reach_is_in_the_header(self):
        self.assertIn("the cutter reaches 5.175 mm past the X edges and 1.587 mm past the Y edges", self.generate())

    def test_tool_flute_length_goes_in_the_header(self):
        for lcf, shown in ((19, "19"), (0, "12")):  # 0: the library doesn't give it
            with self.subTest(lcf=lcf), tempfile.TemporaryDirectory() as tmp:
                library(Path(tmp), lcf)
                out = Path(tmp) / "job.nc"
                r = run(SCRIPT, *STOCK, "--tool", "Test 1/8 Flat", "-o", out, env={"Z1_TOOLS": tmp})
                self.assertEqual(r.returncode, 0, r.stderr)
                tool = next(s for s in out.read_text().splitlines() if s.startswith(";@MKR|TOOL|"))
                self.assertIn(f"|shoulderlength={shown}|flutelength={shown}|", tool)

    def test_plunges_land_off_the_stock_for_every_origin(self):
        for origin in ORIGINS:
            with self.subTest(origin=origin):
                program = self.generate("--origin", origin)
                self.assertEqual(plunges_over_stock(program, origin, WIDTH, LENGTH, RADIUS, top=0.2), [])

    def test_cut_covers_the_whole_top(self):
        program = self.generate("--origin", "topFrontLeft")
        cuts = [(x, y) for code, x, y, z, _ in moves(program) if code == "G1" and z == -3.0]
        xs, ys = [x for x, _ in cuts], [y for _, y in cuts]
        self.assertLessEqual(min(xs), -RADIUS)
        self.assertGreaterEqual(max(xs), WIDTH + RADIUS)
        self.assertEqual((min(ys), max(ys)), (0.0, LENGTH))
        rows = sorted(set(ys))
        self.assertLessEqual(max(b - a for a, b in zip(rows, rows[1:])), 2.0 + 1e-9)

    def test_without_probing(self):
        program = self.generate("--probe-grid-x", 0)
        self.assertNotIn("T0", program)
        self.assertNotIn("G38", program)
        self.assertIn("Set Z0 on the highest spot", program)

    def test_probe_only_has_no_spindle(self):
        program = self.generate("--probe-only")
        self.assertIn("M498", program)
        self.assertNotRegex(program, r"\bM0?3\b")
        self.assertNotIn("T1", program)

    def test_rejects_bad_values(self):
        for args in (["--target-z", "1"], ["--stepover", "4"], ["--target-z", "-0.0004"], ["--feed", "5000"], ["--plunge-feed", "1300"],
                     ["--rpm", "20000"]):
            with self.subTest(args=args):
                r = run(SCRIPT, *args, "-o", Path(tempfile.gettempdir()) / "never-written.nc")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("surface-stock:", r.stderr)


if __name__ == "__main__":
    unittest.main()
