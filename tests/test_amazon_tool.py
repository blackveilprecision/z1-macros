"""amazon-tool against mock listings and a made-up template library.

Nothing is fetched, and Fusion's own libraries are hidden (HOME and APPDATA point at an
empty folder), so the results don't depend on the machine running the tests.
"""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT, run

SCRIPT = ROOT / "amazon-tool" / "amazon-tool.py"
BODY_LENGTH = "max(tool_shoulderLength, (tool_overallLength - tool_holderGaugeLength -12.5 ))"


def listing(title, bullets=(), rows=None):
    """A product page with the parts amazon-tool reads: title, feature bullets and a spec table."""
    lis = "".join(f"<li><span class='a-list-item'>{b}</span></li>" for b in bullets)
    trs = "".join(f"<tr><th>{k}</th><td>‎{v}</td></tr>" for k, v in (rows or {}).items())
    return (
        f'<span id="productTitle" class="a-size-large">{title}</span>'
        f'<div id="feature-bullets"><ul>{lis}</ul></div>'
        f'<table class="a-keyvalue prodDetTable">{trs}</table>'
    )


def template(fusion_type, description, dc, nof, **extra):
    """A library tool the way Fusion stores one, in a Carvera-style holder."""
    geometry = {
        "CSP": False, "HAND": True, "DC": dc, "SFDM": max(dc, 3.175), "NOF": nof, "LCF": 10, "OAL": 38,
        "LB": 19, "shoulder-length": 10, "shoulder-diameter": dc, "assemblyGaugeLength": 25.5, **extra,
    }
    return {
        "type": fusion_type, "unit": "millimeters", "description": description, "BMC": "carbide",
        "guid": "template-guid", "reference_guid": "template-reference",
        "geometry": geometry,
        "expressions": {"tool_bodyLength": BODY_LENGTH, "tool_diameter": f"{dc} mm"},
        "holder": {"description": "Test holder", "gaugeLength": 6.5, "segments": [{"height": 6.5, "lower-diameter": 7.5, "upper-diameter": 7.5}]},
        "shaft": {"type": "shaft", "segments": [{"height": 4, "lower-diameter": dc, "upper-diameter": 3.175}]},
        "post-process": {"number": 5, "diameter-offset": 5, "length-offset": 5, "comment": ""},
        "start-values": {"presets": [{
            "name": "Aluminum", "guid": "preset-guid", "n": 12000, "v_f": 600, "v_f_plunge": 200, "stepover": 1.0,
            "expressions": {"tool_stepover": "tool_diameter/7", "tool_spindleSpeed": "12000 rpm"},
        }]},
    }


LIBRARY = [
    template("flat end mill", "Template flat 1/8", 3.175, 1),
    template("flat end mill", "Template flat 6mm", 6, 2),
    template("ball end mill", "Template ball", 3.175, 2),
    template("chamfer mill", "Template chamfer 90", 3.175, 2, TA=45, **{"tip-diameter": 0.1}),
    template("chamfer mill", "Template engraving 30", 3.175, 1, TA=15, **{"tip-diameter": 0.2}),
    template("drill", "Template drill", 3, 2, SIG=118),
]

# Shaped like the WEXWE B0D4VP4R2C listing: sizes split between the title, size name and a rounded spec table
FLAT = listing(
    'Tools Carbide End Mill Square Milling Cutter for Cutting Alloy Steels,Cast Iron, Hardened Steel'
    ' - 4 Flute Cutting Tools 1/8 Shank (1/8-2" 4PCS)',
    ["Coating :All end mill set with MAH Coating,it is excellent resistance to high temperature"],
    {
        "Cutting Length": "0.5 inches", "Cutting Diameter": "0.13 inches", "Number of Flutes": "4",
        "Size Name": '1/8-2" 4PCS', "Brand Name": "WEXWE", "Part Number": '1/8-2" 4PCS', "ASIN": "B0D4VP4R2C",
        "Cut Type": "Non-Center Cutting",
    },
)


