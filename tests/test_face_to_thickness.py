import json
import re
import tempfile
import unittest
from pathlib import Path

from helpers import ORIGINS, ROOT, footprint, levels, plunges_over_stock, run

SCRIPT = ROOT / "face-to-thickness" / "face-to-thickness.py"
SURFACE_STOCK = ROOT / "surface-stock" / "surface-stock.py"
WIDTH, LENGTH, RADIUS = 69.0, 50.1, 3.175 / 2
# Spelled out rather than left to the defaults, which are meant to be edited for each stock
STOCK = ("--stock-width", WIDTH, "--stock-length", LENGTH, "--origin", "topBackRight")
FACE = (*STOCK, "--stock-height", 17, "--final-height", 10)
INSET = 3.0  # as surface-stock's probe points


def generate(test, script, *args):
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "job.nc"
        r = run(script, *args, "-o", out)
        test.assertEqual(r.returncode, 0, r.stderr)
        return out.read_text()


class FaceToThickness(unittest.TestCase):
    def test_default_job_takes_17_to_10(self):
        program = generate(self, SCRIPT, *FACE)
        self.assertIn("Faces 17 -> 10 mm thick", program)
        self.assertLess(program.index("T0 M6"), program.index("T1 M6"), "probe before the cutter")
        zs = levels(program)
        self.assertEqual(len(zs), 35)
        self.assertEqual(zs[-1], -7.0)
        steps = [a - b for a, b in zip([0.0] + zs, zs)]
        self.assertTrue(all(abs(s - 0.2) < 1e-3 for s in steps), steps)

    def test_passes_are_equal_when_the_depth_does_not_divide(self):
        zs = levels(generate(self, SCRIPT, *FACE, "--stock-height", 16.9))
        steps = [a - b for a, b in zip([0.0] + zs, zs)]
        self.assertEqual(len(zs), 35)
        self.assertAlmostEqual(zs[-1], -6.9, places=3)
        self.assertLess(max(steps) - min(steps), 0.002, steps)  # G-code is rounded to 0.001
        self.assertLessEqual(max(steps), 0.2)

    def test_plunges_land_off_the_stock_for_every_origin(self):
        for origin in ORIGINS:
            with self.subTest(origin=origin):
                program = generate(self, SCRIPT, *FACE, "--origin", origin)
                self.assertEqual(plunges_over_stock(program, origin, WIDTH, LENGTH, RADIUS), [])

    def test_probes_once_on_the_stock_for_every_origin(self):
        for origin in ORIGINS:
            with self.subTest(origin=origin):
                program = generate(self, SCRIPT, *FACE, "--origin", origin)
                block = program[program.index("T0 M6"):program.index("T1 M6")]
                self.assertEqual(re.findall(r"^G38.*", block, re.M), ["G38.2 Z-108 F500", "G38.2 Z-2 F100"])
                [(x, y)] = [tuple(map(float, xy)) for xy in re.findall(r"^(?:G90 )?G0 X(\S+) Y(\S+)$", block, re.M)]
                (x0, x1), (y0, y1) = footprint(origin, WIDTH, LENGTH)
                self.assertTrue(x0 + INSET <= x <= x1 - INSET and y0 + INSET <= y <= y1 - INSET, (x, y))

    def test_cuts_the_same_as_surface_stock(self):
        for height in (17, 16.9):  # 16.9: equal passes of 0.197 mm in both
            with self.subTest(height=height):
                ours = generate(self, SCRIPT, *FACE, "--stock-height", height)
                theirs = generate(
                    self, SURFACE_STOCK, *STOCK, "--probe-grid-x", 0, "--top-margin", 0, "--target-z", 10 - height, "--stock-height", 17
                )
                self.assertEqual(ours[ours.index("\nM7\n"):], theirs[theirs.index("\nM7\n"):])

    def test_clamp_reach_is_in_the_header(self):
        self.assertIn("the cutter reaches 5.175 mm past the X edges and 1.587 mm past the Y edges", generate(self, SCRIPT, *FACE))

    def test_tool_flute_length_goes_in_the_header(self):
        for lcf, shown in ((19, "19"), (0, "12")):  # 0: the library doesn't give it
            with self.subTest(lcf=lcf), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "tools.json").write_text(json.dumps({"data": [{
                    "type": "flat end mill", "unit": "millimeters", "description": "Test 1/8 Flat",
                    "geometry": {"DC": 3.175, "SFDM": 3.175, "NOF": 2, "LCF": lcf, "OAL": 38},
                    "start-values": {"presets": [{"name": "Aluminum", "n": 12000, "v_f": 500, "v_f_plunge": 200, "stepdown": 0.2}]},
                }]}))
                out = Path(tmp) / "job.nc"
                r = run(SCRIPT, *FACE, "--tool", "Test 1/8 Flat", "-o", out, env={"Z1_TOOLS": tmp})
                self.assertEqual(r.returncode, 0, r.stderr)
                tool = next(s for s in out.read_text().splitlines() if s.startswith(";@MKR|TOOL|"))
                self.assertIn(f"|shoulderlength={shown}|flutelength={shown}|", tool)

    def test_without_probing(self):
        program = generate(self, SCRIPT, *FACE, "--no-probe")
        self.assertNotIn("T0", program)
        self.assertNotIn("G38", program)

    def test_rejects_bad_values(self):
        for args in (["--final-height", "17"], ["--final-height", "0"], ["--final-height", "16.9996"], ["--stepover", "4"],
                     ["--feed", "1300"], ["--plunge-feed", "1300"], ["--rpm", "14000"]):
            with self.subTest(args=args):
                r = run(SCRIPT, *args, "-o", Path(tempfile.gettempdir()) / "never-written.nc")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("face-to-thickness:", r.stderr)


if __name__ == "__main__":
    unittest.main()
