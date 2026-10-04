# Surface stock

Faces the top of uneven stock flat on a Makera Z1. `surface-stock.py` writes a G-code job for Makera Studio that probes a grid with the 3D probe, sets Z0 on the highest point it finds, then faces down in shallow passes.

The Z1 firmware (1.1.2) has no G-code variables, expressions or loops, so a parametric macro can't run on the machine. The script does the math and writes plain G-code instead.

## Requirements

- Makera Z1 or Z1 Pro (firmware 1.1.2), Makera Studio and the 3D probe
- Python 3, no packages

## Use

```sh
./surface-stock.py --stock-width 69 --stock-length 50.1 --stock-height 19.9 --target-z -3
```

This writes `surface-stock.nc`. Run `./surface-stock.py --help` for every option; the defaults are at the top of the script.

In Makera Studio:

1. Upload `surface-stock.nc`.
2. Clamp the stock and run the corner probe for X/Y. The job is written for the back-right top corner by default (`--origin` to change).
3. Start the job with **Auto leveling off**. The job asks for the probe (T0) if it isn't fitted, probes the grid, asks for the cutter (T1), measures it, and faces.

| Option | Default | Meaning |
|---|---|---|
| `--stock-width` / `--stock-length` | 69 / 50.1 | Stock size in X / Y (mm) |
| `--stock-height` | 19.9 | Thickness at the highest spot (Studio's stock preview) |
| `--target-z` | -3.0 | Depth to remove below the highest point |
| `--tool-dia` | 3.175 | Cutter diameter |
| `--stepover` / `--pass-depth` | 2.0 / 0.2 | Max stepover and depth per pass |
| `--probe-grid-x` / `--probe-grid-y` | 5 / 4 | Probe points; `0` skips probing (set Z0 on the high spot yourself) |
| `--probe-clearance` | 5.0 | Lift between probe points |
| `--top-margin` | 0.2 | First pass starts this far above Z0 |
| `--rpm` / `--feed` / `--plunge-feed` | 12000 / 500 / 200 | Makera's 6061 values for a 3.175 mm metal end mill |

## How Z0 is found

The first point is probed the way Studio does it and becomes Z0. At each later point the probe lifts `probe-clearance` above Z0 and probes down exactly that far with `G38.3`, so it stops on the surface if the surface is higher than Z0 and at Z0 if not. `G10 L20 P0 Z0` then sets Z0 at that height, so Z0 only ever moves up and ends on the highest point.

## Notes

- Auto leveling must be off: it bends the toolpath to follow the uneven top. The job also clears any leftover leveling grid (`M370`).
- `probe-clearance` must be more than the stock's highest minus lowest thickness. If it isn't, the probe bumps the stock between points and the firmware halts the machine.
- Probe points sit 3 mm inside the edges. A high spot narrower than the grid spacing can be missed; `top-margin` covers small misses at the edges.
- The cut runs past the X edges by the tool radius plus 2 mm and past the Y edges by the tool radius, so clamps there must sit below the finished surface.
- Each probe point stores Z0 (G54) the way Studio's own Z probe does, which is one write to the machine's EEPROM. The job never moves the A axis or uses G92, so 4th-axis and "set current position as origin" setups are left alone.
- `./surface-stock.py --probe-only` writes `surface-stock-probe-test.nc`, which probes without cutting. It prints the stored Z0 after each point with `M498`, so the heights show up in Studio's log (`~/Library/Application Support/MakeraStudio/logs` on macOS).

The probing has been checked on a Z1 Pro running firmware 1.1.2. Watch the first passes of any new setup.
