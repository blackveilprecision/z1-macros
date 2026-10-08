"""shared/: the number format, origins, pass depths, probe grids and options every macro uses."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from helpers import ORIGINS, ROOT, footprint, run

sys.path.insert(0, str(ROOT))
from shared import cli, toolpath, z1  # noqa: E402

MACROS = [ROOT / name / f"{name}.py" for name in ("face-to-thickness", "surface-stock", "surface-to-lowest-point", "square-side")]


class Z1(unittest.TestCase):
    def test_fmt(self):
        for value, text in ((0, "0"), (0.0, "0"), (-0.0, "0"), (-0.0004, "0"), (0.0004, "0"), (-0.0006, "-0.001"),
                            (12, "12"), (100, "100"), (2.5, "2.5"), (-1.5, "-1.5"), (1.23456, "1.235"), (0.1 + 0.2, "0.3"),
                            (-190.091, "-190.091"), (1e-9, "0")):
            with self.subTest(value=value):
                self.assertEqual(z1.fmt(value), text)

    def test_origins_match_the_tests_own_table(self):
        self.assertEqual(z1.ORIGINS, ORIGINS, "helpers.ORIGINS is the tests' independent copy")

    def test_to_program_puts_the_origin_corner_at_zero(self):
        w, l = 65.4, 50.4
        for origin in ORIGINS:
            with self.subTest(origin=origin):
                (x0, x1), (y0, y1) = footprint(origin, w, l)
                self.assertEqual(z1.to_program(origin, w, l, [(0, 0), (w, l)]), [(x0, y0), (x1, y1)])

    def test_start(self):
        lines = z1.start("square-side", (65.4, 50.4, 29), "topFrontLeft", 6.35, 99.5, ["; a note"], g54_z=-87.6, flute_length=25.4)
        self.assertIn(";@MKR|TOOLPATH|number=1|tool_number=1|name=[T1]Square Side", lines)
        self.assertIn(";@MKR|TIME|seconds=100", lines)
        self.assertIn("|shoulderlength=25.4|flutelength=25.4|", next(s for s in lines if s.startswith(";@MKR|TOOL|")))
        self.assertLess(lines.index("M498 ; G54 Z should read -87.600: stop the job if not"), lines.index("T1 M6"))
        self.assertEqual(lines[-2:], ["T1 M6", "M7"])
        self.assertNotIn("M498", "\n".join(z1.start("surface-stock", (1, 1, 1), "topCenter", 3.175, 0, [])))

    def test_z_probe(self):
        self.assertEqual(z1.z_probe(32.7, -25.2), [
            "G53 G0 Z-3", "G90 G0 X32.7 Y-25.2", "G38.2 Z-108 F500", "G91 G0 Z1", "G38.2 Z-2 F100", "G10 L20 P0 Z0", "G90",
        ])
        self.assertEqual(z1.z_probe(0, 0, fast=100, g91=True), [
            "G53 G0 Z-3", "G90 G0 X0 Y0", "G91 G38.2 Z-108 F100", "G91 G0 Z1", "G38.2 Z-2 F100", "G10 L20 P0 Z0", "G90",
        ])

    def test_cut_adds_the_time_move_by_move(self):
        lines, seconds = z1.cut(-0.2, 3.0, [(0, 0), (10, 0), (10, 5)], 200, 500, 30.0)
        self.assertEqual(lines, ["G1 Z-0.2 F200", "G1 X10 F500", "G1 Y5"])
        self.assertEqual(seconds, ((30.0 + abs(3.0 - -0.2) / 200 * 60) + 10.0 / 500 * 60) + 5.0 / 500 * 60)


    def test_face_turns_every_other_level_around(self):
        path = [(0, 0), (10, 0)]
        lines, seconds = z1.face(path, [-0.2, -0.4], 12000, 3.0, 200, 500, 30.0)
        self.assertEqual(lines, [
            "G0 X0 Y0", "S12000 M3", "G0 Z15", "G0 Z3",
            "; Level 1/2 Z-0.2", "G1 Z-0.2 F200", "G1 X10 F500",
            "; Level 2/2 Z-0.4", "G1 Z-0.4 F200", "G1 X0 F500",
            "G0 Z15", "M9", "M05", "G28", "M02",
        ])
        _, first = z1.cut(-0.2, 3.0, path, 200, 500, 30.0)
        self.assertEqual(seconds, z1.cut(-0.4, -0.2, path[::-1], 200, 500, first)[1])


class Toolpath(unittest.TestCase):
    def test_equal_levels(self):
        self.assertEqual(toolpath.equal_levels(0.0, -1.0, 0.3), [-0.25, -0.5, -0.75, -1.0])
        self.assertEqual(len(toolpath.equal_levels(0.0, -0.6, 0.2)), 3, "0.6 / 0.2 is just under 3 in floating point")
        zs = toolpath.equal_levels(1.0, -14.0, 0.2)
        self.assertEqual(len(zs), 75)
        self.assertEqual(zs[-1], -14.0)

    def test_levels_round_to_what_fmt_writes(self):
        # -3.0004 is written Z-3: counted unrounded it was one more pass, the last two both at Z-3
        zs = toolpath.equal_levels(0.2, -3.0004, 0.2)
        self.assertEqual(len(zs), 16)
        self.assertEqual(zs[-1], -3.0)
        self.assertEqual(toolpath.equal_levels(0.0, -0.0004, 0.2), [], "nothing to cut once rounded")

    def test_spread(self):
        self.assertEqual(toolpath.spread(10, 3, 3), [3.0, 5.0, 7.0])
        self.assertEqual(toolpath.spread(10, 2, 0), [0.0, 10.0])
        self.assertEqual(toolpath.spread(10, 1, 3), [5.0])
        self.assertEqual(toolpath.spread(5, 4, 3), [2.5], "no room inside the inset: the middle")

    def test_probe_grid_starts_nearest_the_origin(self):
        for origin, first in (("topFrontLeft", (3, 3)), ("topBackRight", (62.4, 47.4)), ("topFrontRight", (62.4, 3))):
            with self.subTest(origin=origin):
                pts = toolpath.probe_grid(origin, 65.4, 50.4, 5, 4, 3)
                self.assertEqual(len(pts), 20)
                self.assertAlmostEqual(pts[0][0], first[0], places=9)
                self.assertAlmostEqual(pts[0][1], first[1], places=9)
                self.assertEqual(pts[4][1], pts[0][1])
                self.assertEqual(pts[5][0], pts[4][0], "serpentine: the next row starts where the last ended")

    def test_raster_covers_the_top(self):
        path, step = toolpath.raster(69, 50.4, 3.175, 2)
        self.assertEqual(path[0], (-(3.175 / 2 + z1.LEAD), 0.0))
        self.assertAlmostEqual(path[-1][1], 50.4, places=9)
        self.assertLessEqual(step, 2)

    def test_reach(self):
        path, _ = toolpath.raster(69, 50.4, 3.175, 2)
        x, y = 3.175 + z1.LEAD, 3.175 / 2  # a diameter plus LEAD, and a radius
        self.assertAlmostEqual(max(u for u, _ in path) + 3.175 / 2 - 69, x, places=9)
        self.assertAlmostEqual(min(u for u, _ in path) - 3.175 / 2, -x, places=9)
        self.assertAlmostEqual(max(t for _, t in path) + 3.175 / 2 - 50.4, y, places=9)
        self.assertEqual(toolpath.reach(3.175), "the cutter reaches 5.175 mm past the X edges and 1.587 mm past the Y edges")


class Cli(unittest.TestCase):
    def test_checks(self):
        v = {"a": 1, "b": 0, "feed": 1300, "plunge_feed": 200, "rpm": 12000}
        self.assertEqual(cli.positive(v, ("a", "b")), ["b must be positive"])
        self.assertEqual(cli.feeds_and_rpm(v), ["feeds must be between 0 and 1200 mm/min"])
        cli.stop("macro", [])
        with self.assertRaises(SystemExit) as e:
            cli.stop("macro", ["one", "two"])
        self.assertEqual(str(e.exception), "macro: one; two")

    def test_options_have_to_be_spelled_out(self):
        # so a command means the same thing after an option is added
        with tempfile.TemporaryDirectory() as tmp:
            for script in MACROS:
                with self.subTest(script=script.name):
                    log = ["--log", tmp] if script.stem in ("surface-to-lowest-point", "square-side") else []
                    r = run(script, "--pass", 0.1, *log, "-o", Path(tmp) / "job.nc", env={"Z1_STOCK": tmp})
                    self.assertEqual(r.returncode, 2)
                    self.assertIn("unrecognized arguments: --pass", r.stderr)

    def test_help_shows_the_defaults(self):
        p = cli.parser("doc")
        cli.add_variables(p, {"stock_width": 69.0, "origin": "topBackRight"}, {"origin": z1.ORIGINS})
        text = " ".join(p.format_help().split())
        self.assertIn("--stock-width STOCK_WIDTH default: 69.0", text)
        self.assertIn("default: topBackRight", text)

    def test_runs_without_tool_library(self):
        # apply_tool() imports toollib only for --tool
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shutil.copytree(ROOT / "shared", tmp / "shared", ignore=shutil.ignore_patterns("__pycache__"))
            for name in ("face-to-thickness", "surface-stock"):
                with self.subTest(script=name):
                    (tmp / name).mkdir()
                    shutil.copy(ROOT / name / f"{name}.py", tmp / name)
                    out = tmp / f"{name}.nc"
                    r = run(tmp / name / f"{name}.py", "-o", out, env={"Z1_STOCK": str(tmp)})
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertTrue(out.read_text().endswith("M02\n"))


if __name__ == "__main__":
    unittest.main()
