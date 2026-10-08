"""square-side against probe-stock's reference and made-up Studio logs (the real one is never read)."""

import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import REFMZ, ROOT, X0, Y0, Machine, levels, moves, plunges_over_stock

SCRIPT = ROOT / "square-side" / "square-side.py"
WIDTH, LENGTH, RADIUS = 65.4, 50.4, 6.35 / 2
BED = -9.952
# probe-stock's job, spelled out rather than left to its defaults
STOCK = (
    "--stock-width", WIDTH, "--stock-length", LENGTH, "--stock-height", 29, "--probe-grid-x", 5, "--probe-grid-y", 4,
    "--probe-inset", 3, "--probe-clearance", 5, "--fence-x", -7.5, "--fence-y", 77, "--fence-height", 5,
    "--rod-dia", 2, "--rod-tool", 9999, "--side-clearance", 5, "--clamp-height", 10,
)
SIDE = (63.63, 64.03, 64.33, 64.53, 64.77)  # the right side's width at each probed Y, like the real stock
CUT = ("--tool-dia", 6.35, "--flute-length", 25.4, "--pass-depth", 0.2, "--stepover", 4, "--finish-allowance", 0.2,
       "--feed", 500, "--plunge-feed", 200, "--rpm", 12000, "--clamp-height", 10, "--round-to", 0.5, "--overlap", 1)


def z(thickness):
    """The G54 Z an M498 prints after Z0 is set on a surface this far above the bed."""
    return round(BED + thickness + REFMZ, 3)


