---
name: z1-gcode
description: Firmware facts and working patterns for writing, generating and reviewing G-code for the Makera Z1 and Z1 Pro (Carvera Z1) desktop CNC, which runs Makera's Smoothieware fork (firmware 1.1.x) and is driven from Makera Studio. Use it whenever you write or change a G-code generator or macro in this repo, review an .nc or .cnc file meant for a Z1, design probing, Z0, work-offset or tool-change logic for it, or work out why a Z1 job did something unexpected, even if the user only says "the machine", "the macro" or "the job".
---

# G-code for the Makera Z1

The Z1 and Z1 Pro run MakeraZ1Firmware 1.1.2 (github.com/MakeraInc/MakeraZ1Firmware), a fork of Smoothieware. Jobs are uploaded to the machine and started from Makera Studio. The generators in this repo are Python 3, standard library only: they do all the math and write plain G-code.

Every fact below was read in the firmware source (paths are under `src/modules/`, with the function) or seen in Studio's logs. "Unverified" or "inferred" marks the rest. [firmware-reference.md](firmware-reference.md) has more detail, line hints, the sequences Studio and the firmware run, and how to check something in the source yourself.

Don't go by Carvera documentation or the Carvera Community Firmware: the community firmware has `#` variables and O-word IF/WHILE (`Gcode::set_variable_value`, `utils/player/OCodeHandler.cpp`); the Z1 firmware has none of that.

## How a line is read

- **No variables, expressions or loops.** `Gcode::get_value` (`communication/utils/Gcode.cpp`) runs `strtof` on the text after a letter and returns 0 when nothing parses; `Gcode::has_letter` is true if the letter appears anywhere. `Robot::process_move` moves every axis whose letter is present, so `G1 Z[#<depth>]` goes to Z0 and `F[#<feed>]` is F0, which halts with `Undefined feed rate`. Lines starting with `#`, `O` or `%` get "ok - ignore" (`GcodeDispatch::on_console_line_received`), so an O-word loop body runs once, straight through. Upstream Smoothieware v1 parses numbers the same way (`parse_float`). Do the math in the generator.
- **Upper case only.** A line that starts with a lower-case letter is a console command (`SimpleShell`, `Player`: `play`, `abort`...), not G-code. Lower-case words inside a line are ignored.
- **A comment ends the line.** `;` or `(` cuts everything after it: `G1 X1 (note) Y2` never moves Y.
- **No `G90`/`G91` text in comments on a line that starts with G.** Makera's dispatcher moves the first `G90` (else `G91`) it finds anywhere in such a line to the front before it strips comments, so `G0 Z5 ; not G91` runs relative. Upstream doesn't do this.
- **Keep every line within 63 characters.** While a file plays, each line goes into a 64-byte slot (`StaticQueue` in `utils/player/Player.h`, filled by `SerialConsole::on_serial_char_received`). Text past character 63 is dropped: harmless in a comment, silently lost in G-code.
- **Several commands per line work.** The dispatcher splits at each G, M or T word (an S goes with the M beside it): `G90 G0 X1`, `S12000 M3` and `T1 M6` are fine.
- **Unknown G and M codes are ignored, not rejected.** Nothing warns you. So are `M0`/`M1` (commented out in `Robot::on_gcode_received`); nothing in the source handles `M4`.

## Motion

- Emit `G21`. `G20` converts X/Y/Z/I/J/K/F on G0-G3, G10 and G92 (`Robot::to_millimeters`), but not G38 distances or feeds, and never A.
- `F` on a `G0` sets the rapid rate for every later `G0` (`Robot::process_move` stores it as `seek_rate`). Keep F off G0.
- The firmware doesn't reject a high F; the planner clamps it to per-axis limits in the machine's config, which isn't public. Studio's limits for the Z1 are 1200 mm/min and 13000 rpm (table `t_MachineType` in `~/Library/Application Support/MakeraStudio/makera_library.db`). The generators' `MAX_FEED`/`MAX_RPM` come from there.
- `G2`/`G3` take `I`/`J`/`K` (`Robot::compute_arc`, planes G17/G18/G19). There is no `R` form: without I/J/K the radius is 0 and no XY move happens (read from the code, not run).
- `G53` applies only to the G0/G1 on the same line: `G53 G0 Z-3`.
- Canned cycles (`tools/drillingcycles`) load only if the machine's config sets `drillingcycles.enable` (default off; unverified on the Z1) and run only after `G98`/`G99`. Without them `G81` does nothing and the following bare `X.. Y..` lines become G0/G1 moves. Write drilling as G0/G1.
- `G4 P` is seconds: the Z1 runs in grbl mode (`Kernel::Kernel`, default for CNC builds; the logs show grbl-mode replies like `error:Alarm lock`).
- Soft limits are checked on every move except probes; crossing one halts: `error:` then `Soft Endstop Y was exceeded - reset or $X or M999 required` (`Robot::append_milestone`, seen in logs).

