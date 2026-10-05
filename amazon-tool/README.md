# Amazon tool

Turns an Amazon listing for a cutter into a Fusion tool library file. `amazon-tool.py` reads the tool's type and sizes from the listing. It copies the holder, presets and post settings from the closest tool of the same type in your Fusion libraries, and writes a one-tool `.json` library to `tools/` for Fusion to import.

Supported types: flat end mills, ball end mills, chamfer mills (chamfer and V-bits, engraving bits) and drills.

## Requirements

- Python 3.9 or later (the `python3` macOS ships works), no packages
- Fusion, with at least one tool of each type you want to add in a local library. Makera's libraries cover all four, with the Carvera holder the Z1 uses.

## Use

```sh
./amazon-tool.py 'https://www.amazon.com/dp/B0D4VP4R2C'
```

This writes `tools/WEXWE 1-8in 4 Flute End Mill (MAH Coated).json`, named after the tool (`1/8"` becomes `1-8in` in file names), and prints every value with where it was read. In Fusion's Tool Library, import the file; it comes in as a library named after the file. Drag the tool into your own library, then delete the imported one.

```
WEXWE 1/8" 4 Flute End Mill (MAH Coated)
flat end mill (no ball, chamfer or drill words in the title), https://www.amazon.com/dp/B0D4VP4R2C

  diameter           3.175 mm  size "1/8-2" 4PCS" read as diameter-overall length
  shank              3.175 mm  title "1/8 Shank"
  flute length        12.7 mm  spec "cutting length: 0.5 inches"
  overall length      50.8 mm  size "1/8-2" 4PCS" read as diameter-overall length
  flutes                    4  title "4 Flute"
  ...
Check:
  - feed rates (mm/min) are copied from a 1-flute tool, so with 4 flutes each tooth takes 1/4 of the chip: check them for this tool
  - the listing says non-center cutting: ramp or helix into the cut, don't plunge
```

Read the output before importing. Sellers' listings are often incomplete or wrong, so override anything with a flag (all sizes in mm):

| Option | Meaning |
|---|---|
| `--type flat\|ball\|chamfer\|drill` | Tool type, if the title's words don't say it |
| `--dia` / `--shank-dia` | Cutting and shank diameter. For a chamfer mill, `--dia` is the widest part of the cone |
| `--flute-length` / `--overall-length` / `--shoulder-length` | Lengths. The shoulder defaults to the flute length |
| `--flutes` | Number of flutes |
| `--angle` | Chamfer mill: included angle (90 for a bit that cuts a 45° chamfer). Drill: point angle |
| `--tip-dia` | Chamfer mill: flat at the tip, 0 for pointed |
| `--vendor` / `--product-id` / `--description` | Text fields |
| `--template TEXT` | Copy from the tool whose description contains TEXT |
| `--library FILE` | Also search another library (`.json`, or an exported `.tools`) for a template (repeatable) |
| `--number` | Tool number (default 1) |
| `--html PAGE` | Read a listing saved from your browser, if Amazon blocks the script |
| `-o` | Output file instead of `tools/<description>.json` |

## How values are read

Labelled values come first: "1/8 Inch Shank", "Cutting Length: 15mm", "38mm total length", "3mm Drill Bit". The selected size comes next, such as "3.175\*3.175\*15mm" (shank\*diameter\*flute length), "1/8-2"" (diameter-overall length) or "30°\*0.2mm" (angle\*tip). Amazon's spec table comes last, because it rounds: 1/8" shows as "0.13 inches", which the script reads back as 1/8". Values that disagree with the one used are listed under **Check**.

A flute length within 8 mm of the overall length is ignored. Sellers often put the overall length in the "Cutting Length" field.

When a listing leaves something out, the usual value for the type is used and the output says so:

- **Chamfer mill:** diameter = shank, tip = 0 (pointed), flute length = the length of the cone
- **Drill:** shank = diameter, 2 flutes, 118° point
- **Every type:** shoulder length = flute length

Anything else missing stops the script with the flag to pass. Corner-radius, thread, dovetail, countersink and other types stop it too, unless you pass `--type`.

## What is copied from the template

The template is the tool of the same type in your libraries (Fusion's local libraries, plus any `--library`). It is the one with the closest angle (chamfer mills), then diameter, then shank, then the most presets.

- **Holder and post settings:** copied as they are. The tool number is set to 1 (or `--number`), and the length and diameter offsets follow it.
- **Body length:** from the template's formula. Makera's is `max(shoulder, overall - holder gauge - 12.5)`, the stick-out in the Carvera holder.
- **Presets:** each keeps its rpm and mm/min feeds. Stepovers written against the diameter (`tool_diameter/7`) are worked out again, as are feed per tooth and surface speed. A different flute count or diameter changes the chip load, and the output says so. Check the presets for the new tool.
- **Neck profile:** dropped. Fusion then runs the shank down to the shoulder, which is the cautious choice for collision checks.

1/4" (6.35 mm) shanks need nothing special. Makera's 6 mm-shank tools use the same Carvera holder and body-length formula as the 1/8" ones.

## Notes

- The script makes one request to Amazon per run. If Amazon answers with a robot check, save the page from your browser (File > Save Page As) and pass `--html`.
- It reads Fusion's local libraries from `~/Library/Application Support/Autodesk/CAM360/libraries/Local` (macOS) or `%APPDATA%\Autodesk\CAM360\libraries\Local` (Windows) and never writes to them.

Checked against live listings for a flat end mill (WEXWE B0D4VP4R2C), the HOZLY B09ZTXD8R6 it reproduces from your library, and two 1/4"-shank 90° chamfer bits. Ball end mill, drill and V-bit listings were only checked with mock pages. The files have not been imported into Fusion yet.
