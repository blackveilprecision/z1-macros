## What this changes

<!-- What the macro or script does differently, and why. Link an issue if there is one. -->

## Testing

<!-- Say what ran on a machine and what you only checked another way (see CONTRIBUTING.md). -->

- Ran on a machine (model, firmware, what you ran):
- Checked another way (tests, Studio's preview, reading the G-code):

## Checklist

- [ ] `python3 -m unittest discover -s tests` passes, with tests for what changed
- [ ] The macro's README covers the change
- [ ] Python standard library only; no generated files (`.nc`, tool files) committed
- [ ] New macro: its own folder with a README, a row in the root README table, and an entry in `.github/ISSUE_TEMPLATE/bug_report.yml`
