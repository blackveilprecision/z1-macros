# shared

Code the macros share, so each fact about the Z1 and Studio lives in one place. It isn't a macro: nothing here runs on its own or writes a job.

| Module | What's in it |
|---|---|
| [z1.py](z1.py) | The machine and Studio: `MAX_FEED`, `MAX_RPM`, `SAFE_Z`, `APPROACH`, `LEAD`, Studio's Z probe numbers, `ORIGINS`, `STUDIO_LOGS`; `fmt()` for numbers in G-code; `to_program()` from stock to program coordinates; `z_probe()`, Studio's own Z probe; `start()`, `spindle_on()`, `cut()` and `end()`, the parts of a cutting job written the way Studio exports them; `face()`, the facing macros' levels from spindle on to the end |
| [toolpath.py](toolpath.py) | `equal_levels()` for pass depths, `spread()` and `probe_grid()` for probe points, `raster()` for facing the whole top and `reach()`, how far past the edges its cutter goes; all in stock coordinates, from the front-left corner |
| [cli.py](cli.py) | `parser()`, `add_variables()` (which records the options given), `add_tool()` (`--tool` and `--material`), `add_log()` for the options; `apply_tool()`, which imports [toollib](../tool-library) only for `--tool`, keeps the options given and refuses anything but a flat end mill, and `take_tool()`, a facing macro's `--tool` from applying it to printing what it took; `positive()`, `feeds_and_rpm()` and `stop()` for checking values |

A macro puts the repo root on its path before its first import from here:

```python
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from shared import cli, toolpath, z1  # noqa: E402
from shared.z1 import fmt  # noqa: E402
```

Not here:

- `stockref` stays in [probe-stock](../probe-stock): it is probe-stock's reference, and `stock.json` and `jobs.json` are kept beside it. A macro that reads it puts `HERE.parent / "probe-stock"` on the path too.
- `toollib` stays in [tool-library](../tool-library): it has its own command line, and its files are next to it.
- Anything only one macro uses. Move it here when a second one needs it.

Changing something here changes every macro's G-code; `tests/test_shared.py` covers this folder, and each macro's tests cover what it writes.
