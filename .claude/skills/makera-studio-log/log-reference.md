# Studio log reference

Messages seen in Makera Studio's logs (macOS, Z1 Pro, firmware 1.1.2), with where they come from. Numbers are examples. "Firmware" means the text is printed by MakeraZ1Firmware and arrives as `Normal info: <text>`; the z1-gcode skill's `firmware-reference.md` says which file prints it.

## Noise to skip

Most of a log is Studio talking to itself: `[Video] ...` camera frames and socket states, `Corrupt JPEG data`, touch and zoom events (`point.state()`, `Event type: QEvent::Touch...`, `manhattanLength`, `m_lastDistance`, `Clear TouchedMode`, `operator()`, `sizeHint`, `wheel ignore`), `on_sliderGcodePlay_valueChanged`, `setDisplayProgress`, `Discovery completed: no machines found`, Qt warnings (`QLayout: ...`, `QObject::connect ...`), heartbeat and reconnect lines, and `Normal info: ok`. Keep only the quoted `"[LEVEL]time - ..."` entries and drop `ok`.

## Runs

| Message | From | Meaning |
|---|---|---|
| `Auto command executed: buffer M495 X<x>Y<y>P1\n` | Studio | Queued before the file: go to the path origin. With `C D` it also scans the margin, `O F` Z-probes, `A B I J H` auto-levels. |
| `Playing file: /sd/gcodes/<folder>/<name>` | Studio | Start of a run (path on the machine). Preceded by `---file path "..."`. |
| `Normal info: Command buffered: M495 ...\n\r\n` | firmware | The buffered command was accepted. |
| `Normal info:   File size <bytes>\r\n` | firmware | The machine started reading the file. |
| `Normal info: M495 ...`, `Goto path origin first`, `G53 G0 Z-3.000`, `G90 G0 X.. Y..`, `Done ATC` | firmware | The buffered M495 running, each step echoed. |
| `Normal info: Auto scan margin` / `Auto z probe, offset: <dx>, <dy>` / `Auto leveling, grid: 3 * 3 height: 5.00` | firmware | Studio's start options in that M495. |
| `Normal info: Change to probe tool first!` | firmware | Automation asked for T0 when another tool was in. |
| `Normal info: G28 means goto clearance position on CARVERA\n` | firmware | A `G28` line ran. Last message of jobs that end with G28. |
| `Normal info: EEPRROM Data: TOOL:0` ... `G54: x, y, z` | firmware | An `M498`: five entries. `TLO` tool length offset, `TOOLMZ` measured length of the current tool, `REFMZ` reference tool, `G54` work offset (z = machine Z of Z0). |
| `Normal info: tool:1 ref:-77.481 cur:-75.610 offset:1.871` | firmware | An `M499` (format from the source; not in these logs). |
| `Normal info: Suspending , waiting for queue to empty...`, `now save current pos...`, `Suspended, resume to continue playing` | firmware | Paused. |
| `Normal info: Resuming playing...`, `Restoring saved XYZ positions and state...`, `Playing file resumed` | firmware | Resumed: one straight G1 at F1000 back to where it paused. |
| `Normal info: Aborted playing or paused file. \r\n` | firmware | Stopped from Studio. |
| `Normal info: Aborted by halt\n` | firmware | A halt (alarm, emergency stop, reset) ended the run. |
| `Normal info: Alarm:push queue error at line N` | firmware | The machine's line buffer overflowed (source only). |

`Done printing file` is in the firmware but never appears for Studio runs.

## Tool change

From a file, `T1 M6` with another tool in (`ok` replies left out):

```
Normal info: G53 G0 Z-3.000\r\n
Normal info: G53 G0 X-9.130 Y-12.790\r\n        <- change position
Normal info: M497.2\r\n
Normal info: M490.1\r\n                         <- waiting for the user
Tool change confirmed by user.                   <- Studio
Normal info: M493.2 T-1\r\n
Normal info: M497.3\r\n
Normal info: G53 G0 Z-3.000\r\n
Normal info: G53 G0 X-8.930 Y-12.790\r\n        <- tool setter
Normal info: G38.6 Z-108.000 F500.000\r\n
Normal info: [PRB:-8.930,-12.791,-75.676:1]\n
Normal info: G91 G0 Z1.000\r\n
Normal info: G38.6 Z-2.000 F100.000\r\n
Normal info: [PRB:-8.930,-12.791,-75.610:1]\n   <- this Z is the tool's TOOLMZ
Normal info: M493.1\r\n
Normal info: G53 G0 Z-20.000\r\n
Normal info: M493.2 T1\r\n                      <- T1 is in
Normal info: M494.2\r\n
Normal info: Done ATC\r\n
```

For T0 (the 3D probe) `M494.1` follows `M493.2 T-1`, and `M492.3`, `M494.2` come before `M493.2 T0`; a dead or unpaired probe stops there with `ERROR: Probe dead or not set, please charge or set first!`. Done by hand, the change starts with `Tool change command executed: e` (Studio) and `Please change the tool to: T0`. `M493.2 T-1` without a later `M493.2 T<n>` means the change was interrupted and the machine has no tool (from the source and the 1.1.2 release notes).

## Alarms and errors

