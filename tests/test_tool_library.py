"""tool-library/toollib.py, and --tool in the macros, against made-up tool libraries.

Z1_TOOLS points the library at a temporary folder, so neither the repo's own tools,
Makera's downloads nor Fusion's libraries on this computer are read.
"""

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
import toollib  # noqa: E402

SURFACE_STOCK = ROOT / "surface-stock" / "surface-stock.py"


def tool(description, dc, nof=2, presets=(("Aluminum", 11000, 700, 250, 0.3),), unit="millimeters", product_id=""):
    return {
        "type": "flat end mill", "unit": unit, "description": description, "product-id": product_id,
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
        toollib.apply(v, "1/4", "Aluminum", argv=["--feed", "400", "--pass-depth=0.25"])
        self.assertEqual(v, {
            "tool_dia": 6.35, "rpm": 11000, "feed": 500, "plunge_feed": 250, "pass_depth": 0.2,
            "stepover": round(0.63 * 6.35, 3), "origin": "x",
        })
        self.assertIsInstance(v["rpm"], int)

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

    def test_macro_stops_on_an_unknown_tool_or_material(self):
        for args, message in ((["--tool", "nothing"], "no tool matches"), (["--tool", "1/4", "--material", "Brass"], "no Brass preset")):
            with self.subTest(args=args):
                r = run(SURFACE_STOCK, *args, "-o", self.tmp / "job.nc", env=self.env)
                self.assertEqual(r.returncode, 1)
                self.assertIn(message, r.stderr)


if __name__ == "__main__":
    unittest.main()
