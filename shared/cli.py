"""Options and checks the macros share: VARIABLES as flags, --tool, --log, and the limits every job is held to."""

import argparse
import sys
from pathlib import Path

from .z1 import FLUTE_LENGTH, MAX_FEED, MAX_RPM, STUDIO_LOGS, fmt

ROOT = Path(__file__).resolve().parents[1]


def parser(doc):
    """A macro's parser, described by its docstring's first paragraph.

    Options have to be spelled out (no --pass for --pass-depth), so a command
    means the same thing after an option is added.
    """
    return argparse.ArgumentParser(description=doc.split("\n\n")[0], allow_abbrev=False)


class Given(argparse.Action):
    """Stores the value and adds the name to args.given, the settings the user gave, which --tool leaves alone."""

    def __call__(self, parser, namespace, value, option_string=None):
        setattr(namespace, self.dest, value)
        namespace.given = {*namespace.given, self.dest}


def add_variables(p, variables, choices=None):
    """A flag for each of a macro's VARIABLES (stock_width -> --stock-width), typed like its default, which --help shows."""
    p.set_defaults(given=frozenset())
    for name, default in variables.items():
        flag = "--" + name.replace("_", "-")
        if name in (choices or {}):
            p.add_argument(flag, default=default, choices=choices[name], action=Given, help="default: %(default)s")
        else:
            p.add_argument(flag, type=type(default), default=default, action=Given, help="default: %(default)s")


def add_log(p):
    p.add_argument("--log", type=Path, default=STUDIO_LOGS, help="Studio's log folder or one log file (default: %(default)s)")


def add_tool(p, takes="tool_dia, rpm, feeds, pass_depth and stepover"):
    """--tool and --material."""
    p.add_argument("--tool", help=f"take {takes} from this tool in"
                   " ../tool-library (see tool-library/toollib.py list); options you pass still win")
    p.add_argument("--material", default="Aluminum", help="which of the tool's presets to use (default: Aluminum)")


def apply_tool(v, args):
    """Set v's cutter settings from --tool and --material, except those in args.given: (tool, preset).

    Every macro cuts with the end and side of a flat end mill, so any other type stops the
    macro (named after the script). toollib is only imported here, so a macro run without
    --tool doesn't need tool-library.
    """
    sys.path.insert(0, str(ROOT / "tool-library"))
    import toollib

    tool = toollib.find(args.tool)
    if tool.type != "flat end mill":
        stop(Path(sys.argv[0]).stem, [f"--tool must be a flat end mill (got {tool.type or 'no type'})"])
    return toollib.apply(v, tool, args.material, given=args.given)


def take_tool(v, args):
    """A facing macro's --tool: apply it, print what was taken, and return the flute length for Studio's header."""
    if not args.tool:
        return FLUTE_LENGTH
    tool, preset = apply_tool(v, args)
    print(
        f"tool: {tool.description} ({tool.source}), {preset.name}: {fmt(v['tool_dia'])} mm, {v['rpm']} rpm,"
        f" {v['feed']} mm/min, plunge {v['plunge_feed']}, {fmt(v['pass_depth'])} mm passes, {fmt(v['stepover'])} mm stepover"
    )
    return tool.flute_length or FLUTE_LENGTH  # 0 when the library doesn't give it


def positive(v, names):
    return [f"{name} must be positive" for name in names if v[name] <= 0]


def feeds_and_rpm(v):
    """The Z1's limits on feed, plunge_feed and rpm."""
    errors = []
    if not 0 < v["feed"] <= MAX_FEED or not 0 < v["plunge_feed"] <= MAX_FEED:
        errors.append(f"feeds must be between 0 and {MAX_FEED} mm/min")
    if not 0 < v["rpm"] <= MAX_RPM:
        errors.append(f"rpm must be between 0 and {MAX_RPM}")
    return errors


def stop(who, errors):
    """Stop with every error at once, if there are any."""
    if errors:
        raise SystemExit(f"{who}: " + "; ".join(errors))