## Spindle, air, end of job

- `S<rpm> M3`. M3 halts with `ERROR: No tool or probe tool!` unless the active tool is 1-999 (`tools/spindle/SpindleControl.cpp`), so it never spins the probe (T0). `M5` stops. S isn't clamped by the firmware. `M6` stops the spindle first.
- `M7`/`M9` are switches defined in the machine's config, not in the source; what M7 drives on a Z1 is unverified. Studio's exports send `M7` after each `T<n> M6` and `M9` at the end of each toolpath.
- `M2`, and `M30` in grbl mode, also send `M5` and `M9`, select G54, set G90 and reset the speed override (`GcodeDispatch`, `Robot::on_gcode_received`).
- `G28` is not homing. It goes to clearance Z, then clearance X/Y, in machine coordinates, ignores axis words, and logs `G28 means goto clearance position on CARVERA` (`ATCHandler::on_main_loop`; `Endstops::handle_park_g28` is empty). `G28.2` homes. Studio's exports end `G0 Z15`, `M9`, `M05`, `G28`, `M02`.

## Probing (the 3D probe is T0)

- `G38.2`-`G38.5` (`ZProbe::probe_XYZ`): X/Y/Z are distances from where the probe is now. G90/G91, work offsets and G20 don't apply. `F` (mm/min) is for that probe only.
- `G38.2`/`G38.4` that touch nothing: `ALARM: Probe fail`, halt. `G38.3`/`G38.5` stop at the end of the move. `G38.6` probes the tool setter (`ZProbe::calibrate_Z`) and always alarms on a miss. Other subcodes print an error and do nothing.
- If the probe is already triggered when G38.2-.5 starts, the machine halts. Lift first: `G91 G0 Z1`, then `G38.2 Z-2 F100`.
- Motion guard: if the probe triggers during an X/Y move or a downward Z move that isn't a G38, the firmware stops and halts with `ALARM: unexpected probe trigger` (`ZProbe::read_probe`, `ZProbe::on_idle`; seen in the logs when jogging down onto stock). Lift between points by more than the stock's height variation.
- Studio's own Z probe (`ATCHandler::fill_zprobe_scripts`, echoed in the logs): `G53 G0 Z-3`, `G90 G0 X.. Y..`, `G38.2 Z-108 F500`, `G91 G0 Z1`, `G38.2 Z-2 F100`, `G10 L20 P0 Z0`, `G91 G0 Z1`. The numbers come from the machine's config (as logged on one Z1 Pro). The repo's first probe point copies this.

## Tools, Z0 and what the machine stores

- `T<n> M6` (`ATCHandler::on_gcode_received`, manual change on the Z1). If T<n> is already active, nothing happens: no prompt, no measuring (seen in the logs with `T0 M6` and the probe already in). Otherwise it stops the spindle, moves to the change position, waits for the user to confirm in Studio, measures the tool on the tool setter (`G38.6` fast, then slow) and makes it active. For T0 it then checks the probe answers (`ERROR: Probe dead or not set, please charge or set first!`). It needs a homed machine.
- `G10 L20 P0 Z0` makes the current position Z0 of the active work offset (`P0` current, `P1` G54, `P2` G55...). Any G10 with a Z also makes the current tool the reference tool and clears the tool-length offset (`Robot::on_gcode_received` case 10, `ATCHandler::on_set_public_data`); tools measured later are offset by the difference. So measure (M6), set Z0 with that tool, then M6 the next one. Because `T0 M6` doesn't re-measure a probe that is already T0, a probe re-seated since its last measurement would give later tools a wrong Z0 (inferred).
- Every `G10 L2`/`L20` that targets G54 writes G54 (X, Y, Z, A, B) to the EEPROM, one write whichever axes it names. A G10 with Z may write the reference tool too, and tool changes write the tool number and length. G55-G59.3 and G92 live in RAM and are lost at reset (`M500` may save them to config-override if the config allows; unverified on the Z1).
- `M498` prints the stored data to every connection, so it reaches Studio's log from inside a file: five `EEPRROM Data:` lines (sic) ending `G54: x, y, z`; z is the machine Z of Z0. `M499` prints `tool:<n> ref:<mz> cur:<mz> offset:<tlo>`. **Never emit `M498.2`: it erases that EEPROM data.**
- Leave G92 alone. `G92` without words, `G92.1` and `G92.2` zero it (`Robot::on_gcode_received` case 92). Laser mode keeps its offset there (`Robot::setLaserOffset`), and Studio's app contains `G92 X0 Y0 Z0 A0` next to "Current position set as origin" (strings in the binary; not seen in the logs).
- Auto leveling (a Studio start option) bends every later move until `M370` or `M561` or a reset (`zprobe/CartGridStrategy.cpp`). Jobs that set their own Z0 send `M370` and tell the user to start with Auto leveling off.

## Output while a file plays

