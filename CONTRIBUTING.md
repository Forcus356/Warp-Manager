# Contributing

Warp-Manager is a Python-standard-library manager with Bash/native-client workers. Changes should preserve independent Gemini/YouTube policy, accurate current versus historical state, registration volumes and compatibility with installed `warp-google-pool` paths.

## Local checks

```bash
python3 scripts/check_project.py
python3 -m unittest discover -s tests -v
python3 scripts/build_release.py
```

Use Linux/Python 3.10+. The automated suite uses loopback TCP and dummy subprocesses; it does not register WARP or install dependencies. Manual staging tests and their effects are described in [TESTING](docs/TESTING.md).

## Changes and documentation

Keep source VERSION and the VERSION file aligned. Update both English/Russian guides for behavior changes, then the other localized READMEs when their claims change. `README_RU.md` is a compatibility copy of `README.ru_RU.md`. Examples are schema-checked against real defaults/exporters. Generate terminal previews from the renderer; clearly label synthetic state.

Regenerating PNG previews with `python3 scripts/render_previews.py` requires the optional Pillow package and a DejaVu Sans Mono font (Debian/Ubuntu packages `python3-pil` and `fonts-dejavu-core`). They are not needed by the manager, tests or release builder.

Preserve the original GPL/MIT notices and add attribution for newly incorporated code. Cloudflare binaries are downloaded separately rather than bundled into the source ZIP.

## Bug reports

Include version, distribution, Python version, role, a redacted configuration summary, reproduction steps and expected/actual results. Distinguish queued request acknowledgement from the monitor's completed verdict. For performance, record the generator location, actual concurrent transfers, failures, CPU/RAM and common elapsed time rather than adding individual speed samples.

Do not attach registration profiles, private keys, SSH credentials, raw runtime snapshots or unredacted logs. See [SECURITY](SECURITY.md) for confidential issues. Opening a pull request is not evidence that a real Google account or provider exit is available.
