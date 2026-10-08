#!/usr/bin/env python3
"""Tool definitions for the macros, read from Fusion tool libraries.

A macro run with --tool NAME takes the cutter's diameter and its feeds and speeds
for --material from here (see apply()). Tools come from, in this order:

1. custom/: our own tools, as Fusion tool library files (.json); amazon-tool writes here.
2. Makera's Fusion tool files, listed in makera.json and downloaded into cache/ the
   first time they're needed. Makera's repo has no license, so they aren't copied
   into this one.
3. Fusion's local tool libraries on this computer, if there are any.

When two have the same description, the first wins. From the command line:

    ./toollib.py list [TEXT]    tools whose description contains TEXT
    ./toollib.py show TEXT      one tool's sizes and presets
    ./toollib.py fetch          download Makera's tool files again
"""

import argparse
import io
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CUSTOM = HERE / "custom"
CACHE = HERE / "cache"
REFERENCES = HERE / "makera.json"
LOCAL = [
    Path.home() / "Library/Application Support/Autodesk/CAM360/libraries/Local",  # macOS
    *([Path(os.environ["APPDATA"]) / "Autodesk/CAM360/libraries/Local"] if "APPDATA" in os.environ else []),  # Windows
]
STEPOVER = 0.63  # facing stepover as a fraction of the diameter: 2.0 mm on a 3.175 mm cutter, Makera's 6061 value


@dataclass
class Preset:
    """A tool's feeds and speeds for one material, from its Fusion preset."""

    name: str
    rpm: float
    feed: float  # mm/min
    plunge_feed: float  # mm/min
    stepdown: float = None  # mm, None when the preset doesn't set one


@dataclass
class Tool:
    description: str
    type: str
    vendor: str
    product_id: str
    dia: float
    shank: float
    flutes: int
    flute_length: float
    overall_length: float
    source: str
    presets: dict = field(default_factory=dict)  # lower-case material -> Preset

    def preset(self, material):
        p = self.presets.get(material.lower())
        if p is None:
            have = ", ".join(p.name for p in self.presets.values()) or "none"
            raise SystemExit(f"toollib: {self.description} has no {material} preset (it has: {have}); pass --material")
        return p


def load_library(path):
    """A Fusion tool library: an exported .tools file (zipped JSON) or a local library's .json."""
    return load_library_bytes(Path(path).read_bytes())


def references():
    return json.loads(REFERENCES.read_text(encoding="utf-8"))


def fetch(force=False, quiet=False):
    """Download Makera's tool files listed in makera.json into cache/. Returns the files there.

    cache/COMMIT says which commit they came from; when makera.json moves to another, they're downloaded again.
    """
    ref = references()
    CACHE.mkdir(exist_ok=True)
    stamp = CACHE / "COMMIT"
    stale = force or not stamp.exists() or stamp.read_text(encoding="utf-8").strip() != ref["commit"]
    out, failed = [], False
    for name in ref["libraries"]:
        path = CACHE / name
        if stale or not path.exists():
            url = "https://raw.githubusercontent.com/{}/{}/{}".format(
                ref["repo"], ref["commit"], urllib.parse.quote(f"{ref['folder']}/{name}")
            )
            if not quiet:
                print(f"toollib: downloading {name}", file=sys.stderr)
            try:
                with urllib.request.urlopen(url, timeout=15) as response:
                    data = response.read()
                load_library_bytes(data)  # don't keep anything that isn't a tool library
                path.write_bytes(data)
            except (urllib.error.URLError, OSError, ValueError) as e:
                failed = True
                keep = "using the copy in cache/" if path.exists() else "continuing without it"
                print(f"toollib: couldn't download {name} ({e}); {keep}", file=sys.stderr)
                if not path.exists():
                    continue
        out.append(path)
    if stale and not failed:
        stamp.write_text(ref["commit"] + "\n", encoding="utf-8")
    return out


def load_library_bytes(data):
    if zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            data = z.read(next(n for n in z.namelist() if n.endswith(".json")))
    library = json.loads(data)  # UTF-8, with or without a BOM; anything else is a ValueError
    if not isinstance(library, dict) or not isinstance(library.get("data", []), list):
        raise ValueError("not a Fusion tool library")
    return library


def library_files():
    """Every tool library file to read, in order. Z1_TOOLS (paths separated by os.pathsep) replaces the default."""
    if os.environ.get("Z1_TOOLS"):
        roots = [Path(p) for p in os.environ["Z1_TOOLS"].split(os.pathsep) if p]
    else:
        roots = [CUSTOM, *fetch(quiet=True), *LOCAL]
    files = []
    for root in roots:
        if root.is_dir():
            files += sorted(f for f in root.rglob("*") if f.suffix in (".json", ".tools"))
        elif root.is_file():
            files.append(root)
    return files


