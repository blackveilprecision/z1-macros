"""The stock reference: what probe-stock measured, kept up to date from Studio's log.

probe-stock.py saves a probe run as stock.json. A macro that cuts from it notes
what each job it writes will change in jobs.json, under the job file's MD5. When
Studio's log shows that exact file played to the end, the change is applied, so
the next job starts from the stock as it is now. A job stopped partway changes
nothing here: cutting again from the old top only cuts air where it had cut.

Heights are stored above the tool setter: a surface's machine Z less REFMZ, the
reference tool's setter reading, both printed by M498. The firmware offsets
every tool from the same point, so the heights hold across tool changes, a
re-seated probe and Z0 being set somewhere else. Positions are in stock
coordinates, from the front-left corner.

Nothing here writes to the machine. Z1_STOCK (a folder) replaces the one
stock.json and jobs.json are kept in, which the tests use.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
STUDIO_LOGS = Path.home() / "Library/Application Support/MakeraStudio/logs"  # macOS; the scripts take --log elsewhere
PROBE_JOB = "probe-stock.nc"
WHERE_JOB = "probe-stock-where.nc"
ORIGIN_MOVED = 0.1  # X0/Y0 further than this from where the probe job left them: the stock or the origin moved
KEEP_JOBS = 50      # jobs.json keeps the newest this many
TIP_SLACK = 1.0     # A side cut counts for a rod reading when the cut's bottom and the rod's tip are this close

# Origin corner -> its position in stock coordinates, as fractions of the stock's size from front-left
ORIGINS = {
    "topFrontLeft": (0.0, 0.0),
    "topFrontRight": (1.0, 0.0),
    "topBackLeft": (0.0, 1.0),
    "topBackRight": (1.0, 1.0),
    "topCenter": (0.5, 0.5),
}

# Studio's log, e.g.
#   Debug    | 2026-10-06 19:32:06 Tue | :0,  | "[INFO]19:32:06.707 - Playing file: /sd/gcodes/macros/probe-stock.nc"
#   ... "[INFO]19:33:29.793 - Normal info: EEPRROM Data: REFMZ:-77.650\n"
#   ... "[INFO]19:33:29.793 - Normal info: EEPRROM Data: G54: -190.091, -193.616, -82.602\n"
#   ... AAAAA_Executing upload for path upload_cmd: "upload /sd/gcodes/macros/probe-stock.nc"
#   ... "[INFO]19:31:14.512 - XMODEM::send start, mode: wifiMode, MD5: f084bb261f0f0bb6c178de99f1b7a1c5"
STAMP = re.compile(r"\| (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) ")
PLAYING = re.compile(r'Playing file: (.+?)"?\s*$')
UPLOAD = re.compile(r'upload_cmd: "upload (.+?)"')
UPLOAD_MD5 = re.compile(r"XMODEM::send start.*MD5: ([0-9a-fA-F]{32})")
REFMZ = re.compile(r"EEPRROM Data: REFMZ:(-?\d+(?:\.\d+)?)")
G54 = re.compile(r"EEPRROM Data: G54: (-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?), (-?\d+(?:\.\d+)?)")
SENT = re.compile(r"Normal info: ((?:G10|G92)\b[^\"\\]*)")  # Studio echoes what it sends, e.g. its own probes
STOPPED = re.compile(r"Normal info: ((?:Aborted|ALARM:|Soft Endstop)[^\"\\]*)|(System reset completed)")
FINISHED = "G28 means goto clearance position"  # every job here ends with G28
WANTED = ("Normal info", "Playing file", "upload_cmd", "XMODEM::send start", "System reset")


def fmt(value):
    s = f"{value:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def where():
    """The folder stock.json and jobs.json are kept in."""
    return Path(os.environ["Z1_STOCK"]) if os.environ.get("Z1_STOCK") else HERE


def md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest()


@dataclass
class Print:
    """One M498: G54, the origin's machine X, Y and Z, and REFMZ."""

    x: float
    y: float
    z: float
    refmz: float
    stamp: str

    @property
    def z0(self):
        """Z0's height above the tool setter."""
        return self.z - self.refmz


@dataclass
class Run:
    """A file Studio played, and what was logged from then until the next one, in order:
    ("print", Print), ("sent", a G10 or G92 Studio sent) and, once, ("end", "finished" or why it stopped)."""

    name: str
    stamp: str
    log: str
    line: int        # of the "Playing file" line: a log is only ever added to, so (log, line) is this run
    md5: str | None  # of the last upload of that file name before it played
    events: list = field(default_factory=list)

    @property
    def end(self):
        return next((value for kind, value, _ in self.events if kind == "end"), None)

    def prints(self):
        """What the job printed itself, before it ended."""
        out = []
        for kind, value, _ in self.events:
            if kind == "end":
                break
            if kind == "print":
                out.append(value)
        return out


