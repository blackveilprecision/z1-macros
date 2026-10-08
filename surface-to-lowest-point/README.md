# Surface to lowest point

Faces uneven stock flat, down to its lowest point, on a Makera Z1. [probe-stock](../probe-stock) probes the top and saves the heights. `surface-to-lowest-point.py` reads them, rounds the lowest point's thickness down to 0.5 mm, and writes a job that faces the top down to that thickness in shallow passes, each cutting only where the probed top still has material above it. The job doesn't probe, so only the cutter goes in.

Use [surface-stock](../surface-stock) instead when you already know how deep to cut.

## Requirements

- Makera Z1 or Z1 Pro (firmware 1.1.2), Makera Studio and the probe
- Python 3, no packages, on the computer running Studio (it reads Studio's log)
- A probe run saved by [probe-stock](../probe-stock), ideally with the anchor plate touched, so the thickness is measured instead of taken from calipers

## Use

1. Probe the stock and save it ([probe-stock](../probe-stock#use)):

   ```sh
   ../probe-stock/probe-stock.py job --stock-width 65.4 --stock-length 50.4 --stock-height 33
   # upload probe-stock.nc, run it with Auto leveling off, then:
   ../probe-stock/probe-stock.py save
   ```

2. Write the facing job:

   ```sh
   ./surface-to-lowest-point.py
   ```

   It prints every point's height and thickness and the thickness it will leave, then writes `surface-to-lowest-point.nc`. From the probe run of 2026-10-06 on a Z1 Pro (with an earlier version of the script, which read the same numbers):

   ```
   Probe run 2026-10-06 18:59:29 (log_2026-10-06-00-00-02.txt), 20 points:
           X       Y   height  thickness
         3.0     3.0    0.000     32.288  highest
        17.9     3.0   -0.520     31.768
         ...
        47.6    47.4   -3.188     29.100  lowest
         ...
   Lowest point 29.10 mm, rounded down to 29 mm: cutting 3.288 mm below the highest point.
   ```

   It also prints the Z0 the job will cut from (see [Z0](#z0)), and stops instead if anything in the log could have moved Z0 since it was last printed.

3. Upload `surface-to-lowest-point.nc` and start it with **Auto leveling off**, before doing anything else that could move Z0. It prints Z0, asks for the cutter (T1), measures it, and faces down to the final thickness. Before you confirm the tool change, check that Studio's log shows the `G54` Z the script printed; stop the job if it doesn't. If the cutter is already in, there's no tool change: check the log before the first pass.

Once Studio's log shows the job finished, probe-stock counts the top as faced at that thickness. Running the script again then says there's nothing left to cut, unless you pass a lower `--final-height`.

| Option | Default | Meaning |
|---|---|---|
| `--round-to` | 0.5 | Round the lowest point's thickness down to a multiple of this |
| `--final-height` | from the probe run | Thickness to leave instead of the rounded lowest point |
| `--log` | `~/Library/Application Support/MakeraStudio/logs` | Studio's log folder, or one log file |
| `--tool-dia` | 3.175 | Cutter diameter |
| `--stepover` / `--pass-depth` | 2.0 / 0.2 | Max stepover and depth per pass |
| `--finish-depth` | 0.1 | The last pass is a lighter finishing pass this deep over the whole top; 0 for none |
| `--finish-stepover` | half the cutter | Stepover of the finishing pass |
| `--cut-along` | y | Passes run front to back (`y`; the Z1 is stiffer in Y) or left to right (`x`) |
| `--air-margin` | 0.3 | A pass cuts wherever the mapped top is within this of it |
| `--full-passes` | | Every pass covers the whole top, as surface-stock does |
| `--top-margin` | 0.2 | First pass starts this far above the highest point |
| `--rpm` / `--feed` / `--plunge-feed` | 12000 / 500 / 200 | Makera's 6061 values for a 3.175 mm metal end mill |
| `--tool` / `--material` | | Take the cutter, feeds and speeds from a tool in [tool-library](../tool-library) and its preset for that material (default Aluminum); options you pass still win |

The stock's size, origin corner and thickness come from the probe run.

## Passes cut only where there's material

Every pass is flat, at one depth, so the result is parallel to the bottom of the stock. The probe points make a map of the top: straight between neighbouring points, and past the outer ones the higher of the edge value and the slope carried on, so it never guesses low. A pass cuts each row only where the map is within `--air-margin` of it, plus the cutter's radius and 2 mm at both ends, and skips rows with nothing to cut. The last roughing pass stops `--finish-depth` (0.1 mm) above the final thickness and covers the whole top. Then a finishing pass takes that 0.1 mm off everywhere, on rows half the cutter apart, so the final surface comes from a light, even cut.

On the stock above, with one high corner, that takes about 45 minutes instead of 73. With probe points about 15 mm apart, predicting each one from the points either side of it was off by at most 0.23 mm over two spacings, about 0.06 mm over one; the 0.3 mm margin covers that and the 3 mm strip past the outer points. If the real top is higher than the map by more than the margin, the next pass there takes more than one pass depth.

Each pass is one zig-zag at cutting depth. It goes down beyond the stock's front or back edge, wherever the first row has the least clear stretch before its material, and feeds in sideways. Steps across to the next row happen at an end both rows are clear at, and rows with nothing to cut are crossed there, so the cutter only meets material sideways. Between passes it lifts 0.5 mm above the floor just cut and moves to the next pass's start, beyond the edge.

## Z0

The facing job doesn't probe, and it doesn't set Z0: it cuts from the Z0 in the machine, and the script raises every Z by how far the highest point is above it. probe-stock keeps every height above the tool setter, so the script only needs to know where Z0 is now: the last `M498` in Studio's log shows it. After the probe job that is its last top point. A tool change doesn't move Z0; the new tool is measured against the probe. Anything after the last print that could move Z0 without printing it, a G10 Studio sent (its own Z probe, for one) or a job not written here, stops the script; [probe-stock's where job](../probe-stock#how-the-numbers-are-kept) prints it again.

Setting Z0 in the job with a `G10` would be worse: on the Z1 a `G10` that sets Z also makes whatever tool is in the spindle the reference tool (`Robot::on_gcode_received`), which is only right if the probe is still in. Leaving Z0 alone works whether the probe or the cutter is in when the job starts, and writes nothing to the EEPROM. The job prints Z0 (`M498`) before the tool change so you can check it.

## Notes

- The lowest point between probe points, or in the strip outside them, can be lower than any probed one; the round-down usually covers that.
- Cutting along Y, passes run past the front and back edges by the tool radius plus 2 mm, and past the left and right edges by the tool radius, down to the final thickness (the other way round with `--cut-along x`). Clamps there must sit below it; Makera's anchor plate is 5 mm high.
- The job writes nothing to the machine's EEPROM. Never use `M498.2`; it erases that data. The job never moves the A axis or uses G92.

On 2026-10-06 a Z1 Pro (firmware 1.1.2) ran this facing job, from a second probe run of the same stock at 19:32 and with `--air-margin 0.4`: 16 passes along Y in 47.5 minutes, with no alarms, and calipers then read 29.0 mm across the whole top. That version of the script read the probe run from Studio's log itself; reading it through probe-stock is new and hasn't run on a machine yet. The passes are the same.