def to_tool(entry, source):
    g = entry.get("geometry", {})
    presets = {}
    for p in (entry.get("start-values") or {}).get("presets", []):
        if p.get("n") and p.get("v_f"):
            stepdown = p.get("stepdown") if p.get("use-stepdown", True) else None
            presets[p["name"].lower()] = Preset(p["name"], p["n"], p["v_f"], p.get("v_f_plunge", p["v_f"]), stepdown)
    return Tool(
        description=entry.get("description", ""), type=entry.get("type", ""), vendor=entry.get("vendor", ""),
        product_id=entry.get("product-id", ""), dia=g["DC"], shank=g.get("SFDM", g["DC"]), flutes=g.get("NOF", 0),
        flute_length=g.get("LCF", 0), overall_length=g.get("OAL", 0), source=source, presets=presets,
    )


def tools():
    """Every metric cutting tool, first library first; later ones with the same description are skipped."""
    seen, out = set(), []
    for path in library_files():
        try:
            library = load_library(path)
        except (OSError, ValueError, StopIteration, zipfile.BadZipFile):
            continue
        for entry in library.get("data", []):
            if not isinstance(entry, dict) or entry.get("unit") != "millimeters" or "DC" not in entry.get("geometry", {}):
                continue
            description = entry.get("description", "")
            if description.lower() in seen:
                continue
            seen.add(description.lower())
            out.append(to_tool(entry, path.stem))
    return out


def find(text):
    """The one tool whose description, product id or ASIN is `text`, or whose description contains it."""
    every = tools()
    t = text.lower()
    exact = [x for x in every if t in (x.description.lower(), x.product_id.lower())]
    matches = exact or [x for x in every if t in x.description.lower()]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"toollib: no tool matches {text!r}; see tool-library/toollib.py list")
    names = "; ".join(f"{m.description} ({m.source})" for m in matches[:8])
    raise SystemExit(f"toollib: {len(matches)} tools match {text!r}: {names}. Be more specific")


def apply(v, tool, material, given=frozenset()):
    """Set a macro's cutter settings in v from `tool` (a Tool, or a name for find()) and its `material` preset.

    tool_dia, rpm, feed, plunge_feed, pass_depth, flute_length and stepover (STEPOVER
    of tool_dia, the user's if they gave one) are set, only the ones v has, unless
    they're in `given`, the settings the user gave (args.given from cli.add_variables()).
    Returns (tool, preset).
    """
    tool = find(tool) if isinstance(tool, str) else tool
    preset = tool.preset(material)
    values = {
        "tool_dia": tool.dia, "rpm": round(preset.rpm), "feed": round(preset.feed), "plunge_feed": round(preset.plunge_feed),
        "pass_depth": preset.stepdown, "flute_length": tool.flute_length or None,  # 0 when the library has no LCF
    }
    for key, value in values.items():
        if key in v and key not in given and value is not None:
            v[key] = type(v[key])(value)
    if "stepover" in v and "stepover" not in given:
        v["stepover"] = type(v["stepover"])(round(STEPOVER * v.get("tool_dia", tool.dia), 3))
    return tool, preset


def describe(tool):
    lines = [
        f"{tool.description}  ({tool.type}, from {tool.source})",
        f"  diameter {tool.dia:g} mm, shank {tool.shank:g} mm, {tool.flutes} flutes,"
        f" flute length {tool.flute_length:g} mm, overall {tool.overall_length:g} mm",
    ]
    if tool.vendor or tool.product_id:
        lines.append(f"  {tool.vendor} {tool.product_id}".rstrip())
    for p in tool.presets.values():
        depth = f", {p.stepdown:g} mm deep" if p.stepdown else ""
        lines.append(f"  {p.name:<13} {p.rpm:7.0f} rpm, {p.feed:5.0f} mm/min, plunge {p.plunge_feed:4.0f}{depth}")
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("list").add_argument("text", nargs="?", default="")
    sub.add_parser("show").add_argument("text")
    sub.add_parser("fetch")
    args = p.parse_args()
    if args.command == "fetch":
        for path in fetch(force=True):
            print(f"{path}: {len(load_library(path).get('data', []))} tools")
    elif args.command == "show":
        print(describe(find(args.text)))
    else:
        for t in tools():
            if args.text.lower() in t.description.lower():
                materials = ", ".join(p.name for p in t.presets.values())
                print(f"{t.description:<52} {t.type:<14} {t.dia:6g} mm {t.flutes} fl  [{t.source}]  {materials}")


if __name__ == "__main__":
    main()