def read_logs(where_):
    """Every file played in Studio's logs (a folder, or one log file), oldest first."""
    files = sorted(where_.glob("log_*.txt")) if where_.is_dir() else [where_] if where_.is_file() else []
    runs, uploads, uploading, refmz, run = [], {}, None, None, None
    for path in files:
        with open(path, encoding="utf-8", errors="replace") as f:
            for number, line in enumerate(f, 1):
                if not any(w in line for w in WANTED):
                    continue
                stamp = STAMP.search(line)
                stamp = stamp.group(1) if stamp else "?"
                m = PLAYING.search(line)
                if m:
                    name = m.group(1).strip().rsplit("/", 1)[-1]
                    run = Run(name, stamp, path.name, number, uploads.get(name))
                    runs.append(run)
                    continue
                m = UPLOAD.search(line)
                if m:
                    uploading = m.group(1).strip().rsplit("/", 1)[-1]
                    continue
                m = UPLOAD_MD5.search(line)
                if m:
                    if uploading:
                        uploads[uploading] = m.group(1).lower()
                    uploading = None
                    continue
                m = REFMZ.search(line)
                if m:
                    refmz = float(m.group(1))
                    continue
                if run is None:
                    continue
                m = G54.search(line)
                if m and refmz is not None:
                    run.events.append(("print", Print(*map(float, m.groups()), refmz, stamp), stamp))
                    continue
                m = SENT.search(line)
                if m:
                    run.events.append(("sent", m.group(1).strip(), stamp))
                    continue
                m = STOPPED.search(line)
                if m and run.end is None:
                    run.events.append(("end", (m.group(1) or m.group(2)).strip(), stamp))
                elif FINISHED in line and run.end is None:
                    run.events.append(("end", "finished", stamp))
    return runs


def jobs():
    """What every job written here does, newest last: name, md5, generated, kind ("probe", "where" or "cut"),
    text, and a probe job's plan or a cut's effect on the stock."""
    path = where() / "jobs.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        raise SystemExit(f"stockref: can't read {path} ({e})")


