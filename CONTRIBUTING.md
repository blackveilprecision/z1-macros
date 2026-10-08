# Contributing

Issues and pull requests are welcome.

## Reporting a problem

Open an issue from one of the templates: a bug report, a tool listing amazon-tool read wrong, or a macro idea. A bug report asks for:

- Machine (Z1 or Z1 Pro), firmware version and Makera Studio version
- The command you ran, or the options you changed
- What happened, and what you expected

Studio's log usually shows what the machine did. On macOS it is in `~/Library/Application Support/MakeraStudio/logs`. Paste the lines from `Playing file:` onward.

## Adding or changing a macro

- One folder per macro, with a `README.md` covering what it does, requirements (one is a copy of the whole repo, for `shared/`), how to run it from Studio, and its limits.
- Generators are Python 3.9 or later, using only the standard library. Don't commit generated `.nc` files; add them to the folder's `.gitignore`.
- Code that two macros need goes in [shared](shared) instead of being copied: machine limits, `fmt()`, `ORIGINS`, Studio's header, probe and end-of-job lines, pass depths, options and checks. A script imports it with the repo root on its path, before its other repo imports:

  ```python
  HERE = Path(__file__).resolve().parent
  sys.path.insert(0, str(HERE.parent))
  from shared import cli, toolpath, z1  # noqa: E402
  from shared.z1 import fmt  # noqa: E402
  ```

  Only `fmt` is imported by name; write the rest as `z1.SAFE_Z`, `cli.stop()` and so on, so a bare name is the script's own. `stockref` stays probe-stock's and `toollib` stays tool-library's; a macro that reads the stock reference puts `HERE.parent / "probe-stock"` on the path too, as square-side does. [shared/README.md](shared/README.md) says what is where.
- Take cutters from [tool-library](tool-library) with `--tool`/`--material`, as the cutting macros do: `cli.apply_tool()` imports `toollib` only when `--tool` is given, so a script run without it doesn't need tool-library. Build the parser with `cli.parser()`, which doesn't take abbreviated options, and the options with `cli.add_variables()`, which records the ones you gave in `args.given`: `apply_tool()` leaves those alone, and stops unless the tool is a flat end mill (a ball, chamfer or drill can't face flat or cut a square wall). New tools go in `tool-library/custom/` and are committed; Makera's, downloaded into `tool-library/cache/`, aren't, as their repo has no license.
- To cut from measured stock, read [probe-stock](probe-stock)'s reference rather than probing in each job, as surface-to-lowest-point does: `stockref.load()` gives the stock as it is now and `check()` stops when the origin may have moved; `stockref.record()` notes what the job you wrote does to the stock, so the next macro counts it once Studio's log shows it finished.
- Keep the root `README.md` table up to date, and add new folders to the macro list in `.github/ISSUE_TEMPLATE/bug_report.yml`.
- Add tests to `tests/`, one `test_<macro>.py` per macro (`test_shared.py` for `shared/`). `tests/helpers.py` runs a script and reads back the G-code it writes, for checks like "every plunge lands off the stock", and `Machine` makes up Studio's log for jobs that read it.

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

Before opening a pull request, run the tests, and Ruff if you have it (its rules are in `ruff.toml`):

```sh
python3 -m unittest discover -s tests
ruff check
```

GitHub Actions runs both on every pull request. The tests run on Linux (Python 3.9, which macOS ships, and the latest), macOS and Windows. `tests/test_repo.py` also checks the rules above: a README, a table row and an issue form entry for each macro (`shared` isn't one), the standard library only, and no generated files.

The tests check the G-code a script writes, not what the machine does with it. Say in the pull request what you ran on a machine (model and firmware) and what you only checked another way. For anything that moves near the stock, run it first with the cutter well above the work or with the spindle off, and watch the first run of every change.

## License

Contributions are accepted under the [MIT License](LICENSE).