| Message | Cause |
|---|---|
| `ALARM: unexpected probe trigger` | The probe touched something during a normal move (jogging down onto stock, or an X/Y move with the probe low). |
| `ALARM: Probe fail` with `[PRB:...:0]` | A `G38.2` touched nothing (seen from Studio's probing; from a file the text is lost). |
| `ALARM: Z motor alarm triggered -  reset required` | Z driver alarm; seen together with a probe fail. Needs a reset. |
| `ALARM: Spindle alarm triggered -  power off/on required` | Spindle fault; power cycle. |
| `ALARM: Homing fail` | Homing didn't find a switch. |
| `ALARM: Abort during cycle` | Emergency stop or reset (grbl mode). |
| `error:` then `Soft Endstop Y was exceeded - reset or $X or M999 required` | A move left the travel; halted. |
| `error:Alarm lock` | A command sent while halted was refused. |
| `ERROR:Machine has not been homed,Please home first!` | Needs homing first. |
| `ERROR: Probe dead or not set, please charge or set first!` | The probe didn't answer after T0 was fitted. |
| `Probe failed to complete, check the initial probe height and/or initial_height settings` | Auto leveling stopped. |
| `Machine unlocked (alarm cleared)` then `Normal info: [Caution: Unlocked]\nok\n` | Studio sent `$X`. |
| `System reset completed`, `Normal info: Rebooting machine in 3 seconds...` | Studio reset the machine. |
| `ERROR: No tool or probe tool!` | `M3` with T0 or no tool (source only). |

## By hand

- Jog: `Coordinate mode set to relative (G91)`, `Executing jog command: G0 X-10.000000 (direction: X-)`, `Coordinate mode set to absolute (G90)`, `Jog completed: G0 X-10.000000`. The jog itself isn't echoed by the machine.
- Studio's Z probe (`Auto command executed: M495 X..Y..O..F..`): `M497.5`, `M494.1`, `G53 G0 Z-3.000`, `G90 G0 X.. Y..`, `G38.2 Z-108.000 F500.000`, `[PRB:x,y,z:1]`, `G91 G0 Z1.000`, `G38.2 Z-2.000 F100.000`, `[PRB:...]`, `G10 L20 P0 Z0.000`, `G91 G0 Z1.000`, `M494.2`, `Done ATC`. `[PRB]` values are machine coordinates.
- Auto leveling: `G32R1X0Y0A68.180B23.180I3J3H5.000`, `Rectangular Grid Probe...`, `Probe start ht: ...`, `probe at 0,0 is 0.024 mm`, `DEBUG: X-180.520, Y-184.380, Z-0.005` per point, a grid table, `Max deviation from zero: ...`, `Probe completed.`.
- Studio's X/Y/corner probing echoes `G10 L20 P0 X-1.000`, `G10 L20 P0 Y1.000` and similar.

## Files and connection

- Upload: `upload_cmd: "upload /sd/gcodes/<folder>/<name>"`, `XMODEM::send start, mode: wifiMode, MD5: <md5>`, `VIEW sent, total packets=N`, `Transmission successful (FILE end flag received).`, `文件上传完成: File uploaded successfully: <local path>`, `Normal info: Info: upload success: /sd/gcodes/<folder>/<name>.`.
- Open: `Select button clicked for: "/sd/gcodes/..."`, `XMODEM::recv start, mode: wifiMode, md5: <md5>`, `文件对比md5完成: The file MD5 comparison is successful. No need to download the file`, then `Loading Gcode file: "<cache path>"`, `Total lines: N`, `Total distance: ...`, and the preview bounds (`xmin: ... xmax: ...`).
- Delete, mkdir: `Deleting file: ...` (spaces shown as `\u0001`), `Creating directory: /sd/gcodes/<folder>`.
- Connect: `Opening connection: type=WiFi, address=<ip>`, `Machine connected`, then firmware info: `version = 1.1.2`, `model = Z1, 4, 1, 0, Idle`, `time = ...`, `sys-time-data = ...`, `ftype = nc`, `sn = ...` (the serial number: never paste it). Studio also downloads `/sd/config.txt`; where it keeps it is unknown.
- Drops: `WiFi socket disconnected`, `Machine disconnected`, `Reconnect attempt N/M to <ip>`, `Reconnect succeeded`, `Connection stable for 10s, resetting reconnect counter`, `Heartbeat timeout!`.

## Chinese messages

| Text | Meaning |
|---|---|
| 重连中... | Reconnecting... |
| 连接成功. | Connected. |
| 其他操作已暂停 / 其他操作已恢复 | Other operations paused / resumed (around a file transfer) |
| 文件上传完成 | File upload finished |
| 文件下载完成 | File download finished |
| 文件对比md5完成 | File MD5 comparison finished |
| 耗时: N ms | Took N ms |
| 加载QSS文件 / 创建主窗口 | Loading stylesheet / creating main window (startup) |
| 保存令牌，有效期至 / 令牌已过期 / 清除所有令牌数据 | Saving login token, valid until / token expired / clearing all token data |
| 打开授权URL / 过滤器捕获 URL | Opening the sign-in URL / URL captured (sign-in; don't paste) |
| 数据库设备IP更新成功 / 设备信息插入成功 | Device IP updated in database / device info saved |
| 确认重启 | Confirm restart |
