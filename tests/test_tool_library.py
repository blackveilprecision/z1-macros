"""tool-library/toollib.py, and --tool in the macros, against made-up tool libraries.

Z1_TOOLS points the library at a temporary folder, so neither the repo's own tools,
Makera's downloads nor Fusion's libraries on this computer are read.
"""

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from helpers import ROOT, levels, run

sys.path.insert(0, str(ROOT / "tool-library"))
sys.path.insert(0, str(ROOT))
import toollib  # noqa: E402
from shared import cli  # noqa: E402

SURFACE_STOCK = ROOT / "surface-stock" / "surface-stock.py"


def tool(description, dc, nof=2, presets=(("Aluminum", 11000, 700, 250, 0.3),), unit="millimeters", product_id="",
         kind="flat end mill"):
    return {
        "type": kind, "unit": unit, "description": description, "product-id": product_id,
        "geometry": {"DC": dc, "SFDM": dc, "NOF": nof, "LCF": 3 * dc, "OAL": 50},
        "start-values": {"presets": [
            {"name": n, "n": rpm, "v_f": feed, "v_f_plunge": plunge, "stepdown": depth, "use-stepdown": True}
            for n, rpm, feed, plunge, depth in presets
        ]},
    }


class ToolLibrary(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        first, second = self.tmp / "a custom", self.tmp / "b makera"
        first.mkdir()
        second.mkdir()
        (first / "quarter.json").write_text(json.dumps({"data": [
            tool("Test 1/4\" 3 Flute End Mill", 6.35, 3, product_id="QTR-1"),
        ]}))
        with zipfile.ZipFile(second / "Makera Test.tools", "w") as z:  # an exported .tools file
            z.writestr("tools.json", json.dumps({"data": [
                tool("Test 1/4\" 3 Flute End Mill", 6.0),  # same description: the first library wins
                tool("Test 1/8\" 2 Flute End Mill", 3.175, presets=(("Aluminum", 12000, 500, 200, 0.2), ("Hardwood", 12000, 1000, 300, 1))),
                tool("Test 1/8\" 2 Flute End Mill in inches", 0.125, unit="inches"),
            ]}))
        self.env = {"Z1_TOOLS": os.pathsep.join(map(str, (first, second)))}
        patcher = mock.patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reads_json_and_tools_files_first_library_first(self):
        found = {t.description: t for t in toollib.tools()}
        self.assertEqual(sorted(found), ['Test 1/4" 3 Flute End Mill', 'Test 1/8" 2 Flute End Mill'])
        self.assertEqual(found['Test 1/4" 3 Flute End Mill'].dia, 6.35, "the earlier library's tool wins")
        self.assertEqual(found['Test 1/4" 3 Flute End Mill'].source, "quarter")

    def test_find(self):
        self.assertEqual(toollib.find("1/8").dia, 3.175)
        self.assertEqual(toollib.find("qtr-1").dia, 6.35, "by product id")
        for text, message in (("End Mill", "2 tools match"), ("ball", "no tool matches")):
            with self.subTest(text=text), self.assertRaises(SystemExit) as e:
                toollib.find(text)
            self.assertIn(message, str(e.exception))

    def test_presets(self):
        t = toollib.find("1/8")
        self.assertEqual(t.preset("hardwood").feed, 1000)
        with self.assertRaises(SystemExit) as e:
            t.preset("Brass")
        self.assertIn("Aluminum, Hardwood", str(e.exception))

    def test_apply_fills_what_the_command_line_didnt_set(self):
        v = {"tool_dia": 3.175, "rpm": 12000, "feed": 500, "plunge_feed": 200, "pass_depth": 0.2, "stepover": 2.0, "origin": "x"}
        toollib.apply(v, "1/4", "Aluminum", given={"feed", "pass_depth"})
        self.assertEqual(v, {
            "tool_dia": 6.35, "rpm": 11000, "feed": 500, "plunge_feed": 250, "pass_depth": 0.2,
            "stepover": round(0.63 * 6.35, 3), "origin": "x",
        })
        self.assertIsInstance(v["rpm"], int)

    def test_apply_takes_the_stepover_from_the_diameter_given(self):
        v = {"tool_dia": 3.175, "stepover": 2.0}
        toollib.apply(v, "1/4", "Aluminum", given={"tool_dia"})
        self.assertEqual(v, {"tool_dia": 3.175, "stepover": round(0.63 * 3.175, 3)}, "the --tool-dia the user gave")

    def test_apply_flute_length(self):
        for given, expected in ((set(), 19.05), ({"flute_length"}, 20)):
            with self.subTest(given=given):
                v = {"flute_length": 20.0}
                toollib.apply(v, "1/4", "Aluminum", given=given)
                self.assertAlmostEqual(v["flute_length"], expected)
        t = toollib.find("1/4")
        t.flute_length = 0  # the library has no LCF
        v = {"flute_length": 20.0}
        toollib.apply(v, t, "Aluminum")
        self.assertEqual(v, {"flute_length": 20.0})

    def test_given_comes_from_the_parser(self):
        # what argparse parsed, however it was spelled
        p = cli.parser("doc")
        cli.add_variables(p, {"feed": 500, "pass_depth": 0.2, "rpm": 12000, "origin": "a"}, {"origin": ("a", "b")})
        self.assertEqual(p.parse_args([]).given, set())
        args = p.parse_args(["--feed=400", "--pass-depth", "0.25", "--origin", "b"])
        self.assertEqual(args.given, {"feed", "pass_depth", "origin"})
        self.assertEqual((args.feed, args.pass_depth), (400, 0.25))
        v = {"feed": args.feed, "pass_depth": args.pass_depth, "rpm": args.rpm}
        toollib.apply(v, "1/4", "Aluminum", given=args.given)
        self.assertEqual(v, {"feed": 400, "pass_depth": 0.25, "rpm": 11000})

    def test_skips_files_that_arent_tool_libraries(self):
        folder = Path(self.env["Z1_TOOLS"].split(os.pathsep)[0])
        (folder / "latin-1.json").write_bytes('{"data": [], "description": "Fräse"}'.encode("latin-1"))
        (folder / "list.json").write_text("[1, 2]")
        (folder / "data.json").write_text('{"data": 5}')
        self.assertEqual(len(toollib.tools()), 2)

    def test_fetch_downloads_again_for_another_commit(self):
        cache, references = self.tmp / "cache", self.tmp / "makera.json"
        references.write_text(json.dumps({"repo": "r", "commit": "new", "folder": "f", "libraries": ["One.tools"]}))
        cache.mkdir()
        (cache / "One.tools").write_text('{"data": []}')
        (cache / "COMMIT").write_text("old\n")
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({"data": [tool("New", 3)]}).encode()
        with mock.patch.object(toollib, "CACHE", cache), mock.patch.object(toollib, "REFERENCES", references), \
                mock.patch.object(toollib.urllib.request, "urlopen", return_value=response) as urlopen:
            self.assertEqual(toollib.fetch(quiet=True), [cache / "One.tools"])
            self.assertEqual(urlopen.call_count, 1)
            self.assertIn("New", (cache / "One.tools").read_text())
            self.assertEqual((cache / "COMMIT").read_text().strip(), "new")
            with mock.patch.object(Path, "write_text") as write:
                toollib.fetch(quiet=True)
            self.assertEqual(urlopen.call_count, 1, "up to date")
            write.assert_not_called()  # nor is COMMIT written again
            (cache / "COMMIT").write_text("old\n")
            urlopen.side_effect = OSError("offline")
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(toollib.fetch(quiet=True), [cache / "One.tools"], "the old copy is still used")
            self.assertIn("using the copy in cache/", err.getvalue())
            self.assertEqual((cache / "COMMIT").read_text().strip(), "old", "and still counts as old")

    def test_command_line(self):
        r = run(ROOT / "tool-library" / "toollib.py", "show", "1/8", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Hardwood", r.stdout)
        r = run(ROOT / "tool-library" / "toollib.py", "list", "1/4", env=self.env)
        self.assertEqual(r.stdout.count("\n"), 1)

    def test_makera_reference_is_pinned(self):
        ref = toollib.references()
        self.assertEqual(ref["repo"], "MakeraInc/CarveraProfiles")
        self.assertRegex(ref["commit"], r"^[0-9a-f]{40}$")
        self.assertTrue(ref["libraries"] and all(n.endswith(".tools") for n in ref["libraries"]))

    def test_macro_takes_the_tool(self):
        out = self.tmp / "job.nc"
        r = run(SURFACE_STOCK, "--tool", "1/4", "-o", out, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('tool: Test 1/4" 3 Flute End Mill', r.stdout)
        program = out.read_text()
        self.assertIn("S11000 M3", program)
        self.assertIn("F700", program)
        self.assertIn("F250", program)
        self.assertIn("6.35mm Flat End", program)
        zs = levels(program)
        self.assertTrue(all(a - b <= 0.3 + 1e-9 for a, b in zip(zs, zs[1:])), zs)
        rows = sorted({float(m) for m in re.findall(r"^G1 Y(-?[\d.]+)", program, re.M)})
        self.assertLessEqual(max(b - a for a, b in zip(rows, rows[1:])), round(0.63 * 6.35, 3) + 1e-9)

    def test_macro_options_beat_the_tool(self):
        out = self.tmp / "job.nc"
        r = run(SURFACE_STOCK, "--tool", "1/4", "--rpm", "9000", "-o", out, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("S9000 M3", out.read_text())

    def test_macro_pass_depth_beats_the_tool(self):
        # spelled out, as it has to be (test_shared checks that --pass is refused)
        out = self.tmp / "job.nc"
        r = run(SURFACE_STOCK, "--tool", "1/4", "--pass-depth", "0.1", "-o", out, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("0.1 mm passes", r.stdout)
        zs = levels(out.read_text())
        self.assertTrue(all(a - b <= 0.1 + 1e-9 for a, b in zip(zs, zs[1:])), zs)

    def test_macro_takes_the_stepover_from_tool_dia(self):
        r = run(SURFACE_STOCK, "--tool", "1/4", "--tool-dia", "3", "-o", self.tmp / "job.nc", env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3 mm, 11000 rpm", r.stdout)
        self.assertIn(f"{round(0.63 * 3, 3):g} mm stepover", r.stdout)

    def test_macro_option_at_its_default_beats_the_tool(self):
        out = self.tmp / "job.nc"
        r = run(SURFACE_STOCK, "--tool", "1/4", "--rpm=12000", "-o", out, env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("S12000 M3", out.read_text())

    def test_macro_takes_only_flat_end_mills(self):
        other = self.tmp / "c other"
        other.mkdir()
        (other / "other.json").write_text(json.dumps({"data": [
            tool("Test 1/8\" Ball", 3.175, kind="ball end mill"),
            tool("Test 90 deg Chamfer", 6, kind="chamfer mill"),
            tool("Test 3mm Drill", 3, kind="drill"),
        ]}))
        env = {"Z1_TOOLS": os.pathsep.join((self.env["Z1_TOOLS"], str(other)))}
        for name, kind in (("Ball", "ball end mill"), ("Chamfer", "chamfer mill"), ("Drill", "drill")):
            with self.subTest(kind=kind):
                out = self.tmp / "job.nc"
                r = run(SURFACE_STOCK, "--tool", name, "-o", out, env=env)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(r.stderr.strip(), f"surface-stock: --tool must be a flat end mill (got {kind})")
                self.assertFalse(out.exists())

    def test_macro_stops_on_an_unknown_tool_or_material(self):
        for args, message in ((["--tool", "nothing"], "no tool matches"), (["--tool", "1/4", "--material", "Brass"], "no Brass preset")):
            with self.subTest(args=args):
                r = run(SURFACE_STOCK, *args, "-o", self.tmp / "job.nc", env=self.env)
                self.assertEqual(r.returncode, 1)
                self.assertIn(message, r.stderr)


if __name__ == "__main__":
    unittest.main()
