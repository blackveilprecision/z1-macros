#!/usr/bin/env python3
"""Turn an Amazon cutter listing into a Fusion tool library file.

Reads the tool's type and sizes from the listing's title, bullets and spec
tables, copies the holder, presets and post settings from the closest tool of
the same type in your Fusion libraries, and writes a one-tool .json library
to tools/, named after the tool. Import that in Fusion's Tool Library, then
drag the tool into your own library.

Flat end mills, ball end mills, chamfer mills (chamfer and V-bits) and drills
are supported. Sellers' listings are often incomplete or wrong: the script
prints each value and where it read it. Check them against the tool, and
override any of them with flags (see --help).
"""

import argparse
import ast
import copy
import gzip
import html
import json
import math
import operator
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from fractions import Fraction
from pathlib import Path

# Fusion's local tool libraries, searched for a template of the same type (add others with --library)
LOCAL_LIBRARIES = [
    Path.home() / "Library/Application Support/Autodesk/CAM360/libraries/Local",  # macOS
    *([Path(os.environ["APPDATA"]) / "Autodesk/CAM360/libraries/Local"] if "APPDATA" in os.environ else []),  # Windows
]

INCH = 25.4
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko)"
    " Chrome/129.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip",
}
HERE = Path(__file__).resolve().parent

# --type -> Fusion's tool type, and the noun used in the description
TYPES = {"flat": "flat end mill", "ball": "ball end mill", "chamfer": "chamfer mill", "drill": "drill"}
NOUNS = {"flat": "End Mill", "ball": "Ball End Mill", "chamfer": "Chamfer Mill", "drill": "Drill"}
UNSUPPORTED = r"corner radius|radius end|bull ?nose|thread(?:ing)? mill|dovetail|t[- ]slot|lollipop|counter ?sink|tapered (?:ball|end)"
KINDS = [  # (type, words, unless these words): first match in the title or tip type wins, else a flat end mill
    ("chamfer", r"chamfer|v[- ]?(?:bit|groove|shape|cutter)|engrav", None),
    ("ball", r"ball[- ]?(?:nose|end)|ballnose", None),
    ("drill", r"\bdrill(?:s|ing)?\b", r"end ?mill|router bit"),
]
NEEDS = {  # what each type must have, from the listing, a flag or a default
    "flat": ["dia", "shank", "flute_length", "overall_length", "flutes"],
    "ball": ["dia", "shank", "flute_length", "overall_length", "flutes"],
    "chamfer": ["angle", "shank", "dia", "tip", "flute_length", "overall_length", "flutes"],
    "drill": ["dia", "shank", "flute_length", "overall_length", "flutes", "angle"],
}
NAMES = {
    "dia": ("diameter", "--dia"),
    "shank": ("shank", "--shank-dia"),
    "flute_length": ("flute length", "--flute-length"),
    "overall_length": ("overall length", "--overall-length"),
    "flutes": ("flutes", "--flutes"),
    "angle": ("angle", "--angle"),
    "tip": ("tip diameter", "--tip-dia"),
}

GRIP = 8.0  # a flute length closer than this to the overall length leaves no shank to hold: ignore it

# Where a value was read, best first. Spec tables come last: Amazon rounds them (1/8" shows as 0.13 inches).
TEXT, SIZE, SPEC = 1, 2, 3