class SquareSide(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.machine = Machine(self.tmp)

    def probe(self, *args, widths=SIDE, origin="topFrontLeft", depth=15):
        """probe-stock with the rod on the X side across from the origin, then the probe on the plate and the top."""
        far = -1 if origin.endswith("Right") or "Right" in origin else 1
        top = z(29.0)
        prints = [top]
        # each side touch moves X0 by how far that side is from where WIDTH puts it (see probe-stock's side_touch)
        prints += [(round(X0 + far * (w - WIDTH), 3), Y0, top) for w in widths]
        prints += [(X0, Y0, top), (X0, Y0, z(5))] + [(X0, Y0, top)] * 20
        r, _ = self.machine.probe(*STOCK, "--origin", origin, "--side-x-points", len(widths), "--side-depth", depth,
                                  *args, prints=prints)
        self.assertEqual(r.returncode, 0, r.stderr)

    def generate(self, *args, ok=True):
        out = self.tmp / "side.nc"
        if out.exists():
            out.unlink()
        r = self.machine.run(SCRIPT, "--log", self.machine.logs, "-o", out, *CUT, *args)
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
            return r, out.read_text()
        return r, None

    def test_cuts_the_right_side_to_the_narrowest_width_rounded_down(self):
        self.probe()
        r, program = self.generate()
        self.assertIn("Width to leave: 63.5 mm", r.stdout)
        xs = {x for code, x, _, _, _ in moves(program) if code == "G1" and x is not None}
        self.assertEqual(xs, {63.5 + 0.2 + RADIUS, 63.5 + RADIUS}, "roughing leaves 0.2, finishing takes it")
        zs = levels(program)
        self.assertEqual(len(zs), 76, "75 layers of 0.2 mm, then the finishing pass")
        self.assertAlmostEqual(zs[-1], -15.0, places=3, msg="half of 29, plus half the 1 mm overlap")
        self.assertAlmostEqual(min(zs), -15.0, places=3)
        self.assertEqual(re.findall(r"^M498 ; G54 Z should read (\S+):", program, re.M), [f"{z(29.0):.3f}"])
        for banned in ("G38", "G10", "T0"):
            self.assertNotIn(banned, program, "it doesn't probe or move Z0")

    def test_every_pass_starts_and_ends_beyond_the_stock(self):
        self.probe()
        _, program = self.generate()
        self.assertEqual(plunges_over_stock(program, "topFrontLeft", WIDTH, LENGTH, RADIUS, top=1e9), [])
        ys = {round(y, 3) for code, _, y, _, _ in moves(program) if code == "G1" and y is not None}
        self.assertEqual(ys, {round(-RADIUS - 2, 3), round(LENGTH + RADIUS + 2, 3)})

    def test_the_finishing_pass_climbs(self):
        self.probe()
        _, program = self.generate()
        finish = program[program.index("; Finishing pass"):]
        y_moves = [y for code, _, y, _, _ in moves(finish) if code == "G1" and y is not None]
        self.assertGreater(y_moves[-1], y_moves[0], "with the side to its left, a clockwise cutter climbs along +Y")

    def test_more_than_one_roughing_pass_when_the_side_is_far_out(self):
        self.probe()
        _, program = self.generate("--stepover", 0.5)
        layer = program[:program.index("; Layer 2/")]
        xs = [x for code, x, _, _, line in moves(layer) if line.startswith("G1 Y")]  # where each pass along the side runs
        self.assertEqual(len(xs), 3, "1.27 mm to cut plus 0.3 margin, less 0.2 left: three passes of <= 0.5")
        self.assertEqual(xs, sorted(xs, reverse=True), "outside in")
        self.assertAlmostEqual(xs[-1], 63.5 + 0.2 + RADIUS, places=3)

    def test_final_width(self):
        self.probe()
        r, program = self.generate("--final-width", 63)
        self.assertIn(f"G1 Y{LENGTH + RADIUS + 2:g}", program)
        self.assertIn("X66.175", program)

    def test_the_left_side_for_a_right_origin(self):
        self.probe(origin="topFrontRight")
        _, program = self.generate()
        xs = {x for code, x, _, _, _ in moves(program) if code == "G1" and x is not None}
        self.assertEqual(xs, {-(63.5 + 0.2 + RADIUS), -(63.5 + RADIUS)})
        finish = program[program.index("; Finishing pass"):]
        y_moves = [y for code, _, y, _, _ in moves(finish) if code == "G1" and y is not None]
        self.assertLess(y_moves[-1], y_moves[0], "with the side to its right, it climbs along -Y")

    def test_refuses_what_it_cant_cut_safely(self):
        cases = {
            "the clamps": (("--clamp-height", 13), "clamps"),
            "the flutes": (("--flute-length", 12), "flutes"),
            "nothing to cut": (("--final-width", 65), "cuts nothing"),
        }
        self.probe()
        for what, (args, message) in cases.items():
            with self.subTest(what):
                r, _ = self.generate(*args, ok=False)
                self.assertEqual(r.returncode, 1)
                self.assertIn(message, r.stderr)

    def test_the_rod_touched_about_as_deep_as_the_cut(self):
        # As on the machine: the rod set Z0 on a top point 0.04 below the highest, so it went 0.04 deeper than the cut
        top, low = z(29.0), z(28.96)
        prints = [low] + [(round(X0 + w - WIDTH, 3), Y0, low) for w in SIDE] + [(X0, Y0, low), (X0, Y0, z(5))]
        prints += [(X0, Y0, top)] + [(X0, Y0, low)] * 19
        r, _ = self.machine.probe(*STOCK, "--side-x-points", len(SIDE), "--side-depth", 15, prints=prints)
        self.assertEqual(r.returncode, 0, r.stderr)
        r, _ = self.generate()
        self.assertIn("to 14.00 mm above the bed", r.stdout, "the cut stays at half plus the overlap, not the rod's depth")
        self.machine.play(self.tmp / "side.nc", [low])
        r, _ = self.generate(ok=False)
        self.assertIn("63.500 wide at most", r.stderr, "and counts as cut")

    def test_a_rod_touch_a_little_shallower_than_the_cut_is_enough(self):
        self.probe(depth=14.5)
        r, _ = self.generate()
        self.assertIn("to 14.00 mm above the bed", r.stdout)

    def test_a_shallow_rod_touch_is_enough(self):
        self.probe(depth=2)
        r, program = self.generate()
        self.assertIn("13.0 mm above the bottom of the cut", r.stdout)
        self.assertAlmostEqual(levels(program)[-1], -15.0, places=3, msg="the cut doesn't follow the rod")
        self.machine.play(self.tmp / "side.nc", [z(29.0)])
        r, _ = self.generate(ok=False)
        self.assertIn("63.500 wide at most", r.stderr, "the band the rod read is inside the cut")

    def test_a_side_already_narrower_than_the_cut_stays(self):
        self.probe(widths=(64.2, 63.7, 63.3, 63.35, 63.1))
        self.generate("--final-width", 63.4)
        self.machine.play(self.tmp / "side.nc", [z(29.0)])
        r = self.machine.run(ROOT / "probe-stock" / "probe-stock.py", "show", "--log", self.machine.logs)
        widths = [float(w) for w in re.findall(r"^\s+[\d.]+\s+([\d.]+)\s+[\d.]+\s+[\d.]+$", r.stdout, re.M)][:5]
        self.assertEqual(widths, [63.4, 63.4, 63.3, 63.35, 63.1])

    def test_needs_side_touches(self):
        r, _ = self.machine.probe(*STOCK, prints=[z(5)] + [z(29.0)] * 20)
        self.assertEqual(r.returncode, 0, r.stderr)
        r, _ = self.generate(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--side-x-points", r.stderr)

    def test_once_it_has_run_the_side_counts_as_cut(self):
        self.probe()
        self.generate()
        jobs = json.loads((self.tmp / "jobs.json").read_text())
        self.assertEqual(jobs[-1]["effect"]["type"], "side")
        self.machine.play(self.tmp / "side.nc", [z(29.0)])
        r, _ = self.generate(ok=False)
        self.assertEqual(r.returncode, 1)
        self.assertIn("63.500 wide at most", r.stderr)


if __name__ == "__main__":
    unittest.main()
