# Tool library

Cutting tools the macros can use by name. Run a facing macro with `--tool` and it takes the cutter's diameter and its feeds and speeds from here instead of its defaults:

```sh
./surface-to-lowest-point/surface-to-lowest-point.py --tool 'WEXWE 1/4"' --material Aluminum
```

Tools are Fusion tool library files, the same files Fusion imports, so one definition serves both CAM and the macros. They come from, in this order:

1. **`custom/`**: our own tools, one Fusion library file (`.json`) each. [amazon-tool](../amazon-tool) writes here.
2. **Makera's tool files**, from [MakeraInc/CarveraProfiles](https://github.com/MakeraInc/CarveraProfiles/tree/main/CAM_Post_Processors/Fusion360-profiles/Tool%20Files) at the commit in `makera.json`. They're downloaded into `cache/` the first time a script needs them. Makera's repo has no license, so the files aren't copied into this one.
3. **Fusion's local tool libraries** on this computer (`~/Library/Application Support/Autodesk/CAM360/libraries/Local` on macOS), if Fusion is installed.

When two tools have the same description, the first one found wins, so a tool in `custom/` replaces a Makera tool of the same name.

## Use

```sh
./toollib.py list            # every tool: description, type, diameter, flutes, library, materials
./toollib.py list 1/4        # descriptions containing "1/4"
./toollib.py show 'WEXWE 1/4"'   # one tool's sizes and its feeds and speeds per material
./toollib.py fetch           # download Makera's files again
```

A name matches a tool's description or product id exactly, or any part of its description; it has to pick out one tool.

In surface-stock, face-to-thickness and surface-to-lowest-point:

| Option | Meaning |
|---|---|
| `--tool NAME` | Take `tool_dia`, `rpm`, `feed`, `plunge_feed`, `pass_depth` (the preset's stepdown) and `stepover` from this tool |
| `--material NAME` | Which of the tool's presets to use (default `Aluminum`). Fusion's are named by material: Aluminum, Brass, Copper, Hardwood, Softwood, Plastic, Carbon Fiber, PCB |

Options you pass on the command line still win: `--tool 'WEXWE 1/4"' --feed 600` uses the tool's other settings and a 600 mm/min feed. Fusion presets carry no facing stepover, so `stepover` is set to 63% of the diameter, Makera's 2.0 mm on a 3.175 mm cutter. The macros only load this library with `--tool`; without it they run on their own.

In another script:

```python
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tool-library"))
import toollib

tool = toollib.find('WEXWE 1/4"')          # .dia, .shank, .flutes, .flute_length, .overall_length, .type
cut = tool.preset("Aluminum")              # .rpm, .feed, .plunge_feed (mm/min), .stepdown (mm)
toollib.apply(v, 'WEXWE 1/4"', "Aluminum")   # fills a macro's settings dict, as --tool does
```

## Adding a tool

- From an Amazon listing: `../amazon-tool/amazon-tool.py <link>` writes it to `custom/`. Read what it prints: sellers' listings are often wrong, and its presets start as a copy of a similar tool's.
- From Fusion: export the library (Tool Library, right-click, Export) and put the file in `custom/`, or leave it in Fusion: local libraries are read too.
- Or edit a file in `custom/` by hand: it's Fusion's JSON. A preset's `n` is rpm, `v_f` the feed and `v_f_plunge` the plunge feed in mm/min, `stepdown` the depth per pass in mm.

Check a tool's presets before cutting with it. The ones amazon-tool copies are for the tool it copied from, and Makera's are Makera's choices for their own tools. The macros refuse feeds over 1200 mm/min and speeds over 13000 rpm, the Z1's limits in Studio.

Only metric tools are read; Fusion libraries in inches are skipped. `Z1_TOOLS` (paths separated by `:` on macOS and Linux, `;` on Windows) replaces the three places above, which the tests use.