NUM = r"\d+-\d+/\d+|\d+/\d+|\d*\.\d+|\d+"
UNIT = r'mm|millimet(?:er|re)s?|inch(?:es)?|in\b|"'
VALUE = rf"(?P<num>{NUM})\s*(?P<unit>{UNIT})?"
LABELS = {
    "dia": r"(?<!shank )(?<!shaft )(?<!tip )(?:(?:cutting|cutter|cut|blade|edge|mill|head)\s*)?dia(?:meter)?\b\.?"
    r"|(?:(?:solid\s*)?(?:carbide|hss|cobalt)\s*)?(?:twist\s*)?drill(?:\s*bits?)?"  # "3mm Drill Bit"
    r"|(?:(?:solid\s*)?(?:carbide|hss|cobalt)\s*)?(?:ball\s*(?:nose|end)\s*)?end\s*mills?",  # "1/8 Carbide End Mill"
    "shank": r"shank(?:\s*(?:dia(?:meter)?|size))?|shaft(?:\s*dia(?:meter)?)?",
    "flute_length": r"(?:cutting|cut|flute|blade|edge|effective)\s*(?:edge\s*)?length|length\s*of\s*cut|\bLOC\b",
    "overall_length": r"(?:overall|total|full|whole)\s*length|length\s*overall|\bOAL\b",
    "tip": r"tip(?:\s*(?:dia(?:meter)?|width|size))?|point\s*(?:dia(?:meter)?|width)|bottom\s*width",
}
SPEC_ROWS = {
    "dia": ("cutting diameter", "cutter diameter", "diameter"),
    "shank": ("shank diameter", "shank size", "shank"),
    "flute_length": ("cutting length", "flute length", "length of cut"),
    "overall_length": ("overall length", "total length"),
    "tip": ("tip diameter", "tip size"),
}
ANGLE = r"(?<![\w.])(?P<deg>\d{1,3}(?:\.\d+)?)\s*(?:°|deg(?:ree)?s?\b)"
ANGLE_ROWS = ("included angle", "point angle", "tip angle", "cutting angle", "angle")
NOT_TIP_ANGLE = r"helix|spiral|rake|relief|clearance"
FLUTE_WORDS = {"single": 1, "one": 1, "two": 2, "double": 2, "three": 3, "triple": 3, "four": 4, "five": 5, "six": 6}
FLUTES = rf"(?<![\w/.\-])(?P<n>\d+|{'|'.join(FLUTE_WORDS)})\s*[- ]?\s*flutes?\b|flutes?\s*[:=]\s*(?P<m>\d+)"
CLEAN = str.maketrans({
    "（": "(", "）": ")", "“": '"', "”": '"', "″": '"', "：": ":", "，": ",", "º": "°", "˚": "°",
    "‎": "", "‏": "",
})
OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv}


def fmt(value):
    s = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def clean(fragment):
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", fragment, flags=re.S | re.I)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text)).translate(CLEAN)
    return re.sub(r"\s+", " ", text).strip()


def to_mm(num, unit, inch=False):
    """'1/8', '1-1/2' or '15' with its unit -> mm. A decimal with no unit could be either: None."""
    unit = (unit or "").lower()
    if "/" in num:
        whole, _, frac = num.rpartition("-")
        value = float(Fraction(frac)) + (int(whole) if whole else 0)
        return value if unit.startswith("m") else value * INCH
    if unit.startswith("m"):
        return float(num)
    return float(num) * INCH if unit or inch else None


def snap(mm, inch):
    """Spec tables round to 2 places (1/8" is '0.13 inches' or '3.18 millimeters'): undo that for 1/64" sizes."""
    n = round(mm / INCH * 64)
    exact = n * INCH / 64
    return exact if n and abs(exact - mm) <= 0.0051 * (INCH if inch else 1) else mm


def size_label(mm):
    n = round(mm / INCH * 64)
    if n and abs(n * INCH / 64 - mm) < 0.005:
        f = Fraction(n, 64)
        return f'{f.numerator}/{f.denominator}"' if f.denominator > 1 else f'{f.numerator}"'
    return f"{fmt(mm)}mm"


def fetch(url):
    request = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body, final = response.read(), response.geturl()
            if response.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
    except urllib.error.HTTPError as e:
        raise SystemExit(f"amazon-tool: Amazon answered {e.code}. Save the page from your browser and pass --html PAGE")
    except urllib.error.URLError as e:
        raise SystemExit(f"amazon-tool: couldn't reach Amazon ({e.reason})")
    page = body.decode("utf-8", "replace")
    if 'id="productTitle"' not in page:
        raise SystemExit(
            "amazon-tool: Amazon sent a robot check instead of the listing. Save the page from your browser"
            " (File > Save Page As) and run again with --html PAGE"
        )
    return page, final


def asin_in(url):
    m = re.search(r"(?:/dp/|/gp/product/|/gp/aw/d/|/product/)([A-Z0-9]{10})(?=[/?#]|$)", url)
    m = m or re.fullmatch(r"\s*([A-Z0-9]{10})\s*", url)
    return m.group(1) if m else None