def record(job, kind, text, **extra):
    """Note what the job just written to `job` is, under its MD5, so its runs can be found in Studio's log."""
    digest = md5(job)
    entry = {"name": Path(job).name, "md5": digest, "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             "kind": kind, "text": text, **extra}
    entries = [e for e in jobs() if e["md5"] != digest] + [entry]
    where().mkdir(parents=True, exist_ok=True)
    (where() / "jobs.json").write_text(json.dumps(entries[-KEEP_JOBS:], indent=1) + "\n", encoding="utf-8")


def save(reference):
    where().mkdir(parents=True, exist_ok=True)
    (where() / "stock.json").write_text(json.dumps(reference, indent=1) + "\n", encoding="utf-8")


@dataclass
class State:
    """The stock as it is now: the saved probe run, and every job since that ran to the end applied to it."""

    probe: dict             # the run saved: name, stamp, log, line, md5
    width: float            # the sizes the probe job was written for
    length: float
    height: float
    origin: str             # Studio's origin corner
    bed: float              # the bed's height above the tool setter
    bed_from: str
    top: list               # [(u, t, height above the setter)]
    faces: dict             # {"x": [(t, tip height, u)], "y": [(u, tip height, t)]}: the sides, touched with the rod
    origin_xy: tuple        # G54 X/Y where the probe job left them
    machine: Print          # the last M498 in the log
    unsure: str = ""        # something since the last M498 that may have moved X0/Y0/Z0
    newer_probe: str = ""   # when probe-stock.nc ran again after the saved run
    unknown: str = ""       # the last job not written here that played since: it may have cut the stock
    history: list = field(default_factory=list)  # [(time, what)]
    notes: list = field(default_factory=list)    # jobs that didn't finish

    @property
    def z0(self):
        """Z0's height above the tool setter now."""
        return self.machine.z0

    def program(self, u, t):
        """Stock coordinates -> the jobs' coordinates (from the origin corner)."""
        ox, oy = ORIGINS[self.origin]
        return u - ox * self.width, t - oy * self.length

    def check(self, who):
        """Stop `who` if a job can't be cut from this: the origin may have moved, or there's a newer probe run."""
        if self.newer_probe:
            raise SystemExit(
                f"{who}: probe-stock.nc ran again at {self.newer_probe}, after the run in stock.json."
                " Run probe-stock.py save first"
            )
        if self.unsure:
            raise SystemExit(
                f"{who}: X0/Y0/Z0 may have moved since Studio's log last showed them: {self.unsure}."
                f" Run {WHERE_JOB} (probe-stock.py where writes it); it only prints them. Then run this again"
            )
        dx, dy = self.machine.x - self.origin_xy[0], self.machine.y - self.origin_xy[1]
        if max(abs(dx), abs(dy)) > ORIGIN_MOVED:
            raise SystemExit(
                f"{who}: X0/Y0 are {dx:+.3f}, {dy:+.3f} mm from where the probe run left them. If the stock moved,"
                " probe it again (probe-stock.py job); if only the origin did, set it back"
            )


def apply(state, effect):
    if effect["type"] == "top":  # the whole top cut flat at this height
        state.top = [(u, t, min(h, effect["h"])) for u, t, h in state.top]
    elif effect["type"] == "side":  # the X side across from the origin cut to this width, from the top down to this height
        # A rod reading is the widest from its tip up. It's the new width when the cut went down to about the tip;
        # a reading from well below the cut still includes side the cut didn't touch
        # The cut can't make a side wider: where it was already narrower, the reading stays
        left = ORIGINS[state.origin][0] == 0
        cut = (lambda u: min(u, effect["width"])) if left else (lambda u: max(u, state.width - effect["width"]))
        state.faces["x"] = [(t, tip, cut(old) if tip >= effect["bottom"] - TIP_SLACK else old)
                            for t, tip, old in state.faces.get("x", [])]
    else:
        raise SystemExit(f"stockref: unknown effect {effect['type']!r} in jobs.json; update the repo")


def fold(reference, runs, registry, until=None):
    """The State: the probe run in `reference`, then every run after it in the log, or up to the run at
    `until` ((log, line)): probe runs before that one count as known, they print everything they change."""
    p, s, m = reference["probe"], reference["stock"], reference["machine"]
    state = State(
        probe=p, width=s["width"], length=s["length"], height=s["height"], origin=s["origin"],
        bed=reference["bed"], bed_from=reference["bed_from"], top=[tuple(x) for x in reference["top"]],
        faces={axis: [tuple(x) for x in pts] for axis, pts in reference["faces"].items()},
        origin_xy=(m["x"], m["y"]), machine=Print(m["x"], m["y"], m["z"], m["refmz"], m["stamp"]),
    )
    by_md5 = {e["md5"]: e for e in registry}
    start = next((i for i, r in enumerate(runs) if (r.log, r.line) == (p["log"], p["line"])), None)
    if start is None:
        state.unsure = f"the probe run of {p['stamp']} isn't in Studio's log any more, so what ran since is unknown"
        return state

    def after_end(run):  # what was logged once a run had ended: Studio's own probes, M498s from the console
        return run.events[next((i for i, (kind, _, _) in enumerate(run.events) if kind == "end"), len(run.events)):]

    for i, run in enumerate(runs[start:]):
        job = by_md5.get(run.md5)
        probe = run.name == PROBE_JOB or (job and job["kind"] == "probe")
        if i and until and (run.log, run.line) == tuple(until):
            break
        if i and probe and not until:
            state.newer_probe = run.stamp
            break
        known = i == 0 or job is not None or probe  # the probe run's own prints are in the reference already
        for kind, value, stamp in (run.events if i else after_end(run)):
            if kind == "print":
                state.machine, state.unsure = value, ""
            elif kind == "sent":
                state.unsure = f"Studio sent {value} ({stamp})"
            elif kind == "end" and i:
                if not known:
                    state.unsure = state.unknown = f"{run.name} played ({run.stamp})"
                elif value == "finished" and job and job.get("effect"):
                    apply(state, job["effect"])
                    state.history.append((stamp, f"{run.name}: {job['text']}"))
                elif job and job.get("effect"):
                    state.notes.append(
                        f"{run.name} stopped at {stamp} ({value}); what it cut isn't counted, so running a job"
                        " from this again cuts air where it had cut"
                    )
        if i and run.end is None:
            if not known:
                state.unsure = state.unknown = f"{run.name} played ({run.stamp})"
            elif job and job.get("effect"):
                state.notes.append(f"{run.name} started at {run.stamp}; the log doesn't show it ending, so it isn't counted")
    return state


def load(log=STUDIO_LOGS, until=None):
    """The stock as it is now, from stock.json and Studio's log (or as it was before the run at `until`)."""
    path = where() / "stock.json"
    try:
        reference = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise SystemExit(
            "stockref: no stock.json yet. Write the probe job (probe-stock.py job), run it on the machine,"
            " then save what it measured (probe-stock.py save)"
        ) from None
    except json.JSONDecodeError as e:
        raise SystemExit(f"stockref: can't read {path} ({e})") from None
    return fold(reference, read_logs(log), jobs(), until)
