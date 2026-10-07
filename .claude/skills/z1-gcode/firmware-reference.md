# Z1 firmware reference

Read from MakeraZ1Firmware commit `b3a2e26` (2026-08-11, `version.txt` 1.1.2), compared with upstream Smoothieware (edge) and the Carvera Community Firmware. Paths are under `src/`. Line numbers are hints for that commit only; search for the function name if they have moved. "Log" means seen in Makera Studio's logs on macOS with a Z1 Pro on 1.1.2.

Contents: [Map](#map-of-the-source) · [Parsing](#parsing-and-dispatch) · [Motion](#motion) · [Offsets and EEPROM](#work-offsets-tool-length-and-the-eeprom) · [Probing](#probing) · [Tool change](#tool-change-manual-z1) · [Spindle and end](#spindle-air-dwell-end-of-job) · [Playback and output](#file-playback-and-output) · [Studio's commands](#what-studio-sends) · [Not verified](#not-verified) · [How to check](#how-to-check-a-behaviour)

## Map of the source

| What | File | Function |
|---|---|---|
| Split a line, comments, G90/G91, halted state, M2/M30 | `modules/communication/GcodeDispatch.cpp` | `on_console_line_received` (L55) |
| Read a word's value | `modules/communication/utils/Gcode.cpp` | `has_letter` (L72), `get_value` (L96), `prepare_cached_values` (L200) |
| G0-G4, G10, G17-G21, G54-G59, G90/G91, G92, M2, M114, M203-M220 | `modules/robot/Robot.cpp` | `on_gcode_received` (L527), `process_move` (L1149) |
| Feed errors, soft limits, arcs | `modules/robot/Robot.cpp` | `append_line` (L1768), `append_milestone` (L1480), `compute_arc` (L2011) |
| G38.x, probe guard | `modules/tools/zprobe/ZProbe.cpp` | `on_gcode_received` (L383), `probe_XYZ` (L530), `calibrate_Z` (L611), `read_probe` (L170), `on_idle` (L738) |
| M6, M490-M499, M480, M495, M496, G28 | `modules/tools/atc/ATCHandler.cpp` | `on_gcode_received` (L1925), `on_main_loop` (L2771), `fill_*_scripts` (L126-L476) |
| Auto-leveling grid, M370/M561, G32 | `modules/tools/zprobe/CartGridStrategy.cpp` | `handleGcode` (L374) |
| Playback, buffered commands, pause/resume/abort, M600 | `modules/utils/player/Player.cpp` | `on_main_loop` (L655), `play_command` (L358), `suspend_command` (L995), `resume_command` (L1055), `abort_command` (L520) |
| File lines arriving from the controller board | `modules/communication/SerialConsole.cpp` | `on_serial_char_received` (PTYPE_PLAY_DATA, L224) |
| M3/M5 | `modules/tools/spindle/SpindleControl.cpp` | `on_gcode_received` (M3 at L44) |
| G28.x homing | `modules/tools/endstops/Endstops.cpp` | `on_gcode_received` (L1321) |
| Canned cycles | `modules/tools/drillingcycles/Drillingcycles.cpp` | `on_module_loaded` (L40), `on_gcode_received` (L198) |
| Lower-case and `$` commands | `modules/utils/simpleshell/SimpleShell.cpp` | `on_console_line_received` (L323) |
| EEPROM layout, halt reasons, grbl mode | `libs/Kernel.h` (`EEPROM_data` L97, `HALT_REASON` L58), `libs/Kernel.cpp` (`grbl_mode` L206) | |
| Which modules load | `main.cpp` | |

## Parsing and dispatch

- `GcodeDispatch::on_console_line_received` trims leading spaces, then by first character: `$` and lower case go to the shell (`SimpleShell`) and player (`play`, `abort`, `suspend`, `resume`, `buffer`...); `N` line numbers are stripped; `G`, `M`, `T`, `S` are G-code; `;`, `(` and blank lines get `ok`; a line starting with `X`, `Y`, `Z`, `A` or `F` is run as the last G0-G3 (F alone as G1); anything else (`#`, `O`, `%`, `I`, `R`...) gets `ok - ignore: [...]`.
- On a line starting with `G`, the first `G90` (or else `G91`) anywhere in the text is moved to the front (L97-L113). This runs before comments are stripped (L115-L119), so the text in a comment counts. Not in upstream.
- Comments: everything from the first `;` or `(` is removed.
- Splitting (L124-L158): a G command ends at the next `G`, `M` or `T` (also `S` if the rest has both S and M); an M command at the next `G` or `M`; a `T`/`S` command runs to the M after it. `G53` takes the G0/G1 that follows on the same line, or reuses the last G0/G1 if there is none.
- Values: `get_value` scans for the letter, runs `strtof` after it and keeps scanning if nothing parses; it returns 0 if no occurrence parses. `has_letter` only checks the letter is present, and is case-sensitive. Every module tests `has_letter` and then reads `get_value`, so `X[...]` is X0. `strtof` also skips spaces after the letter (`X 10` is X10). Upstream uses `parse_float` (`libs/nist_float.cpp`) the same way. The Carvera Community Firmware adds `Gcode::set_variable_value`/`get_variable_value` and `utils/player/OCodeHandler.cpp`; the Z1 firmware has neither.
- `prepare_cached_values`: the subcode (the `.n` of `G38.2`) is a 4-bit field, so subcodes above 15 wrap.
- Halted state (L165-L187): every command except M2, M5, M9, M30, M105, M114, M115, M119, M80, M81, M911, M503, M106, M107 and M999 is refused with `error:Alarm lock` (grbl mode) until `$X` or `M999`.
- Errors: only `Robot::append_line`/`append_arc` set `is_error` (zero or negative feed: `Undefined feed rate`, `feed rate < 0`), which prints `error:...` and `Entering Alarm/Halt state` and halts. Unknown G and M codes reach no handler and nothing is printed.
- Lower-case lines inside a file are shell commands too: `Player` and `SimpleShell` see every line that passes through `ON_CONSOLE_LINE_RECEIVED`, file lines included.

## Motion

- Motion mode is only set by a G0-G3 on the line (`Robot::on_gcode_received`); bare axis lines get the last mode from the dispatcher.
- `process_move`: absolute target = value + work offset - G92 offset + tool offset; relative (G91) target = value + current machine position. A and B are not unit-converted.
- `F` on G0 sets `seek_rate`, on G1-G3 `feed_rate` (L1274). Both stay until changed. Defaults come from config `default_feed_rate` and `default_seek_rate` (1000 and 3000 mm/min if absent; the Z1 config isn't public).
- Rate limits: `append_milestone` scales the rate down to config `x/y/z_axis_max_speed` (defaults 4000/4000/2000 mm/min if absent). Nothing rejects a high F.
- Arcs: `compute_arc` takes the radius from I/J/K in the current plane. No R word. Zero offsets give zero radius and `append_arc` returns without moving unless Z changes (read, not run).
- Soft limits (`append_milestone`): checked for homed axes unless probing. With `soft_endstop_halt` (the logged machine halts) it prints `error:` and `Soft Endstop <axis> was exceeded - reset or $X or M999 required`. With the A axis enabled on a Z1 the Y minimum is forced to -160.
- `Robot::is_homed_all_axes` halts with `ERROR:Machine has not been homed,Please home first!` (`play` refuses to start, tool measuring and Studio's automation need it).
- `G4`: grbl mode takes `P` as seconds (float), otherwise milliseconds; `S` adds seconds. grbl mode defaults to true for CNC builds (`Kernel::Kernel`, L206); the logs show grbl-mode texts (`error:Alarm lock`, `ALARM: Abort during cycle`), so the Z1 runs in grbl mode.
- `M220 S` speed override 10-1000 %; `M223 S` spindle override 50-200 %.
- Drilling cycles: module deleted at boot unless config `drillingcycles.enable` is true (default false). When loaded, `G98`/`G99` start a cycle (remembering the initial Z), `G81`/`G82`/`G83` drill with sticky `Z R F Q P`, `G80` ends; relative mode is refused.

## Work offsets, tool length and the EEPROM

- EEPROM layout (`libs/Kernel.h` `EEPROM_data`): `TLO`, `G54[3]`, `REFMZ`, `TOOLMZ`, `reserve`, `TOOL`, `G54AB[2]`. Loaded at boot: G54 into `wcs_offsets[0]` and TLO into the tool offset (`Robot::on_module_loaded`), tool number, REFMZ, TOOLMZ and TLO into the ATC handler (`ATCHandler::on_module_loaded`, L1502).
- Writes (`write_eeprom_data` call sites): `Robot.cpp` G10 when the target is G54 (L637); `G92.4 A.. R..` in G54 (L721); `Robot::saveToolOffset` (L2068, from `M493.1`); `ATCHandler` `M493.2 T<n>` when the tool number changes (L2197) and reference-tool updates (L3188); `Kernel::check_eeprom_data` at boot if a field is NaN.
- `G10 L2 P<n>` sets offset n to the given values; `G10 L20 P<n>` sets it so the current position reads the given values. `P0` is the active offset, `P1`-`P9` are G54-G59.3. Relative mode doesn't change G10. If the line has Z, it first tells the ATC to make the current tool the reference (`set_ref_tool_mz`: `ref_tool_mz = cur_tool_mz`, REFMZ written if it changed, tool offset 0) and clears the robot's tool offset (`Robot::clearToolOffset`).
- Tool length: after a tool-setter touch, `M493.1` (`ATCHandler::set_tool_offset`) takes the probe Z as `cur_tool_mz` and, if a reference exists (`ref_tool_mz < 0`), sets TLO = cur - ref and saves TLO and TOOLMZ.
- `G54`-`G59` select offsets 1-6, `G59.1`-`G59.3` 7-9 (`MAX_WCS` 9). Only G54 is in the EEPROM. `M500` writes all non-zero offsets as `G10 L2` lines to `/sd/config-override` if `save_g54` (defaults to grbl mode) and G92 as `G92.3` if `save_g92` (default false); nothing in this repo or Studio's logged runs uses M500.
- G92 (`Robot::on_gcode_received` case 92, RAM only): no words, `.1` or `.2` zero it; `.3` sets it directly; `.4` is manual homing (`G92.4 A.. S`/`R` re-seat the A axis); `.5` applies the laser offset. Laser mode puts its offset in G92 (`Robot::setLaserOffset`) and `Robot::clearLaserOffset` zeroes it.
- `M498`/`M498.1` print `EEPRROM Data: TOOL:`, `TLO:`, `TOOLMZ:`, `REFMZ:` and `G54: x, y, z` through `THEKERNEL->streams` (ATCHandler L2719). It prints the RAM copy, which G10 has just updated. Only G54 is shown, whatever offset is active. `M498.2` overwrites the EEPROM data with zeros (`Kernel::erase_eeprom_data`; the comment above it says "Show").
- `M499` prints `tool:%d ref:%.3f cur:%.3f offset:%.3f`; `M499.2` the tool positions; `M499.3`-`.5` beep.

## Probing

- `G38.2`-`.5` (`ZProbe::on_gcode_received` L458, `probe_XYZ`): X/Y/Z are a relative delta move; none is converted from inches; `F` is mm/min for that probe (default config `slow_feedrate`). `.4`/`.5` probe away from contact. Afterwards the position is reset to where the motors stopped and `[PRB:x,y,z:ok]` (machine coordinates, ok 1 or 0) goes to the command's stream.
- Fails: already triggered at the start → `Error:ZProbe triggered before move, aborting command.` and halt; move too small → halt; no touch on `.2`/`.4` → `ALARM: Probe fail` and halt. Subcodes other than 2-6 → `Error :Only G38.2 to G38.5 are supported`, nothing moves.
- `G38.6` (`calibrate_Z`): Z only, stops on the tool setter's calibrate pin, always alarms (`ALARM: Calibrate fail!`) on a miss. Used by tool measuring.
- Guard (`read_probe` L204, `on_idle`): with config `halt_on_probe_during_motion` (default true), a triggered probe during an X/Y move or a Z move in the negative direction outside probing stops all motors and halts with `ALARM: unexpected probe trigger`. It is suppressed for about a second after a probe hit (the `probe_detected` debounce). Upward Z moves are allowed, so lifting off a touch is safe.
- Studio's Z probe as the firmware runs it (`fill_zprobe_scripts`, from `M495 X Y O F`; log): `M497.5`, `M494.1`, `G53 G0 Z-3.000`, `G90 G0 X.. Y..`, `G38.2 Z-108.000 F500.000`, `G91 G0 Z1.000`, `G38.2 Z-2.000 F100.000`, `G10 L20 P0 Z0.000`, `G91 G0 Z1.000`, `M494.2`. The values are config: clearance Z, `toolrack_z`, fast/slow probe rates, retract, probe height.

## Tool change (manual, Z1)

`T<n> M6` with n different from the active tool (`ATCHandler::on_gcode_received` L1931, manual branch): the spindle is turned off (halt if it won't stop), the position and modal state are saved, and these scripts run, each echoed to Studio (log):

```
G53 G0 Z-3.000            ; clearance
G53 G0 X.. Y..            ; change position
M497.2
M490.1                    ; wait: Studio asks the user, then confirms
M493.2 T-1                ; no tool until measured
M494.1                    ; (T0 only) probe laser on
M497.3
G53 G0 Z-3.000
G53 G0 X.. Y..            ; tool setter
G38.6 Z-108.000 F500.000
G91 G0 Z1.000
G38.6 Z-2.000 F100.000    ; this [PRB] Z becomes TOOLMZ
M493.1                    ; store length (TLO = TOOLMZ - REFMZ)
G53 G0 Z-20.000
M492.3                    ; (T0 only) probe must answer, else halt
M494.2                    ; (T0 only)
M493.2 T<n>               ; tool is now active (EEPROM)
M494.2
```

Then the machine returns to clearance Z and the saved X/Y, restores the modal state and prints `Done ATC`. Measuring is skipped (and the machine halts) if not all axes are homed. `M491` re-measures the active tool the same way (source only). `M6` when n is already active does nothing. Interactive changes also print `Please change the tool to: T<n>`; from a file that line is lost.

## Spindle, air, dwell, end of job

- `M3` (`SpindleControl::on_gcode_received`): ignored when halted; halts with `ERROR: No tool or probe tool!` unless the active tool is 1-999; waits for queued moves, sets S, turns on. With vacuum or blowing modes on in Studio it also switches those. `M5` waits and stops. `PWMSpindleControl::set_speed` stores the rpm as given; the PWM is capped at 0.9 on the Z1.
- `M7`/`M8`/`M9`: only `Switch` modules from the config react, through `input_on_command`. No Z1 config in the source.
- `M2` (and `M30` in grbl mode): dispatcher sends M5 and M9 (`GcodeDispatch` L257); Robot selects G54, sets G90, resets M220 (L807).
- `G28` (subcode 0): `ATCHandler` sets `g28_triggered`; `on_main_loop` prints `G28 means goto clearance position on CARVERA`, goes to clearance Z, then clearance X/Y (machine coordinates). `Endstops::handle_park_g28` is empty in grbl mode. `G28.2` homes, `G28.1` stores a park position that nothing uses.
- `M0`/`M1` are commented out. `M600` suspends a playing file (`Player::suspend_command`), `M601` resumes.
- `M888` bypasses the homed check and `M887` restores it (`Robot::override_homed_check`; the messages they print say the reverse). Don't use either in a job.

## File playback and output

- The motion board doesn't read the file. `play` sends the file name's CRC to the controller board, which streams lines back (`PTYPE_PLAY_DATA`). `SerialConsole::on_serial_char_received` splits them at `\n` into `gcode_buffer_queue`, a `StaticQueue` of 64-byte slots (`utils/player/Player.h`): 63 characters per line survive. A full queue logs `Alarm:push queue error at line N`. How the controller board handles longer lines is not in this source; Studio's exports end with a comment line of about 100 KB (a thumbnail, after `M02`) and play to the end.
- `Player::on_main_loop` dispatches each line with `current_stream`, which is null unless `play` had `-v` (`play_command` L387). Replies written to `gcode->stream` (`ok`, `[PRB:...]`, `ALARM: Probe fail`, `grid cleared and disabled`) are discarded; text printed with `THEKERNEL->streams->printf` reaches Studio. Search for that call to see what a job can report.
- `buffer <cmd>` queues a command that runs before the next file line, through `THEKERNEL->streams`, so its output and echo reach Studio. Studio uses it for `M495` at the start of every run.
- A halt during playback calls `Player::on_halt` → `abort_command`, printing `Aborted by halt`. Studio's stop prints `Aborted playing or paused file.`. Pause: `Suspending , waiting for queue to empty...`, `now save current pos...`, `Suspended, resume to continue playing`; resume: `Resuming playing...`, `Restoring saved XYZ positions and state...` (one straight `G1 X Y Z F1000` back to the saved work position), `Playing file resumed`.
- `Done printing file` is printed only to a `reply_stream` (M24/M32); Studio's runs never show it.

## What Studio sends

Seen in the logs, unless marked:

- Every run: `buffer M495 X<x>Y<y>P1` just before playing. Variants: `C<x2>D<y2>` scan margin, `O<dx>F<dy>` Z probe at path origin + offset (`O` without `F`: the 4th-axis Z probe), `A<w>B<h>I<nx>J<ny>H<z>` auto leveling (`G32R1...` on the machine), `R` rotation. The same M495 without `buffer` runs these interactively.
- Jogging: `G91`, `G0 X-10`, `G90` (Studio logs "Coordinate mode set to relative (G91)").
- `$X` to unlock (`[Caution: Unlocked]`), `M496.n` go to clearance/work origin/anchors, `M495.3` XYZ probe, `M480.1`-`.10` corner/pocket probing (source).
- Strings in Studio's app binary (not seen in logs): `G92 X0 Y0 Z0 A0`, `G92 X0 Y0 Z10 A0`, `G92.4 A0 R0`, `G10L20P0`, `G10L2P0X%1Y%2`, "Current position set as origin".
- Export header (Studio's cached exports):

```
;@MKR|BEGIN
;@MKR|SCHEMA|v=1.0.0
;@MKR|MACHINE|id=Z1|name=Makera Z1
;@MKR|MATERIAL|id=|name3=other|name1=Aluminum Alloys|name2=6061 Aluminum|uuid1=...
;@MKR|STOCK|id=cuboid|length=100|width=100|height=5|diameter=50
;@MKR|ORIGIN|id=0|type_name=topFrontLeft|x=-50|y=-50|z=2.5
;@MKR|CAM|id=MakeraStudio|name=MakeraStudio|v=0.1.2.0
;@MKR|UNIT|value=mm
;@MKR|MAXFEEDRATE|value=1200
;@MKR|TOOL|number=1|id=...|name=3.175*12mm Flat End(Metal)|type=Flat End|...
;@MKR|TIME|seconds=299.978
;@MKR|TOOLPATH|number=1|tool_number=1|name=[T1]...
;@MKR|END

G90 G21
;@MKR|TOOLPATH_START|toolpath_number=1
```

## Not verified

- The Z1's config (`/sd/config.txt` on the machine): max speeds and accelerations, default feeds, clearance positions, what M7/M9 switch, drilling cycles, `save_g54`, soft limits.
- The controller board's firmware: file storage, long lines, what Studio's commands look like on the wire.
- Which `;@MKR` fields Studio needs, and what Studio's 4th-axis toolpaths do with G92.
- EEPROM endurance.
- On a machine: R-form arcs, M600 in a file, M491, a G38.2 miss inside a file (expected: halt, only `Aborted by halt` logged).

## How to check a behaviour

1. Find who handles the code: `grep -rn "gcode->m == 498\|case 498" src/modules` (or `gcode->g == 38`). Robot handles most G codes in a `switch`; the ATC handler uses `if (gcode->m == ...)` chains. `src/main.cpp` lists the modules that load.
2. Read how the words are read: `has_letter`/`get_value`, `to_millimeters` (unit conversion), `subcode`.
3. Check where its output goes: `gcode->stream->printf` is lost during a file; `THEKERNEL->streams->printf` reaches Studio.
4. Check config: `->by_default(...)` gives the default, but the machine's own `config.txt` can override it and isn't public.
5. Check what is Makera's: compare with upstream (`git clone --depth 1 -b edge https://github.com/Smoothieware/Smoothieware`); Makera's changes often carry Chinese comments or dated initials (`lsf`, `zqq`).
6. Confirm on the machine with a job that can't hurt anything (spindle off, well above the work), and read the result in Studio's log.
