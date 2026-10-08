# Face to thickness

Faces flat stock down to a set thickness on a Makera Z1. `face-to-thickness.py` writes a G-code job for Makera Studio that probes the top once with the 3D probe, sets Z0 there, then faces the whole top down in equal passes until the stock is the thickness you asked for.

The top has to be flat already. If it isn't, run [surface-stock](../surface-stock) first.

## Requirements

- Makera Z1 or Z1 Pro (firmware 1.1.2), Makera Studio and the 3D probe
- Python 3, no packages
- Run it from a copy of this whole repo: it imports [../shared](../shared)
- Calipers or a micrometer: the depth to cut is worked out from the thickness you measure

## Use

```sh
./face-to-thickness.py --stock-width 69 --stock-length 50.1 --stock-height 17 --final-height 10
```

This writes `face-to-thickness.nc`. Run `./face-to-thickness.py --help` for every option and its default; change the defaults at the top of the script.

In Makera Studio:

1. Upload `face-to-thickness.nc`.
2. Clamp the stock and run the corner probe for X/Y. The job is written for the back-right top corner by default (`--origin` to change).
3. Start the job with **Auto leveling off**. The job asks for the probe (T0) if it isn't fitted, probes the middle of the top, asks for the cutter (T1), measures it, and faces.

| Option | Default | Meaning |
|---|---|---|
| `--stock-width` / `--stock-length` | 69 / 50.4 | Stock size in X / Y (mm) |
| `--stock-height` | 17 | Thickness now, measured |
| `--final-height` | 10 | Thickness to leave |
| `--tool-dia` | 3.175 | Cutter diameter |
| `--stepover` / `--pass-depth` | 2.0 / 0.2 | Max stepover and max depth per pass |
| `--rpm` / `--feed` / `--plunge-feed` | 12000 / 500 / 200 | Makera's 6061 values for a 3.175 mm metal end mill |
| `--tool` / `--material` | | Take the cutter, feeds and speeds from a tool in [tool-library](../tool-library) and its preset for that material (default Aluminum); options you pass still win |
| `--no-probe` | off | Skip probing; set Z0 on the top in Studio yourself |
| `--origin` | topBackRight | Studio origin corner the job is written for: `topFrontLeft`, `topFrontRight`, `topBackLeft`, `topBackRight` or `topCenter` |
| `-o` / `--out` | `face-to-thickness.nc` here | Where to write the job |

## How the depth is set

Z0 is probed in the middle of the top, the same way Studio's own Z probe does it. The job then cuts `stock-height` minus `final-height` below Z0, so the result is only as accurate as the thickness you enter. Measure it in a few places.

The depth is split into equal passes no deeper than `pass-depth`. 7 mm at 0.2 mm is 35 passes of exactly 0.2 mm; 6.9 mm would be 35 passes of 0.197 mm, not 34 full passes and a 0.1 mm sliver that rubs instead of cutting.

To hit the thickness closely, leave a little on the first run (`--final-height 10.2`). Then measure, and run again with the measured thickness and `--final-height 10`. The second run is one light pass.

To take the same amount off both faces, run to the halfway thickness (`--final-height 13.5`). Flip the stock so the faced side sits down, then run again with `--stock-height 13.5 --final-height 10`.

## Notes

- Clamps and vise jaws must sit below the final thickness. The cutter reaches its diameter plus 2 mm past the X edges and its radius past the Y edges (5.2 mm and 1.6 mm with a 3.175 mm cutter), all the way down to the final surface. The job and the generator print both.
- Auto leveling must be off: it bends the toolpath. The job also clears any leftover leveling grid (`M370`).
- Taking a lot off with a 3.175 mm cutter is slow: 17 mm to 10 mm on 69 x 50.1 mm stock is about 2.5 hours. The generator prints its time estimate.
- Probing stores Z0 (G54) the way Studio's own Z probe does, which is one write to the machine's EEPROM. The job never moves the A axis or uses G92, so 4th-axis and "set current position as origin" setups are left alone.

The probe sequence and the cutting moves are the same as surface-stock's (the cutting body is identical to `surface-stock.py --probe-grid-x 0 --top-margin 0` with the same depth: both split it into equal passes). This macro itself has not been run on a machine yet. Watch the first passes.
