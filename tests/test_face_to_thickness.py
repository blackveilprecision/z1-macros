import tempfile
import unittest
from pathlib import Path

from helpers import ORIGINS, ROOT, levels, plunges_over_stock, run

SCRIPT = ROOT / "face-to-thickness" / "face-to-thickness.py"
SURFACE_STOCK = ROOT / "surface-stock" / "surface-stock.py"
WIDTH, LENGTH, RADIUS = 69.0, 50.1, 3.175 / 2
# Spelled out rather than left to the defaults, which are meant to be edited for each stock
STOCK = ("--stock-width", WIDTH, "--stock-length", LENGTH, "--origin", "topBackRight")
FACE = (*STOCK, "--stock-height", 17, "--final-height", 10)


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

    def test_cuts_the_same_as_surface_stock(self):
        ours = generate(self, SCRIPT, *FACE)
        theirs = generate(
            self, SURFACE_STOCK, *STOCK, "--probe-grid-x", 0, "--top-margin", 0, "--target-z", -7, "--stock-height", 17
        )
        self.assertEqual(ours[ours.index("\nM7\n"):], theirs[theirs.index("\nM7\n"):])

    def test_without_probing(self):
        program = generate(self, SCRIPT, *FACE, "--no-probe")
        self.assertNotIn("T0", program)
        self.assertNotIn("G38", program)

    def test_rejects_bad_values(self):
        for args in (["--final-height", "17"], ["--final-height", "0"], ["--stepover", "4"]):
            with self.subTest(args=args):
                r = run(SCRIPT, *args, "-o", Path(tempfile.gettempdir()) / "never-written.nc")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("face-to-thickness:", r.stderr)


if __name__ == "__main__":
    unittest.main()
