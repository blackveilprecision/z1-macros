# Probe stock

Probes the stock once for every macro that cuts from it, on a Makera Z1. `probe-stock.py job` writes the probe job, `save` reads what it measured from Studio's log into `stock.json`, and macros such as [surface-to-lowest-point](../surface-to-lowest-point) cut from that. Each macro notes what its job will do to the stock. Once Studio's log shows the job finished, the next macro starts from the stock as it is, without probing again.

It's a separate job because the Z1 firmware (1.1.2) has no variables: `Z[#<depth>]` is read as `Z0`. A job can't measure and then decide where to cut, so the scripts do the math between jobs.

## Requirements

- Makera Z1 or Z1 Pro (firmware 1.1.2), Makera Studio, Python 3 on the same computer (it reads Studio's log)
- The probe (T0) for the top and the anchor plate
- The probe rod for the sides and corner, on metal stock only (it finds the stock by contact)

## Use

```sh
./probe-stock.py job --corner --side-x-points 5 --side-y-points 3
```

1. Upload `probe-stock.nc` to the Z1, keeping its name.
2. Run it with **Auto leveling off**. No spindle, no cutting. With side or corner touches it asks for the rod first, then the probe; without them, only the probe.
3. `./probe-stock.py save` reads the run from the log, prints every point and writes `stock.json`.
4. Run the macros.

`./probe-stock.py show` prints the stock as it is now. `./probe-stock.py where` writes `probe-stock-where.nc`, which only prints X0/Y0/Z0 to the log. Run it when a macro says they may have moved.

| Option (`job`) | Default | Meaning |
|---|---|---|
| `--stock-width` / `--stock-length` | 65.4 / 50.4 | Stock size in X / Y; the widest, if a side is uneven |
| `--stock-height` | 29 | Rough thickness: checks the anchor plate reading (or, with `--no-fence`, is the thickness at the highest point) |
| `--origin` | topFrontLeft | Studio's origin corner |
| `--fence-x` / `--fence-y` / `--fence-height` | -7.5 / 77 / 5 | A point on the anchor plate's top, and its height above the bed |
| `--no-fence` | | Don't touch the anchor plate |
| `--probe-grid-x` / `--probe-grid-y` | 5 / 4 | Points on the top |
| `--probe-inset` | 3 | Points this far in from the edges |
| `--probe-clearance` | 5 | Lift between top points; more than the top's highest minus lowest point |
| `--corner` | | Touch the origin corner's two sides with the rod and set X0/Y0, as Studio's corner probe does |
| `--side-x-points` / `--side-y-points` | 0 / 0 | Points on the side across from the origin: the right side and the back for a front-left origin |
| `--side-depth` | 2 | Rod tip this far below the top for those |
| `--side-clearance` | 5 | How far outside a side across from the origin the rod comes down; it searches twice that |
| `--rod-dia` | 2 | The rod's diameter where it touches |
| `--rod-tool` | 9999 | The rod's tool number |
| `--clamp-height` | 10 | Tallest clamp beside the sides; the rod tip stays 1 mm above it |

## What it touches

**With the rod (T9999), if there are side or corner touches.** The rod is measured on the tool setter, then touches the top beside the origin corner to set Z0, as Studio's corner probe does. For each side of the corner it goes out 10 mm, down 2 mm, and feeds in: 100 mm/min to the touch, back 1 mm, again at 50. The sides across from the origin are touched at `--side-x-points`/`--side-y-points` points, `--side-depth` down.

- The rod is straight, so it touches a side wherever the side sticks out most between its tip and the top. That's the worst case over the band a cut to that depth removes. It has to stick out of the collet further than `--side-depth`.
- A touch only reaches the log by setting X0 or Y0 there. A touch across from the origin sets it as if the stock were exactly `--stock-width` (or `--stock-length`), so how far X0 moves is how far the side is off that. The origin's own side is touched afterwards to put X0/Y0 back on the corner.
- The rod needs a tool number other than the probe's T0: the firmware ignores a tool change to the tool already in, so the job couldn't ask for the swap. T9999 is the firmware's third special tool, after the probe (T0) and the laser (T8888). Studio sets a tool other than T0 for its own rod corner probe, but its logs don't say which, so check the name at the first tool change prompt. Pass `--rod-tool` if yours is another number.

**With the probe (T0).** It touches the anchor plate, then the top grid, each fast to the touch, back 1 mm, then slow at 100 mm/min, as Studio's Z probe does. The firmware stops a few milliseconds after the touch, so a faster touch reads lower. Z0 ends on the last point. The anchor plate is Makera's L-shaped plate in the front-left corner, 5 mm above the bed. X-7.5 Y77 is on its left arm, about 27 mm behind the stock. If the plate puts the highest point more than 2 mm from `--stock-height`, `save` stops: the probe probably missed the plate and touched the bed.

G38.2 alarms and stops the job if a touch finds nothing.

Before a file plays, Studio moves to the lowest X/Y in its preview, and its preview reads a `G38.2 X-20` as a position, not a distance. A job with 20 mm searches was sent to Y-20 and stopped at the soft limit. So every search is written after `G91`, which the firmware ignores for G38.2, and the searches away from the corner are kept to 10 mm. The furthest the job goes is the corner touches, 11 mm out, which is inside where Studio's own corner probe went on this machine.

## How the numbers are kept

While a file plays, the firmware throws away probe results. `M498` still prints the stored G54 and REFMZ, the reference tool's tool setter reading, to Studio's log. So every touch sets X0, Y0 or Z0 and runs `M498`. Heights are stored as G54 Z minus REFMZ: height above the tool setter, which every tool is measured against. So the rod's and the probe's readings line up, and so do later cutters and a Z0 set somewhere else. `save` prints how far apart the rod and the probe read the top point they both touch.

`save` matches the prints to the touches by the job's MD5, which Studio logs on upload. The same goes for the macros' jobs: a job counts once the log shows that exact file reaching its final `G28` without an abort or alarm. A job stopped partway doesn't count; running it again from the old numbers only cuts air where it had already cut.

The macros don't probe; they cut from wherever X0/Y0/Z0 were last printed. They stop if, since then:

- Studio sent a `G10` (its own probes) or `G92`
- a job not written here played
- `probe-stock.nc` ran again without `save`
- X0/Y0 moved more than 0.1 mm from where the probe run left them: the stock moved, so probe it again

A moved Z0 is fine once printed: run `probe-stock-where.nc`.

`stock.json` and `jobs.json` stay in this folder, out of git. `Z1_STOCK` (a folder) replaces it for the tests.

## Notes

- Each touch is one EEPROM write, as with Studio's own probes. Never use `M498.2`; it erases that data.
- With an uneven side, check the top points along it still land on the stock.
- The thickness assumes the stock sits flat on the same bed as the anchor plate.
- The probe's anchor plate and top touches match surface-to-lowest-point's earlier probe job, which ran on a Z1 Pro on 2026-10-06. The rod touches copy Studio's corner probe sequence. They, the T9999 tool change and `save` haven't run from a file yet; the tests check the G-code against made-up logs. Watch the first run.