def read_listing(page):
    def section(element_id, end):
        m = re.search(rf'id="{element_id}"[^>]*>(.*?){end}', page, re.S)
        return m.group(1) if m else ""

    rows = {}
    tables = [section("productOverview_feature_div", "</table>")]
    tables += [t for a, t in re.findall(r"<table\b([^>]*)>(.*?)</table>", page, re.S) if "prodDetTable" in a]
    for table in tables:
        for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", table, re.S):
            cells = [clean(c) for c in re.findall(r"<t[hd]\b[^>]*>(.*?)</t[hd]>", row, re.S)]
            if len(cells) == 2 and cells[0]:
                rows.setdefault(cells[0].lower(), cells[1])
    for item in re.findall(r"<li\b[^>]*>(.*?)</li>", section("detailBullets_feature_div", "</ul>"), re.S):
        key, sep, value = clean(item).partition(":")
        if sep:
            rows.setdefault(key.strip().lower(), value.strip())

    byline = clean(section("bylineInfo", "</a>"))
    byline = re.sub(r"^(?:Visit the|Brand:)\s*|\s*Store$", "", byline)
    return {
        "title": clean(section("productTitle", "</span>")),
        "bullets": [clean(li) for li in re.findall(r"<li\b[^>]*>(.*?)</li>", section("feature-bullets", "</ul>"), re.S)],
        "description": clean(section("productDescription", "</div>")),
        "rows": rows,
        "byline": byline,
    }


def detect_type(listing):
    rows = listing["rows"]
    text = " ".join([listing["title"], rows.get("end cut type", ""), rows.get("tip type", "")])
    m = re.search(UNSUPPORTED, text, re.I)
    if m:
        raise SystemExit(
            f"amazon-tool: this looks like a {m.group(0).lower()} tool, which isn't supported yet;"
            f" pass --type {'|'.join(TYPES)} if it is one of those"
        )
    for kind, pattern, unless in KINDS:
        m = re.search(pattern, text, re.I)
        if m and not (unless and re.search(unless, text, re.I)):
            return kind, f'the title says "{m.group(0)}"'
    return "flat", "no ball, chamfer or drill words in the title"


def clauses(text):
    text = re.sub(r"\([^()]*\)", " ", text)  # parentheses repeat a size in the other unit
    return [re.sub(r"\s+", " ", c).strip() for c in re.split(r"[,;|]|\s-\s|\.\s", text) if c.strip()]


def labeled(clause, label):
    """'1/8 Inch Shank', 'Shank: 1/8"', or 'Cutting Length 15mm' ending the clause."""
    hits = list(re.finditer(rf"(?<![\w/.\-]){VALUE}\s*(?:{label})", clause, re.I))
    hits += re.finditer(rf"(?:{label})\s*(?:[:=]|\bis\b)\s*{VALUE}", clause, re.I)
    return hits or list(re.finditer(rf"(?:{label})\s+{VALUE}\s*$", clause, re.I))


