# z1-macros

[![CI](https://github.com/blackveilprecision/z1-macros/actions/workflows/ci.yml/badge.svg)](https://github.com/blackveilprecision/z1-macros/actions/workflows/ci.yml)

G-code macros and generators for the Makera Z1 desktop CNC, run from Makera Studio.

| Folder | What it does |
|---|---|
| [probe-stock](probe-stock) | Probes the stock once (top, sides, bed) for the macros that cut from it |
| [surface-stock](surface-stock) | Probes uneven stock and faces it flat |
| [surface-to-lowest-point](surface-to-lowest-point) | Faces uneven stock down to its lowest point, from probe-stock's reference |
| [face-to-thickness](face-to-thickness) | Faces flat stock down to a set thickness |
| [square-side](square-side) | Cuts the uneven side straight, half the height at a time, from probe-stock's reference |
| [amazon-tool](amazon-tool) | Turns an Amazon cutter listing into a Fusion tool library file |
| [tool-library](tool-library) | Cutters by name for the macros (`--tool`): our own, Makera's and Fusion's |

Not affiliated with Makera. Check every job before running it on your machine.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for what to include and the Z1 firmware quirks to know about.

## License

[MIT](LICENSE)
