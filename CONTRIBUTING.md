# Contributing

Issues and pull requests are welcome.

## Reporting a problem

Open an issue with:

- Machine (Z1 or Z1 Pro), firmware version and Makera Studio version
- The command you ran, or the options you changed
- What happened, and what you expected

Studio's log usually shows what the machine did. On macOS it is in `~/Library/Application Support/MakeraStudio/logs`. Paste the lines from `Playing file:` onward.

## Adding or changing a macro

- One folder per macro, with a `README.md` covering what it does, requirements, how to run it from Studio, and its limits.
- Generators are Python 3 using only the standard library. Don't commit generated `.nc` files; add them to the folder's `.gitignore`.
- Keep the root `README.md` table up to date.

## Writing G-code for the Z1

The Z1 firmware (1.1.2) is a Smoothieware fork. Things that are easy to get wrong:

- No `#` variables, `[expressions]` or O-word loops. They aren't rejected: `Z[#<depth>]` is read as `Z0`. Do the math in the generator.
- `G38.x` distances are relative to the current position. `G38.2` alarms if nothing is touched; `G38.3` doesn't.
- The 3D probe is tool `T0`. `M6 T<n>` prompts for a manual tool change and measures the new tool.
- Only G54 is saved to the machine's EEPROM; every `G10` that sets it is one write. G55–G59 and G92 live in RAM.
- Leave G92 alone. Studio's "set current position as origin" and its 4th-axis toolpaths keep X/Y/Z/A origins there, so `G92.1` would wipe them.
- While a file plays, the firmware discards each line's output, including probe results. `M498` prints the stored G54 offset to Studio's log and works from a file.
- An auto-leveling grid stays active until `M370`.

## Testing

Say in the pull request what you ran on a machine (model and firmware) and what you only checked another way. For anything that moves near the stock, run it first with the cutter well above the work or with the spindle off, and watch the first run of every change.

## License

Contributions are accepted under the [MIT License](LICENSE).