def candidates(listing):
    """Every size the listing states, as field -> [(value, rank, where)]."""
    found = {name: [] for name in NAMES}

    def add(field, value, rank, where):
        if value is not None and (value > 0 or field == "tip" and value == 0):
            found[field].append((round(value, 4), rank, where))

    texts = [("title", listing["title"]), *(("bullet", b) for b in listing["bullets"]), ("description", listing["description"])]
    for where, text in texts:
        for clause in clauses(text):
            for field, label in LABELS.items():
                for m in labeled(clause, label):
                    add(field, to_mm(m["num"], m["unit"]), TEXT, f'{where} "{m.group(0).strip()}"')
            for m in re.finditer(FLUTES, clause, re.I):
                n = m["m"] or m["n"]
                add("flutes", FLUTE_WORDS.get(n.lower()) or int(n), TEXT, f'{where} "{m.group(0).strip()}"')
            if not re.search(NOT_TIP_ANGLE, clause, re.I):
                for m in re.finditer(ANGLE, clause, re.I):
                    add("angle", float(m["deg"]), TEXT, f'{where} "{m.group(0).strip()}"')

    rows = listing["rows"]
    size = rows.get("size name") or rows.get("size") or ""
    m = re.match(r"\s*(\d*\.?\d+)\s*[*x×]\s*(\d*\.?\d+)\s*[*x×]\s*(\d*\.?\d+)\s*mm\b", size, re.I)
    if m:
        a, b, c = map(float, m.groups())
        where = f'size "{size}" read as shank*diameter*flute length'
        add("shank", max(a, b), SIZE, where)
        add("dia", min(a, b), SIZE, where)
        add("flute_length", c, SIZE, where)
    m = re.match(r'\s*(\d+/\d+|\d*\.?\d+)\s*-\s*(\d*\.?\d+)\s*"', size)
    if m and float(m[2]) * INCH > 4 * to_mm(m[1], '"'):
        where = f'size "{size}" read as diameter-overall length'
        add("dia", to_mm(m[1], '"'), SIZE, where)
        add("overall_length", float(m[2]) * INCH, SIZE, where)
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:°|deg)\s*[*x×]\s*(\d*\.?\d+)\s*mm", size, re.I)
    if m:
        where = f'size "{size}" read as angle*tip diameter'
        add("angle", float(m[1]), SIZE, where)
        add("tip", float(m[2]), SIZE, where)
    else:
        for m in re.finditer(ANGLE, size, re.I):
            add("angle", float(m["deg"]), SIZE, f'size "{size}"')

    for field, keys in SPEC_ROWS.items():
        key = next((k for k in keys if k in rows), None)
        m = key and re.search(VALUE, rows[key], re.I)
        if m and (mm := to_mm(m["num"], m["unit"])):
            inch = not (m["unit"] or "").lower().startswith("m")
            add(field, snap(mm, inch), SPEC, f'spec "{key}: {rows[key]}"')
    key = next((k for k in ANGLE_ROWS if k in rows), None)
    m = key and re.search(r"\d+(?:\.\d+)?", rows[key])
    if m:
        add("angle", float(m.group(0)), SPEC, f'spec "{key}: {rows[key]}"')
    m = re.search(r"\d+", rows.get("number of flutes", ""))
    if m:
        add("flutes", int(m.group(0)), SPEC, f'spec "number of flutes: {rows["number of flutes"]}"')
    return found, size


def choose(found):
    """Best-ranked value per field, and per field the values that disagree with it."""
    picked, conflicts = {}, {}
    for field, cands in found.items():
        if not cands:
            continue
        value, _, where = min(cands, key=lambda c: c[1])
        picked[field] = (value, where)
        tol = {"flutes": 0, "angle": 0.5}.get(field, max(0.15, 0.02 * value))
        unit = {"flutes": "", "angle": "°"}.get(field, " mm")
        for other, _, other_where in cands:
            if abs(other - value) > tol:
                conflicts.setdefault(field, []).append(f"{NAMES[field][0]}: using {fmt(value)}{unit}, but {other_where} says {fmt(other)}{unit}")
    return picked, conflicts


def fill_defaults(kind, t, sources):
    """Values a listing often leaves out but that have a usual answer for the type."""

    def default(field, value, why):
        if field not in t and value is not None:
            t[field], sources[field] = value, f"not in the listing: {why} ({NAMES[field][1]} to change)"

    if kind == "chamfer":
        default("dia", t.get("shank"), "same as the shank")
        default("tip", 0.0, "pointed")
        if all(k in t for k in ("dia", "tip", "angle")) and 0 < t["angle"] < 180:
            cone = (t["dia"] - t["tip"]) / 2 / math.tan(math.radians(t["angle"] / 2))
            default("flute_length", math.ceil(cone * 10) / 10, "the length of the cone")
    if kind == "drill":
        default("shank", t.get("dia"), "same as the diameter")
        default("flutes", 2, "a twist drill's")
        default("angle", 118.0, "the usual 118°")