class AmazonTool(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        (self.tmp / "home").mkdir()
        self.library = self.tmp / "library.json"
        self.library.write_text(json.dumps({"data": LIBRARY, "version": 37}))

    def generate(self, page, *args, script=SCRIPT, out=True):
        html = self.tmp / "page.html"
        html.write_text(page, encoding="utf-8")
        home = str(self.tmp / "home")
        output = ["-o", self.tmp / "out.json"] if out else []
        r = run(
            script, "--html", html, "--library", self.library, *output, *args,
            env={"HOME": home, "USERPROFILE": home, "APPDATA": None},
        )
        tool = None
        if r.returncode == 0 and out:
            library = json.loads((self.tmp / "out.json").read_text())
            self.assertEqual(sorted(library), ["data", "version"])
            [tool] = library["data"]
        return r, tool

    def assertGeometry(self, tool, **expected):
        g = tool["geometry"]
        for key, value in expected.items():
            self.assertAlmostEqual(g[key.replace("_", "-")], value, places=3, msg=key)

    def test_flat_end_mill(self):
        r, tool = self.generate(FLAT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tool["type"], "flat end mill")
        self.assertEqual(tool["description"], 'WEXWE 1/8" 4 Flute End Mill (MAH Coated)')
        self.assertEqual(tool["product-id"], "B0D4VP4R2C")  # the part number is just the size name
        self.assertEqual(tool["product-link"], "https://www.amazon.com/dp/B0D4VP4R2C")
        # 0.13 inches is 1/8" rounded; LB is the template's formula: max(12.7, 50.8 - 6.5 - 12.5)
        self.assertGeometry(tool, DC=3.175, SFDM=3.175, LCF=12.7, OAL=50.8, NOF=4, LB=31.8, assemblyGaugeLength=38.3)
        self.assertIn("Template flat 1/8", r.stdout)
        self.assertIn("non-center cutting", r.stdout)

    def test_copies_the_template_for_the_new_tool(self):
        r, tool = self.generate(FLAT)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tool["holder"]["description"], "Test holder")
        self.assertNotIn("shaft", tool, "the template's neck doesn't fit another tool")
        self.assertNotIn("tool_diameter", tool["expressions"], "would override the new diameter in Fusion")
        self.assertEqual(tool["expressions"]["tool_bodyLength"], BODY_LENGTH)
        self.assertNotEqual(tool["guid"], "template-guid")
        self.assertNotEqual(tool["reference_guid"], "template-reference")
        self.assertEqual(
            {k: tool["post-process"][k] for k in ("number", "diameter-offset", "length-offset")},
            {"number": 1, "diameter-offset": 1, "length-offset": 1},
        )
        [preset] = tool["start-values"]["presets"]
        self.assertEqual((preset["n"], preset["v_f"]), (12000, 600), "rpm and mm/min feeds are kept")
        self.assertAlmostEqual(preset["f_z"], 600 / (12000 * 4))
        self.assertAlmostEqual(preset["stepover"], 3.175 / 7)
        self.assertNotEqual(preset["guid"], "preset-guid")

    def test_chamfer_bit_with_a_quarter_inch_shank(self):
        # Shaped like sisona B0CKBGYGJ4: the spec table's "Cutting Length" is really the overall length
        page = listing(
            'Carbide Chamfer End Mill 90 Degree 1/4" Shank Dia - Carving Bits, V Groove,Tisin Coated 3 Flute - for Alloy Steels',
            ['SPECIFICATION: chamfer End Mill With 90 Degree, 1/4" shank, 2" overall length'],
            {
                "Cutting Length": "2 inches", "Finish Type": "TiSiN", "Cutting Diameter": "0.25 inches",
                "Number of Flutes": "3", "Brand Name": "sisona", "Part Number": "3103206", "End Cut Type": "Chamfer",
                "ASIN": "B0CKBGYGJ4",
            },
        )
        r, tool = self.generate(page)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tool["type"], "chamfer mill")
        self.assertEqual(tool["description"], 'sisona 90° 1/4" Chamfer Mill (TiSiN Coated)')
        # TA is half the included angle; the flute length falls back to the cone, (6.35 / 2) / tan(45°)
        self.assertGeometry(tool, DC=6.35, SFDM=6.35, TA=45, tip_diameter=0, LCF=3.2, OAL=50.8, NOF=3, LB=31.8)
        self.assertIn("Template chamfer 90", r.stdout)
        self.assertIn('ignored spec "cutting length: 2 inches"', r.stdout)

    def test_engraving_bit_angle_and_tip_from_the_size(self):
        page = listing(
            "Single Flute Engraving Bit for Metal, 1/8 Shank",
            ["38mm total length"],
            {"Size Name": "30°*0.2mm", "Number of Flutes": "1", "Brand Name": "Engr", "ASIN": "B0TESTVBIT"},
        )
        r, tool = self.generate(page)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGeometry(tool, TA=15, tip_diameter=0.2, DC=3.175, SFDM=3.175, OAL=38)
        self.assertIn("Template engraving 30", r.stdout)

    def test_ball_end_mill(self):
        page = listing(
            "SpeTool 2 Flute Carbide Ball Nose End Mill 1/8 inch Cutting Dia, 1/8 Shank, 1/2 inch Cutting Length,"
            " 1-1/2 inch Overall Length",
            rows={"Brand Name": "SpeTool", "ASIN": "B0TESTBALL"},
        )
        r, tool = self.generate(page)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tool["type"], "ball end mill")
        self.assertGeometry(tool, DC=3.175, LCF=12.7, OAL=38.1, NOF=2)

    def test_drill_defaults(self):
        page = listing(
            "HSS Twist Drill Bit 3mm, 33mm Flute Length, 61mm Overall Length",
            rows={"Brand Name": "Drillco", "ASIN": "B0TESTDRIL"},
        )
        r, tool = self.generate(page)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(tool["type"], "drill")
        self.assertEqual(tool["BMC"], "hss")
        self.assertGeometry(tool, DC=3, SFDM=3, SIG=118, NOF=2, LCF=33, OAL=61)

    def test_flags_override_the_listing(self):
        r, tool = self.generate(FLAT, "--flute-length", 10, "--number", 3, "--description", "My mill")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertGeometry(tool, LCF=10)
        self.assertEqual(tool["description"], "My mill")
        self.assertEqual(tool["post-process"]["number"], 3)
        self.assertEqual(tool["post-process"]["length-offset"], 3)

    def test_refuses_types_it_cannot_model(self):
        r, _ = self.generate(listing("4 Flute Corner Radius End Mill 1/4 Shank", rows={"ASIN": "B0TESTRADI"}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("corner radius", r.stderr)

    def test_asks_for_what_the_listing_leaves_out(self):
        page = listing("2 Flute Carbide End Mill 1/8 Shank 1/8 Cutting Dia 8mm Cutting Length", rows={"ASIN": "B0TESTNOAL"})
        r, _ = self.generate(page)
        self.assertEqual(r.returncode, 1)
        self.assertIn("--overall-length", r.stderr)

    def test_needs_a_template_of_the_same_type(self):
        self.library.write_text(json.dumps({"data": LIBRARY[:2], "version": 37}))
        r, _ = self.generate(listing("HSS Twist Drill Bit 3mm, 33mm Flute Length, 61mm Overall Length", rows={"ASIN": "B0TESTDRIL"}))
        self.assertEqual(r.returncode, 1)
        self.assertIn("no metric drill", r.stderr)

    def test_default_output_is_named_after_the_tool(self):
        script = self.tmp / "amazon-tool.py"
        shutil.copy(SCRIPT, script)
        r, _ = self.generate(FLAT, script=script, out=False)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = self.tmp / "tools" / "WEXWE 1-8in 4 Flute End Mill (MAH Coated).json"
        self.assertTrue(out.exists(), list((self.tmp / "tools").iterdir()))


if __name__ == "__main__":
    unittest.main()
