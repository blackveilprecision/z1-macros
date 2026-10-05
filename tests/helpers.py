"""Shared helpers: run a macro's script, and read back the G-code it writes."""

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Studio origin corner -> where the stock sits in program coordinates, as fractions of its size
ORIGINS = {
    "topFrontLeft": (0.0, 0.0),
    "topFrontRight": (1.0, 0.0),
    "topBackLeft": (0.0, 1.0),
    "topBackRight": (1.0, 1.0),
    "topCenter": (0.5, 0.5),
}


def run(script, *args, env=None):
    """Run a script with this Python; output is decoded as UTF-8 on every platform."""
    environ = {**os.environ, "PYTHONIOENCODING": "utf-8", **(env or {})}
    environ = {k: v for k, v in environ.items() if v is not None}
    return subprocess.run(
        [sys.executable, str(script), *map(str, args)],
        capture_output=True, text=True, encoding="utf-8", env=environ,
    )


def moves(program):
    """(G0/G1, x, y, z, line) for every absolute move, in order. Probing (G38, G53, G91) is skipped."""
    x = y = z = None
    relative = False
    out = []
    for line in program.splitlines():
        code = line.split(";")[0].strip()
        if "G91" in code:
            relative = True
        if "G90" in code:
            relative = False
        m = re.match(r"(?:G90 )?(G[01])\b", code)
        if relative or not m:
            continue
        words = dict(re.findall(r"([XYZ])(-?\d*\.?\d+)", code))
        x = float(words["X"]) if "X" in words else x
        y = float(words["Y"]) if "Y" in words else y
        z = float(words["Z"]) if "Z" in words else z
        out.append((m.group(1), x, y, z, line))
    return out


def footprint(origin, width, length):
    """The stock's X and Y range in program coordinates."""
    ox, oy = ORIGINS[origin]
    return (-ox * width, (1 - ox) * width), (-oy * length, (1 - oy) * length)


def plunges_over_stock(program, origin, width, length, radius, top=0.0):
    """Lines that move the cutter down below `top` while any part of it is over the stock."""
    (x0, x1), (y0, y1) = footprint(origin, width, length)
    bad, z_prev = [], None
    for _, x, y, z, line in moves(program):
        going_down = z is not None and z_prev is not None and z < z_prev and z < top
        over = x is not None and y is not None and x0 - radius < x < x1 + radius and y0 - radius < y < y1 + radius
        if going_down and over:
            bad.append(line)
        z_prev = z
    return bad


def levels(program):
    """The Z of each cutting level, top first."""
    return [z for code, _, _, z, line in moves(program) if code == "G1" and re.match(r"G1 Z", line)]