def coating(listing):
    def acronym(word):
        return sum(ch.isupper() for ch in word) >= 2

    for text in [listing["title"], *listing["bullets"]]:
        for m in re.finditer(r"\b([A-Za-z]{2,8})[- ][Cc]oat(?:ed|ing)s?\b", text):
            if acronym(m[1]):
                return m[1]
    rows = listing["rows"]
    finish = rows.get("coating") or rows.get("finish types") or rows.get("finish type") or ""
    return finish if re.fullmatch(r"[A-Za-z]{2,8}", finish) and acronym(finish) else ""


def material(listing):
    rows = listing["rows"]
    text = " ".join([listing["title"], *listing["bullets"], rows.get("material type", ""), rows.get("material", "")])
    if re.search(r"carbide|tungsten", text, re.I):
        return "carbide"
    if re.search(r"\bHSS\b|high[- ]speed steel", text, re.I):
        return "hss"
    return None


def evaluate(expr, names):
    """A Fusion expression of numbers, tool_* names, + - * / and max/min; None if it has anything else."""

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return node.value
        if isinstance(node, ast.Name) and node.id in names:
            return names[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return -ev(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in OPS:
            return OPS[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("max", "min") and node.args:
            return (max if node.func.id == "max" else min)(ev(a) for a in node.args)
        raise ValueError(expr)

    try:
        return ev(ast.parse(expr, mode="eval"))
    except (SyntaxError, ValueError, ZeroDivisionError):
        return None


def load_library(path):
    """A Fusion library: an exported .tools file (zipped JSON) or a local library's .json."""
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as z:
            name = next(n for n in z.namelist() if n.endswith(".json"))
            return json.loads(z.read(name))
    return json.loads(Path(path).read_text(encoding="utf-8"))


def library_tools(extra):
    """(library path, library, tool) for every tool in --library files, then in Fusion's local libraries."""
    pool = []
    for path in extra:
        try:
            library = load_library(path)
        except (OSError, StopIteration, zipfile.BadZipFile, json.JSONDecodeError) as e:
            raise SystemExit(f"amazon-tool: can't read the Fusion library {path} ({e})")
        pool += [(path, library, tool) for tool in library.get("data", [])]
    for root in LOCAL_LIBRARIES:
        for path in sorted(root.rglob("*.json")) if root.is_dir() else []:
            try:
                library = load_library(path)
            except (OSError, StopIteration, zipfile.BadZipFile, json.JSONDecodeError):
                continue
            pool += [(path, library, tool) for tool in library.get("data", []) if isinstance(tool, dict)]
    return pool


def pick_template(pool, kind, t, wanted):
    """Same type, then the closest angle (chamfers), diameter and shank, then the most presets; earlier libraries win ties."""
    cands = [
        c for c in pool
        if c[2].get("type") == TYPES[kind] and c[2].get("unit") == "millimeters" and "DC" in c[2].get("geometry", {})
    ]
    if wanted:
        cands = [c for c in cands if wanted.lower() in c[2].get("description", "").lower()]
    if not cands:
        where = f' matching "{wanted}"' if wanted else ""
        raise SystemExit(
            f"amazon-tool: no metric {TYPES[kind]}{where} in your Fusion libraries to copy the holder and presets from."
            " Add one in Fusion (or pass --library FILE)"
        )

    def score(c):
        g, presets = c[2]["geometry"], (c[2].get("start-values") or {}).get("presets", [])
        angle = abs(g.get("TA", 0) * 2 - t["angle"]) if kind == "chamfer" else 0
        return angle, round(abs(g["DC"] - t["dia"]), 3), round(abs(g.get("SFDM", g["DC"]) - t["shank"]), 3), -len(presets)

    return min(cands, key=score)


def quoted(text):
    return "'" + text.replace("'", "’") + "'"


def build_tool(template, kind, t, number):
    """Copy the template tool (holder, presets, post settings) and give it this cutter's sizes."""
    tool = copy.deepcopy(template)
    g, tg = tool["geometry"], template["geometry"]
    dc, nof = t["dia"], t["flutes"]
    gauge = (tool.get("holder") or {}).get("gaugeLength", 0)
    names = {
        "tool_shoulderLength": t["shoulder_length"], "tool_overallLength": t["overall_length"],
        "tool_holderGaugeLength": gauge, "tool_fluteLength": t["flute_length"], "tool_diameter": dc,
        "tool_shaftDiameter": t["shank"], "tool_numberOfFlutes": nof,
    }

    # Body length (stick-out below the holder): the template's own formula if it has one,
    # otherwise the same depth in the holder as the template tool.
    expr = template.get("expressions", {}).get("tool_bodyLength")
    body = evaluate(expr, names) if expr else None
    if body is None:
        expr = None
        body = max(t["shoulder_length"], t["overall_length"] - (tg["OAL"] - tg["LB"]))
    if not t["flute_length"] <= t["shoulder_length"] <= body <= t["overall_length"]:
        raise SystemExit(
            f"amazon-tool: flute length {fmt(t['flute_length'])} <= shoulder {fmt(t['shoulder_length'])}"
            f" <= body {fmt(body)} <= overall length {fmt(t['overall_length'])} doesn't hold; check the sizes"
        )

    g.update({
        "DC": dc, "SFDM": t["shank"], "NOF": nof, "LCF": t["flute_length"], "OAL": t["overall_length"],
        "LB": round(body, 4), "shoulder-length": t["shoulder_length"],
    })
    if "shoulder-diameter" in g:
        g["shoulder-diameter"] = dc
    if "assemblyGaugeLength" in g:
        g["assemblyGaugeLength"] = round(body + gauge, 4)
    if kind == "chamfer":
        g.update({"TA": t["angle"] / 2, "tip-diameter": t["tip"]})
    if kind == "drill":
        g["SIG"] = t["angle"]
    tool.pop("shaft", None)  # the template's neck profile; without one Fusion runs the shank down to the shoulder

    tool["expressions"] = {
        **({"tool_bodyLength": expr} if expr else {}),
        "tool_description": quoted(t["description"]),
        "tool_fluteLength": f"{fmt(t['flute_length'])} mm",
        "tool_overallLength": f"{fmt(t['overall_length'])} mm",
        "tool_productId": quoted(t["product_id"]),
        "tool_productLink": quoted(t["link"]),
        "tool_shoulderLength": f"{fmt(t['shoulder_length'])} mm",
        "tool_vendor": quoted(t["vendor"]),
    }
    tool.update({
        "description": t["description"], "vendor": t["vendor"], "product-id": t["product_id"],
        "product-link": t["link"], "guid": str(uuid.uuid4()), "last_modified": int(time.time() * 1000),
    })
    if "reference_guid" in tool:
        tool["reference_guid"] = str(uuid.uuid4())
    if t["material"]:
        tool["BMC"] = t["material"]
    post = tool.setdefault("post-process", {})
    for key in ("diameter-offset", "length-offset"):  # these follow the tool number in Fusion's libraries
        if key in post and post[key] == post.get("number"):
            post[key] = number
    post["number"] = number

    # Presets keep their rpm and mm/min feeds. Stepover/stepdown written against the diameter are
    # worked out again, as are per-tooth, per-rev and surface speed.
    for p in (tool.get("start-values") or {}).get("presets", []):
        p["guid"] = str(uuid.uuid4())
        for key, field in (("tool_stepover", "stepover"), ("tool_stepdown", "stepdown")):
            value = evaluate(p.get("expressions", {}).get(key, ""), names)
            if value is not None and field in p:
                p[field] = value
        n = p.get("n")
        if n:
            p["v_c"] = math.pi * dc * n / 1000
            if "v_f" in p:
                p["f_z"] = p["v_f"] / (n * nof)
            if "v_f_plunge" in p:
                p["f_n"] = p["v_f_plunge"] / n
    return tool, body, expr


def parse_args():
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("url", nargs="?", help="Amazon link or ASIN (optional with --html)")
    p.add_argument("--html", type=Path, help="a listing page saved from your browser, if Amazon blocks the script")
    p.add_argument("--type", choices=TYPES, help="tool type, if the listing's words don't say it")
    p.add_argument("--template", help="copy the holder and presets from the tool whose description contains this")
    p.add_argument("--library", type=Path, action="append", default=[], help="extra Fusion library (.json or exported .tools) to search for a template")
    p.add_argument("-o", "--out", type=Path, help="output file (default: tools/<tool description>.json)")
    p.add_argument("--number", type=int, default=1, help="tool number (default: 1)")
    sizes = p.add_argument_group("override what the listing says (mm and degrees)")
    sizes.add_argument("--dia", type=float, help="cutting diameter; for a chamfer mill, the widest part of the cone")
    sizes.add_argument("--shank-dia", dest="shank", type=float)
    sizes.add_argument("--flute-length", type=float)
    sizes.add_argument("--overall-length", type=float)
    sizes.add_argument("--shoulder-length", type=float, help="default: the flute length")
    sizes.add_argument("--flutes", type=int)
    sizes.add_argument("--angle", type=float, help="chamfer mill: included angle (90 for a 45° chamfer); drill: point angle")
    sizes.add_argument("--tip-dia", dest="tip", type=float, help="chamfer mill: flat at the tip (0 = pointed)")
    sizes.add_argument("--vendor")
    sizes.add_argument("--product-id")
    sizes.add_argument("--description")
    args = p.parse_args()
    if not args.url and not args.html:
        p.error("give an Amazon link or --html PAGE")
    return args


def main():
    args = parse_args()
    asin = asin_in(args.url or "")
    host = urllib.parse.urlparse(args.url or "").netloc
    host = host if "amazon." in host else "www.amazon.com"
    if args.html:
        page = args.html.read_text(encoding="utf-8", errors="replace")
    elif asin:
        page, _ = fetch(f"https://{host}/dp/{asin}")
    else:  # short link (a.co, amzn.to): follow it
        page, final = fetch(args.url)
        asin, host = asin_in(final), urllib.parse.urlparse(final).netloc

    listing = read_listing(page)
    rows = listing["rows"]
    asin = asin or rows.get("asin")
    if not listing["title"] or not asin:
        raise SystemExit("amazon-tool: that doesn't look like an Amazon product page")
    kind, kind_source = (args.type, "--type") if args.type else detect_type(listing)

    found, size = candidates(listing)
    ignored = []
    oal = args.overall_length or min(found["overall_length"], key=lambda c: c[1], default=(None,))[0]
    if oal:  # sellers often fill in "Cutting Length" with the overall length
        ignored = [c for c in found["flute_length"] if c[0] > oal - GRIP]
        found["flute_length"] = [c for c in found["flute_length"] if c not in ignored]
    picked, conflicts = choose(found)
    t, sources = {}, {}
    for field in NEEDS[kind]:
        if getattr(args, field) is not None:
            t[field], sources[field] = getattr(args, field), NAMES[field][1]
        elif field in picked:
            t[field], sources[field] = picked[field]
    fill_defaults(kind, t, sources)
    missing = [f for f in NEEDS[kind] if f not in t]
    if missing:
        raise SystemExit(
            f"amazon-tool: the listing doesn't give the {', '.join(NAMES[f][0] for f in missing)};"
            f" pass {', '.join(NAMES[f][1] for f in missing)}"
        )
    if args.shoulder_length is not None:
        t["shoulder_length"], sources["shoulder_length"] = args.shoulder_length, "--shoulder-length"
    else:
        t["shoulder_length"] = t["flute_length"]
        sources["shoulder_length"] = "not in the listing: same as the flute length (--shoulder-length to change)"
    if kind in ("chamfer", "drill") and not 0 < t["angle"] < 180:
        raise SystemExit(f"amazon-tool: an angle of {fmt(t['angle'])}° can't be right; pass --angle")
    if kind == "chamfer" and t["tip"] >= t["dia"]:
        raise SystemExit("amazon-tool: the tip diameter must be smaller than the diameter; pass --tip-dia or --dia")

    t["vendor"] = args.vendor or rows.get("brand name") or rows.get("brand") or rows.get("manufacturer") or listing["byline"] or "Unknown"
    part = next((rows[k] for k in ("part number", "model", "item model number", "manufacturer part number") if k in rows), "")
    same_as_size = re.sub(r"\W", "", part.lower()) == re.sub(r"\W", "", size.lower())
    t["product_id"] = args.product_id or (part if part and not same_as_size else asin)
    t["link"] = f"https://{host}/dp/{asin}"
    t["material"] = material(listing)
    coat = coating(listing)
    if args.description:
        t["description"] = args.description
    else:
        what = size_label(t["dia"])
        if kind in ("flat", "ball"):
            what += f" {'Single' if t['flutes'] == 1 else t['flutes']} Flute"
        if kind == "chamfer":
            what = f"{fmt(t['angle'])}°" + (f" x {fmt(t['tip'])}mm" if t["tip"] else "") + f" {what}"
        t["description"] = f"{t['vendor']} {what} {NOUNS[kind]}" + (f" ({coat} Coated)" if coat else "")

    path, library, template = pick_template(library_tools(args.library), kind, t, args.template)
    tool, body, expr = build_tool(template, kind, t, args.number)

    # Fusion names an imported library after its file: the description, with 1/8" written as 1-8in
    name = re.sub(r'[<>:\\|?*\x00-\x1f]+', "", t["description"].replace('"', "in").replace("/", "-")).strip(" .")
    out = args.out or HERE / "tools" / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"data": [tool], "version": library.get("version", 37)}, indent=4, sort_keys=True) + "\n")

    print(t["description"])
    print(f"{TYPES[kind]} ({kind_source}), {t['link']}")
    print()
    units = {"flutes": "", "angle": "°"}
    for field in [*NEEDS[kind], "shoulder_length"]:
        name = NAMES[field][0] if field in NAMES else "shoulder length"
        value = f"{fmt(t[field])}{units.get(field, ' mm')}"
        print(f"  {name:<16} {value:>10}  {sources[field]}")
    print(f"  {'body length':<16} {fmt(body) + ' mm':>10}  " + (f"template formula {expr}" if expr else "same depth in the holder as the template"))
    print(f"  {'vendor / part':<16} {t['vendor']} / {t['product_id']}, {t['material'] or tool.get('BMC')}")
    print()
    presets = [p.get("name", "?") for p in (tool.get("start-values") or {}).get("presets", [])]
    tg = template["geometry"]
    print(
        f"Holder ({(tool.get('holder') or {}).get('description', 'none')}) and presets ({', '.join(presets) or 'none'})"
        f" copied from {template.get('description')!r} in {Path(path).stem}. Tool number {args.number}."
    )

    notes = [c for f in NEEDS[kind] if f in picked and sources[f] == picked[f][1] for c in conflicts.get(f, [])]
    notes += [f"ignored {where}: {fmt(v)} mm leaves no shank to hold" for v, _, where in ignored]
    if abs(tg["DC"] - t["dia"]) > 0.05:
        notes.append(f"the presets are for a {fmt(tg['DC'])} mm tool: set feeds and speeds for {fmt(t['dia'])} mm")
    if tg.get("NOF") and tg["NOF"] != t["flutes"] and kind != "drill":
        ratio = Fraction(tg["NOF"], t["flutes"])
        notes.append(
            f"feed rates (mm/min) are copied from a {tg['NOF']}-flute tool, so with {t['flutes']} flutes each tooth"
            f" takes {f'{ratio} of' if ratio < 1 else f'{ratio}x'} the chip: check them for this tool"
        )
    if kind == "chamfer":
        if t["angle"] == 45:
            notes.append("45° may be the chamfer it cuts, not the included angle: pass --angle 90 if so")
        cone = (t["dia"] - t["tip"]) / 2 / math.tan(math.radians(t["angle"] / 2))
        if t["flute_length"] < cone - 0.05:
            notes.append(f"the flute length is shorter than the cone ({fmt(cone)} mm): check --dia, --tip-dia and --angle")
    if t["shank"] > t["dia"] + 0.05 and kind != "chamfer":
        notes.append("the neck between the flutes and the shank isn't modelled: Fusion runs the shank down to the shoulder")
    if re.search(r"non[- ]?cent", rows.get("cut type", ""), re.I):
        notes.append("the listing says non-center cutting: ramp or helix into the cut, don't plunge")
    if notes:
        print("Check:")
        for note in notes:
            print(f"  - {note}")
    print(f"\nwrote {out}")
    print("import it in Fusion's Tool Library, then drag the tool into your library")


if __name__ == "__main__":
    main()
