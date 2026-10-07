import tempfile
import unittest
from pathlib import Path

from helpers import ORIGINS, ROOT, levels, moves, plunges_over_stock, run

SCRIPT = ROOT / "surface-stock" / "surface-stock.py"
WIDTH, LENGTH, RADIUS = 69.0, 50.1, 3.175 / 2
# Spelled out rather than left to the defaults, which are meant to be edited for each stock
STOCK = ("--stock-width", WIDTH, "--stock-length", LENGTH, "--stock-height", 19.9, "--target-z", -3, "--origin", "topBackRight")


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
        for args in (["--target-z", "1"], ["--stepover", "4"], ["--feed", "5000"], ["--rpm", "20000"]):
            with self.subTest(args=args):
                r = run(SCRIPT, *args, "-o", Path(tempfile.gettempdir()) / "never-written.nc")
                self.assertNotEqual(r.returncode, 0)
                self.assertIn("surface-stock:", r.stderr)


if __name__ == "__main__":
    unittest.main()
