# Surface to lowest point

Faces uneven stock flat, down to its lowest point, on a Makera Z1. A first job probes a grid on the stock and logs every height. `surface-to-lowest-point.py` then reads those heights from Studio's log and rounds the lowest point's thickness down to 0.5 mm. It writes a second job that faces the top down to that thickness in shallow passes, each cutting only where the probed top still has material above it. That job doesn't probe again, so only the cutter goes in.

It takes two jobs because the Z1 firmware (1.1.2) has no variables, expressions or loops. It reads the number straight after each letter, so `Z[#<depth>]` is read as `Z0` (`Gcode::get_value` in [MakeraZ1Firmware](https://github.com/MakeraInc/MakeraZ1Firmware)). Upstream Smoothieware has none either. A job can't measure the stock and then decide how deep to cut, so the script does that math between the two jobs.

Use [surface-stock](../surface-stock) instead when you already know how deep to cut.

## Requirements

- Makera Z1 or Z1 Pro (firmware 1.1.2), Makera Studio and the 3D probe
- Python 3, no packages, on the computer running Studio (it reads Studio's log)
- A reference for the thickness: a fence or other flat surface a known height above the bed, next to the stock, that the probe can reach. Without one, the thickness at the highest point comes from calipers

## Use

The defaults are at the top of the script. Pass flags for a different stock:

```sh
./surface-to-lowest-point.py --probe --stock-width 65.4 --stock-length 50.4
```

The fence is Makera's L-shaped anchor plate in the front-left corner of the bed (Anchor1); its top is 5 mm above the bed. With the stock pushed into the corner of the L and the origin on that corner, the default fence point, X-7.5 Y77, is on the plate's left arm between a screw hole and the reference dowel, about 27 mm behind the stock, so the probe body stays clear of it on the way down. For another fence or setup, pass `--fence-x`/`--fence-y`: a point on its top in the job's coordinates. To find one, run Studio's corner probe, jog the probe over the fence's top and read X/Y in Studio's work coordinates. Keep it at least 2 mm from the fence's edges and far enough from the stock that the probe body doesn't come down on it.

1. That writes `surface-to-lowest-point-probe.nc`. Upload it to the Z1 from Makera Studio, keeping that name, because step 2 looks for it in the log.
2. Clamp the stock and run Studio's corner probe for X/Y. The jobs are written for the front-left top corner by default (`--origin` to change).
3. Run the probe job with **Auto leveling off**. It asks for the probe (T0) if it isn't fitted, probes the fence's top, then measures each point on the stock. No spindle, no cutting.
4. Run the script again without `--probe`, with the same options:

   ```sh
   ./surface-to-lowest-point.py --stock-width 65.4 --stock-length 50.4
   ```

   It reads the newest complete probe run from Studio's log, and prints every point's height and thickness, measured from the bed (the fence's top minus `--fence-height`). It then writes `surface-to-lowest-point.nc`.

   ```
   Probe run 2026-10-06 18:59:29 (log_2026-10-06-00-00-02.txt), 20 points:
           X       Y   height  thickness
         3.0     3.0    0.000     32.288  highest
        17.9     3.0   -0.520     31.768
         ...
        47.6    47.4   -3.188     29.100  lowest
         ...
   Thicknesses are measured from the fence (its top 5 mm above the bed, probed at X-7.5 Y77).
   Lowest point 29.10 mm, rounded down to 29 mm: cutting 3.288 mm below the highest point.
   ```

   It also prints the Z0 the facing job will cut from (see [Z0](#z0)), and stops instead if anything in the log could have moved Z0 since it was last printed. Run the probe job again then.

5. Upload `surface-to-lowest-point.nc` and start it with **Auto leveling off**, before doing anything else that could move Z0. It prints Z0, asks for the cutter (T1), measures it, and faces down to the final thickness. Before you confirm the tool change, check that Studio's log shows the `G54` Z the script printed; stop the job if it doesn't. If the cutter is already in, there's no tool change: check the log before the first pass.

| Option | Default | Meaning |
|---|---|---|
| `--probe` | | Write the probe job instead of the facing job |
| `--stock-width` / `--stock-length` | 65.4 / 50.4 | Stock size in X / Y (mm). Give the widest size if a side is uneven |
| `--fence-x` / `--fence-y` | -7.5 / 77 | A point on the fence's top, in job coordinates |
| `--no-fence` | | Don't probe the fence; the highest point is taken to be `--stock-height` thick |
| `--fence-height` | 5 | Height of the fence's top above the bed the stock sits on |
| `--stock-height` | 33 | Rough thickness at the highest point. With a fence it only checks the fence reading; without one it sets every thickness |
| `--round-to` | 0.5 | Round the lowest point's thickness down to a multiple of this |
| `--final-height` | from the probe run | Thickness to leave instead of the rounded lowest point |
| `--log` | `~/Library/Application Support/MakeraStudio/logs` | Studio's log folder, or one log file |
| `--tool-dia` | 3.175 | Cutter diameter |
| `--stepover` / `--pass-depth` | 2.0 / 0.2 | Max stepover and depth per pass |
| `--cut-along` | y | Passes run front to back (`y`; the Z1 is stiffer in Y) or left to right (`x`) |
| `--air-margin` | 0.3 | A pass cuts wherever the mapped top is within this of it |
| `--full-passes` | | Every pass covers the whole top, as surface-stock does |
| `--probe-grid-x` / `--probe-grid-y` | 5 / 4 | Probe points |
| `--probe-inset` | 3 | Keep probe points this far inside the edges |
| `--probe-clearance` | 5 | Lift between probe points |
| `--top-margin` | 0.2 | First pass starts this far above the highest point |
| `--rpm` / `--feed` / `--plunge-feed` | 12000 / 500 / 200 | Makera's 6061 values for a 3.175 mm metal end mill |
| `--tool` / `--material` | | Take the cutter, feeds and speeds from a tool in [tool-library](../tool-library) and its preset for that material (default Aluminum); options you pass still win |

## How the heights get out of the machine

While a file plays, the firmware throws away each line's replies, so the probe results (`[PRB:...]`) never reach Studio. `M498` is the exception: it prints the stored work offsets to every connection, including the one Studio logs. The probe job sets Z0 on each point in turn (`G10 L20 P0 Z0`, the way Studio's own Z probe does) and runs `M498` after it. The log gets one line per point:

```
EEPRROM Data: G54: -121.866, -143.602, -69.031
```

The last number is the Z0 offset set on that point, so the differences between lines are the differences in height. The script takes the first line per point after the newest `Playing file: .../surface-to-lowest-point-probe.nc`. If the run stopped before every point, it says so instead of guessing.

With a fence, its line comes first: it is the fence's top, so the bed is `fence-height` below it, and each point's thickness is its line minus the bed. All of it is measured with the same probe tip, so the tip's length cancels out. Without a fence, the highest point is taken to be `--stock-height` thick.

## Passes cut only where there's material

Every pass is flat, at one depth, so the result is parallel to the bottom of the stock. The probe points make a map of the top: straight between neighbouring points, and past the outer ones the higher of the edge value and the slope carried on, so it never guesses low. A pass cuts each row only where the map is within `--air-margin` of it, plus the cutter's radius and 2 mm at both ends, and skips rows with nothing to cut. The last pass covers the whole top, so the face ends up flat everywhere.

On the stock above, with one high corner, that takes about 45 minutes instead of 73. With probe points about 15 mm apart, predicting each one from the points either side of it was off by at most 0.23 mm over two spacings, about 0.06 mm over one; the 0.3 mm margin covers that and the 3 mm strip past the outer points. If the real top is higher than the map by more than the margin, the next pass there takes more than one pass depth.

Each pass is one zig-zag at cutting depth. It goes down beyond the stock's front or back edge, wherever the first row has the least clear stretch before its material, and feeds in sideways. Steps across to the next row happen at an end both rows are clear at, and rows with nothing to cut are crossed there, so the cutter only meets material sideways. Between passes it lifts 0.5 mm above the floor just cut and moves to the next pass's start, beyond the edge.

## Z0

The facing job doesn't probe, and it doesn't set Z0: it cuts from the Z0 in the machine, and the script raises every Z by how far the highest point is above it. That Z0 is where the probe job left it, on its last point (2.13 mm below the highest point in the run above), or the last Z0 printed after the run: a facing job that probed again prints the Z0 it set. A tool change doesn't move Z0; the new tool is measured against the probe. Anything after the last print that could move Z0 without printing it, a G10 Studio sent (its own Z probe, for one) or another job, stops the script.

Setting Z0 in the job with a `G10` would be worse: on the Z1 a `G10` that sets Z also makes whatever tool is in the spindle the reference tool (`Robot::on_gcode_received`), which is only right if the probe is still in. Leaving Z0 alone works whether the probe or the cutter is in when the job starts, and writes nothing to the EEPROM. The job prints Z0 (`M498`) before the tool change so you can check it.

## Notes

- `probe-clearance` must be more than the stock's highest minus lowest thickness. The probe job alarms and stops if a point is more than that below the one before it, or if a point misses the stock. 
- With a fence, the thickness assumes the stock sits flat on the same bed as the fence, with nothing under it. If the fence reading puts the highest point more than 2 mm from `--stock-height`, the script stops: the probe most likely missed the fence and touched the bed, 5 mm lower. Without a fence, every thickness is only as good as `--stock-height`.
- The lowest point between probe points, or in the strip outside them, can be lower than any probed one; the round-down usually covers that.
- Probe points sit `probe-inset` inside the edges of `stock-width`. If a side is uneven, check that the points along it still land on the stock. With 65.4 mm given and the narrowest part at 63.5 mm, the last column is 1.1 mm in from the edge there.
- Cutting along Y, passes run past the front and back edges by the tool radius plus 2 mm, and past the left and right edges by the tool radius, down to the final thickness (the other way round with `--cut-along x`). Clamps there must sit below it; Makera's anchor plate is 5 mm high.
- Each probe point stores Z0 (G54) the way Studio's own Z probe does, which is one write to the machine's EEPROM: 21 for the probe job with the fence. The facing job writes none. Never use `M498.2`; it erases that data. The jobs never move the A axis or use G92.

The probe job ran on a Z1 Pro (firmware 1.1.2) on 2026-10-06, without the fence and with it: no alarms, the stock points repeating within 0.006 mm between the runs, and the script read both back from Studio's log (the output above). An earlier facing job that probed the grid again put Z0 within 0.003 mm of what the probe run measured, and cut two full-width passes. The facing job as it is now, without probing and cutting only where there's material, hasn't run yet; the tests check its passes against the probed top. Watch the first passes.
