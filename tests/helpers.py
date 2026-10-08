"""Shared helpers: run a macro's script, read back the G-code it writes, and make up Studio's log."""

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROBE_STOCK = ROOT / "probe-stock" / "probe-stock.py"
X0, Y0, REFMZ = -190.091, -193.616, -77.65  # G54 X/Y and the probe's tool setter reading in the made-up logs

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


def log_line(text, stamp="2026-10-07 10:00:00"):
    return f'Debug    | {stamp} Wed | :0,  | "[INFO]{stamp[11:]}.000 - {text}"\n'


def studio_log(*events):
    """Studio's log, from events in order:
    ("uploaded", name, md5): the file uploaded; ("play", name, prints): played, each print an M498 (a Z, or (X, Y, Z));
    ("finish",): the job's last G28; ("raw", text): anything else Studio logs as "Normal info", e.g. "Aborted by halt".
    """
    out = []
    for kind, *rest in events:
        if kind == "uploaded":
            out.append(f'Debug    | 2026-10-07 10:00:00 Wed | :0,  | AAAAA_Executing upload for path upload_cmd: "upload /sd/gcodes/macros/{rest[0]}"\n')
            out.append(log_line(f"XMODEM::send start, mode: wifiMode, MD5: {rest[1]}"))
        elif kind == "play":
            out.append(log_line(f"Playing file: /sd/gcodes/macros/{rest[0]}"))
            for p in rest[1] if len(rest) > 1 else []:
                x, y, z = p if isinstance(p, tuple) else (X0, Y0, p)
                out += [log_line(f"Normal info: EEPRROM Data: {k}\\n") for k in ("TOOL:0", "TLO:0.000", f"TOOLMZ:{REFMZ}", f"REFMZ:{REFMZ}")]
                out.append(log_line(f"Normal info: EEPRROM Data: G54: {x:.3f}, {y:.3f}, {z:.3f}\\n"))
        elif kind == "finish":
            out.append(log_line("Normal info: G28 means goto clearance position on CARVERA\\n"))
        elif kind == "raw":
            out.append(log_line(f"Normal info: {rest[0]}\\r\\n"))
    return "".join(out)


class Machine:
    """A made-up Z1 and its Studio log in a folder, which also holds probe-stock's stock.json and jobs.json."""

    def __init__(self, folder):
        self.folder = Path(folder)
        self.logs = self.folder / "logs"
        self.logs.mkdir(exist_ok=True)
        self.env = {"Z1_STOCK": str(self.folder)}
        self.events = []

    def log(self, *events):
        """Add to the log. ("upload", path) logs that file's upload with the MD5 it has now."""
        for e in events:
            self.events.append(("uploaded", Path(e[1]).name, hashlib.md5(Path(e[1]).read_bytes()).hexdigest()) if e[0] == "upload" else e)
        (self.logs / "log_2026-10-07-00-00-01.txt").write_text(studio_log(*self.events), encoding="utf-8")

    def run(self, script, *args):
        return run(script, *args, env=self.env)

    def play(self, job, prints=(), end=("finish",)):
        """Upload a job and run it: it prints `prints`, then finishes (or ends with another event, or None).
        A file without G28 never logs finishing, as on the machine."""
        if end == ("finish",) and not re.search(r"^G28\b", Path(job).read_text(), re.M):
            end = None
        self.log(("upload", job), ("play", Path(job).name, list(prints)), *([end] if end else []))

    def probe(self, *args, prints, save=True):
        """Write probe-stock's job with `args`, run it printing `prints`, and save it. Returns (save's result, job)."""
        job = self.folder / "probe-stock.nc"
        r = self.run(PROBE_STOCK, "job", *args, "-o", job)
        assert r.returncode == 0, r.stderr
        self.play(job, prints)
        return (self.run(PROBE_STOCK, "save", "--log", self.logs) if save else None), job.read_text()