Studio's runs log no per-line replies: `Player::on_main_loop` gives each file line a null stream unless the file was played with `-v`. `ok`, `[PRB:...]`, and errors written to the command's own stream are lost. Anything printed to every stream still arrives: `M498`, `M499`, the G28 message, each step of a tool change and `Done ATC`, pauses and resumes, `Aborted by halt`, many alarms. A `G38.2` miss inside a file halts the machine, but its `ALARM: Probe fail` line is lost; expect only `Aborted by halt` (inferred). There is no "Done printing file" for Studio runs. To get a number out of a job, store it and print it (`G10 L20 P0 Z0`, then `M498`) and read it back with the makera-studio-log skill.

## What Studio adds around your file

- Before the first line Studio sends `buffer M495 X<x> Y<y> P1`, "Goto path origin first": `G53 G0 Z-3`, then `G90 G0 X<x> Y<y>` in work coordinates. In the logged runs X/Y were the low corner of Studio's preview of the file (0, 0 for files with no cutting moves). Its preview reads `G38.2` X/Y as positions: a file with `G38.2 X-20` and `G38.2 Y-20` got `M495 X-20Y-20P1` and halted on `Soft Endstop Y was exceeded` before its first line (logged 2026-10-06). Keep probe searches short toward the machine's limits and write them after `G91` (the firmware takes G38 as relative anyway; whether Studio's preview then reads them as relative is unverified). With Studio's Z probe or auto leveling ticked, the M495 carries `O F` or `A B I J H` and probes before your first line.
- `;@MKR|...` lines are Studio's export header (machine, material, stock, origin, tools, max feed, time, toolpaths). The firmware sees comments; Studio uses them for its preview and tool list. Copy the block in `build()` of `surface-stock/surface-stock.py`. Which fields Studio requires is untested.

## Patterns that work without variables

Read these in the repo rather than re-deriving them:

- **Z0 only moves up**: `probe_block()` in `surface-stock/surface-stock.py`. Probe the first point Studio's way and set Z0. At each later point lift `probe_clearance` above the current Z0, `G38.3` down exactly that far (it stops on a higher surface, or at Z0), then `G10 L20 P0 Z0`. Z0 ends on the highest point. Ran on a Z1 Pro, firmware 1.1.2.
- **Measure, log, generate**: `probe-stock/`. Its job sets Z0 (or X0/Y0 for side touches) on every point with `G38.2` and prints it with `M498`; `save` reads the values back from Studio's log (`stockref.read_logs()`) into `stock.json`, as heights above the tool setter (G54 Z minus REFMZ). Macros such as `surface-to-lowest-point/` do the math from that and write the next job.
- **Plunge beside the stock**: `raster()` and `build()` start each level off the stock and cut across; tests check it with `plunges_over_stock()` in `tests/helpers.py`.
- **Studio's dialect**: header, `G90 G21`, `M370`, probing, `T1 M6`, `M7`, `G0 X Y`, `S<rpm> M3`, `G0 Z15`, passes, `G0 Z15`, `M9`, `M05`, `G28`, `M02`.
- **Tests read the G-code, not the machine**: `tests/helpers.py` (`run`, `moves`, `levels`, `footprint`, `plunges_over_stock`); one `tests/test_<macro>.py` per macro.

## Reviewing a Z1 job

- Every value is a literal number: no `#`, `[`, `O` words or expressions.
- Upper case; no line over 63 characters; no `G90`/`G91` inside a comment; no `(` comment before G-code on the same line.
- `G21` and `G90` set at the top; G91 used only for short relative moves and switched back.
- G38 distances are relative; anything that must touch uses `G38.2`; the probe is lifted before the next probe.
- Every `G0`/`G1` with the probe fitted clears the stock: no X/Y move or downward move into it.
- `M3` comes after a `T<n> M6` with n >= 1; no F on G0; F and S within Studio's limits.
- No `G92`, `G92.1`, `M498.2` or `M500`; work offset changes only through `G10 L20 P0`, and as few as needed (each is an EEPROM write).
- `M370` at the start of a job that sets its own Z0; the user is told to start with Auto leveling off.
- Values to read later are printed with `M498` (or `M499`), not expected from `[PRB:...]`.
- The plunges land off the stock, and anything clamped must sit below the deepest pass.
- Say what ran on a machine and what was only checked by tests.

## Checking the firmware yourself

Clone `https://github.com/MakeraInc/MakeraZ1Firmware` (shallow is enough; `version.txt` gives the version). Start from `communication/GcodeDispatch.cpp` (how a line is split and dispatched), then the module that handles the code: `robot/Robot.cpp` (motion, G10, G92, G54-G59), `tools/zprobe/ZProbe.cpp` (G38), `tools/atc/ATCHandler.cpp` (M6, M49x, G28), `utils/player/Player.cpp` (file playback, output, pause). The reference file lists where each fact lives and the grep that finds it.
