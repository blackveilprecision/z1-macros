# Square side

Cuts the uneven side of the stock straight on a Makera Z1, half its height at a time. It works from [probe-stock](../probe-stock)'s reference: the side across from the origin in X (the right side for a front-left origin), touched with the probe rod.

It cuts that side to the narrowest width probed, rounded down to 0.5 mm, from the top down to half the thickness plus 0.5 mm:

- **Roughing:** layers from the top at the tool's stepdown, each one pass along the side. Wider steps are split into several passes. Roughing leaves 0.2 mm on the wall.
- **Finishing:** one pass the full depth down, climbing along the side.
- **Every pass starts and ends beyond the front or back of the stock,** so the cutter only goes down where there's nothing under it.

Then flip the stock front to back, probe it again and run this again with `--final-width` set to the width the first run printed, so the other half comes out the same. The two overlap by 1 mm.

## Requirements

- Makera Z1 or Z1 Pro (firmware 1.1.2), Makera Studio, Python 3 on the same computer (it reads Studio's log)
- Run it from a copy of this whole repo: it imports [../shared](../shared) and [stockref](../probe-stock)
- A probe-stock run with the side touched with the rod (2 mm down, like Studio's corner probe):

  ```sh
  ../probe-stock/probe-stock.py job --corner --side-x-points 5
  ```

- A cutter with flutes longer than the cut, sticking out of the collet more than the cut plus 5 mm. The defaults are for the WEXWE 1/4" 3 flute (25.4 mm flutes) in [tool-library](../tool-library).

## Use

```sh
./square-side.py --tool 'WEXWE 1/4"'
```

1. It prints the side's width at each probed point, how much it will take off, and the depth. Then it writes `square-side.nc`.
2. Upload it and start it with **Auto leveling off**. It prints Z0, asks for the cutter (T1), and cuts. Before you confirm the tool change, check that Studio's log shows the `G54` Z the script printed.
3. Flip the stock front to back (top face down), so the square left side stays against the anchor plate. Push it into the corner and clamp it.
4. Probe it again (step 1 of probe-stock) and save, then run `./square-side.py --final-width <the width the first run printed>` for the other half. Without it the width comes from the new probe run, which reads the uncut half and can round to a different width.

| Option | Default | Meaning |
|---|---|---|
| `--final-width` | the narrowest probed, rounded down | Width to leave, from the origin's side |
| `--round-to` | 0.5 | Round the narrowest width down to a multiple of this |
| `--depth` | half the thickness + half the overlap | How far down the side to cut |
| `--overlap` | 1 | How much the cuts before and after the flip overlap |
| `--tool` / `--material` | | Take the cutter, its flute length, feeds and speeds from [tool-library](../tool-library) |
| `--tool-dia` / `--flute-length` | 6.35 / 25.4 | Cutter diameter and flute length |
| `--pass-depth` | 0.2 | Depth of each roughing layer |
| `--stepover` | 4 | Most material one roughing pass takes across |
| `--finish-allowance` | 0.2 | What roughing leaves for the finishing pass |
| `--rpm` / `--feed` / `--plunge-feed` | 12000 / 500 / 200 | |
| `--clamp-height` | 10 | Tallest clamp beside the side; the cutter stays 2 mm above it (and above the 5 mm anchor plate) |
| `--log` | Studio's macOS log folder | Studio's log folder or one `log_*.txt`; pass it on Windows or Linux |
| `-o` / `--out` | `square-side.nc` here | Where to write the job |

## Notes

- The probe rod is straight, so each reading is the widest the side gets from the rod's tip up to the top. Below the tip the side is assumed no wider than that. Each roughing layer is only 0.2 mm deep, so the cutter takes whatever is there either way. Spots narrower than the final width somewhere down the side are left as they are.
- The cut is placed from X0, which the corner touches put on the left side. The two halves line up if the left side is square to the top and bottom.
- Once Studio's log shows the job finished, probe-stock counts the side as cut, and running this again on the same half says there's nothing left to cut.
- It sets no Z0 or work offset (no `G10`), so G54 isn't written; the tool change (`T1 M6`) stores the tool number and its measured length, as any tool change does. It never uses `M498.2` or G92.
- It ran to the end twice on a Z1 Pro (firmware 1.1.2) on 2026-10-07, on the first half of the right side; the second run cut it to 63.4 mm, 15.01 mm down. The second half after the flip hasn't run on a machine yet, nor has a left side (right origin). The tests check the G-code against made-up logs. Watch the first layers and the finishing pass.
