---
name: makera-studio-log
description: Find and read Makera Studio's log files (log_*.txt) to see what a Makera Z1 or Z1 Pro did during a job or by hand, and pull values out of a run - M498 Z0 (G54) offsets, probe results, tool lengths, tool changes, alarms, pauses and aborts. Use it whenever the user asks what happened on the machine, why a job stopped or alarmed, which version of a file ran, or wants numbers from a probe run, and whenever code has to parse Studio's log (like last_probe_run() in surface-to-lowest-point), even if they don't say "log".
---

# Makera Studio's log

Studio logs what it sends and what the machine sends back to it. It is the best record of what a job did. All of this was checked against Studio's logs on macOS with a Z1 Pro on firmware 1.1.2; the firmware side is in the z1-gcode skill. [log-reference.md](log-reference.md) has the full message list, interactive sequences and the Chinese messages.

## Where the logs are

- macOS: `~/Library/Application Support/MakeraStudio/logs/log_YYYY-MM-DD-HH-MM-SS.txt`. Windows and Linux: not verified. Look for `log_*.txt` in Studio's application-data folder, or ask the user.
- The name is the time of the file's first line. Studio starts a new file when it launches (first line `======= System & OpenGL Info =======`) and with the first line it logs after midnight, so a run that crosses midnight is split over two files.
- A day is a few MB, mostly camera frames (`[Video] frame #...`) and touch events. Studio appends to the newest file while it runs. Treat the logs as read-only.
- `~/Library/Application Support/MakeraStudio/GCodes/<folder>/<name>` is Studio's cached copy of files on the machine, which plays its own copy at `/sd/gcodes/<folder>/<name>`. The cache is replaced on the next upload or open, so check the MD5 (below) before assuming it is what ran.
- Don't open `config.ini` in the `MakeraStudio` folder: it holds Studio's login tokens.

## Line format

```
Debug    | 2026-10-04 14:13:28 Sun | :0,  | "[INFO]14:13:28.168 - Normal info: EEPRROM Data: G54: -121.866, -143.602, -69.031\n"
```

- Qt level (`Debug`, `Warning`, `Info`, padded), Studio's clock to the second with the weekday, a source field that is always `:0, `, then the message.
- Connection and machine messages are quoted and start with `[INFO]`, `[WARN]`, `[ERROR]` or `[DEBUG]` and the time to the millisecond. Text from the machine follows `Normal info: `. Everything else is Studio talking about itself.
- Escapes are literal: `\r\n` and `\n` are two characters, and one entry can hold several lines (`[Caution: Unlocked]\nok\n`). Some firmware messages arrive as two entries (`error:` then the text).
- UTF-8 with LF endings, and Chinese in some Studio messages (translated in the reference). Open with `errors="replace"`: one file has raw binary, some entries run over several physical lines, and a few lines are torn (two writes interleaved). Search within a line with `re.search`; don't anchor at the start.

## What a file run looks like

surface-to-lowest-point's former probe job (now `probe-stock.nc`), with the `Debug    | <date> | :0,  |` prefix, the quotes and the `Normal info: ok` replies left out:

```
[INFO]18:36:04.630 - Auto command executed: buffer M495 X0Y0P1\n
[INFO]18:36:04.740 - Playing file: /sd/gcodes/macros/surface-to-lowest-point-probe.nc
[INFO]18:36:05.211 - Normal info:   File size 1588\r\n
[INFO]18:36:05.211 - Normal info: M495 X0Y0P1\n\r\n
[INFO]18:36:05.211 - Normal info: Goto path origin first\r\n
[INFO]18:36:05.212 - Normal info: G53 G0 Z-3.000\r\n
[INFO]18:36:05.212 - Normal info: G90 G0 X0.000 Y0.000\r\n
[INFO]18:36:05.212 - Normal info: Done ATC\r\n
[INFO]18:36:05.725 - Normal info: G53 G0 Z-3.000\r\n          <- T0 M6: tool change starts
...
[INFO]18:36:12.861 - Tool change confirmed by user.
...
[INFO]18:36:13.206 - Normal info: G38.6 Z-108.000 F500.000\r\n
[INFO]18:36:22.294 - Normal info: [PRB:-9.259,-12.709,-77.726:1]\n
[INFO]18:36:22.295 - Normal info: G91 G0 Z1.000\r\n
[INFO]18:36:22.295 - Normal info: G38.6 Z-2.000 F100.000\r\n
[INFO]18:36:23.021 - Normal info: [PRB:-9.259,-12.709,-77.654:1]\n   <- tool length (TOOLMZ)
...
[INFO]18:36:26.853 - Normal info: M493.2 T0\r\n               <- T0 is now in the spindle
[INFO]18:36:33.964 - Normal info: Done ATC\r\n
[INFO]18:36:41.738 - Normal info: EEPRROM Data: TOOL:0\n      <- first M498
[INFO]18:36:41.760 - Normal info: EEPRROM Data: TLO:0.000\n
[INFO]18:36:41.760 - Normal info: EEPRROM Data: TOOLMZ:-77.654\n
[INFO]18:36:41.760 - Normal info: EEPRROM Data: REFMZ:-77.654\n
[INFO]18:36:41.760 - Normal info: EEPRROM Data: G54: -190.091, -193.616, -55.309\n
...                                                           (one block per M498)
[INFO]18:37:25.245 - Normal info: G28 means goto clearance position on CARVERA\n
```

- A run starts at `Playing file:` (the path on the machine). Just before it Studio sends `buffer M495 X.. Y.. P1`, which moves to the path origin before the file's first line. `  File size N` means the machine accepted the file.
- The file's own lines leave no trace: no `ok`, no `[PRB:...]`, no errors. What does appear: `M498` blocks (five lines each; the last number is the machine Z of Z0), `M499` (`tool:.. ref:.. cur:.. offset:..`), the `G28` message, and every step of a tool change with `Done ATC`.
- A tool change shows its steps from `G53 G0 Z-3.000` to `Done ATC`, Studio's `Tool change confirmed by user.`, two `[PRB:...]` from the tool setter (the second Z is the tool's length, logged as TOOLMZ), and `M493.2 T<n>` naming the new tool. `T<n> M6` for the tool already in the spindle logs nothing.
- There is no "Done printing file". Jobs ending in `G28` (Studio's exports and this repo's jobs) end with `G28 means goto clearance position on CARVERA`; a file that ends with only `M02` just goes quiet.
- Pause: `Suspending , waiting for queue to empty...`, `Suspended, resume to continue playing`. Resume: `Resuming playing...`, `Restoring saved XYZ positions and state...`, `Playing file resumed`.
- Stopped from Studio: `Aborted playing or paused file.` Halted: `Aborted by halt`, with an `ALARM: ...` line next to it (`ALARM: Abort during cycle` after an emergency stop or reset). Then Studio's `Machine unlocked (alarm cleared)` and `[Caution: Unlocked]`; commands sent while halted get `error:Alarm lock`.
- An alarm raised by a line of the file itself (a `G38.2` miss, say) loses its own text; expect only `Aborted by halt` (from the firmware source, not seen yet). Alarms the firmware prints to every connection do show: `ALARM: unexpected probe trigger`, `ALARM: Spindle alarm triggered -  power off/on required`, `ALARM: Z motor alarm triggered -  reset required`, `Soft Endstop ...`.
- `Machine disconnected` ... `Machine connected` inside a run: the machine keeps going, but whatever it printed while Studio was away is missing (inferred). Check for this before trusting a count of M498 blocks.

Commands sent by hand (Studio's buttons or MDI) get their replies logged, so their `[PRB:x,y,z:1]` (machine coordinates; last field 1 = touched, 0 = missed), `ALARM: Probe fail` and `Please change the tool to: T<n>` show. In a file run the only `[PRB:...]` lines come from tool changes and from probing Studio adds with its `M495`, never from the file's own G38 lines.

## Which version of a file ran

An upload logs `upload_cmd: "upload /sd/gcodes/<folder>/<name>"`, then `XMODEM::send start, mode: wifiMode, MD5: <md5>`, then `Normal info: Info: upload success: /sd/gcodes/<folder>/<name>.`. The MD5 is the file's: compare it with `md5 -q file.nc` (macOS) or `hashlib.md5(Path(f).read_bytes()).hexdigest()`. The last upload of that path before `Playing file:` is the version that ran. Opening a file in Studio logs `XMODEM::recv start, mode: wifiMode, md5: <md5>` for the machine's copy, and `Total lines: N` after `Loading Gcode file:` is Studio's line count for its preview.

## Pulling values out of runs

Standard library only. `runs()` cuts the logs into one dict per `Playing file:`, holding the messages up to the run's end marker (lines logged later are interactive use):

```python
import re
from pathlib import Path

LOGS = Path.home() / "Library/Application Support/MakeraStudio/logs"
MSG = re.compile(r'\| (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \w+ \|.*?"\[(\w+)\][\d:.]+ - (.*)"\s*$')
END = ("Aborted playing or paused file", "Aborted by halt", "G28 means goto clearance")


def events(paths):
    """(log file, time, text) for each message; machine output starts with 'Normal info: '."""
    for path in paths:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = MSG.search(line)
                if m:
                    yield path.name, m.group(1), m.group(3).replace("\\r", "").replace("\\n", " ").strip()


def runs(paths):
    run = None
    for log, when, text in events(paths):
        if text.startswith("Playing file: "):
            if run:
                yield run
            run = {"file": text[14:], "log": log, "start": when, "msgs": [], "end": None}
        elif run and not (run["end"] and when > run["end"][0]):  # keep lines from the end marker's second
            run["msgs"].append((when, text))
            if text.startswith("Normal info: ") and any(e in text for e in END) and not run["end"]:
                run["end"] = (when, text[13:])
    if run:
        yield run


for run in runs(sorted(LOGS.glob("log_*.txt"))):
    machine = [(w, t[13:]) for w, t in run["msgs"] if t.startswith("Normal info: ")]
    z0 = [float(t.rsplit(",", 1)[1]) for _, t in machine if t.startswith("EEPRROM Data: G54:")]
    tools = [t.split()[-1] for _, t in machine if re.match(r"M493\.2 T\d", t)]
    print(run["start"], run["file"], f"{len(z0)} x M498", tools, run["end"] or "no end marker")
    for w, t in machine:
        if re.search(r"ALARM|rror|Abort|Suspend|resumed", t):
            print("   ", w, t)
    if any(t == "Machine disconnected" for _, t in run["msgs"]):
        print("    Studio lost the connection during this run; output may be missing")
```

It reads the files in date order, so a run that crosses midnight stays whole. Print `run["msgs"]` to read one run in full. Filter on `run["file"].endswith("/name.nc")` to pick a job. `z0` holds the G54 Z after each `M498`; differences between entries are height differences (higher surface, less negative).

`read_logs()` in `probe-stock/stockref.py` is the repo's working parser: it reads every log in date order and returns each file played, with its upload MD5, every `M498` (G54 and REFMZ) and Studio-sent `G10`/`G92` logged until the next `Playing file:`, and whether it reached its final `G28` or stopped (`Aborted`, `ALARM:`, `Soft Endstop`). A run that crosses midnight stays whole. Its tests build fake logs in the same format (`studio_log()` and `Machine` in `tests/helpers.py`).

## Quoting the log in an issue, commit or PR

Paste the lines from `Playing file:` onward, without the noise. Remove machine IP addresses (`Connecting to`, `ws://...`), the serial number (`sn = ...`), local paths with the user name (write `~`), `Post request URL` lines and anything about tokens, and the names of the user's own job files unless they say otherwise.
