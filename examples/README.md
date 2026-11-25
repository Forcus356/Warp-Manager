# Validated configuration examples

| File | Instance | Policy |
|---|---:|---|
| [gemini-fixed.json](gemini-fixed.json) | 1 | Three fixed Gemini tunnels |
| [youtube-fixed.json](youtube-fixed.json) | 2 | One fixed YouTube RU tunnel |
| [gemini-capacity.json](gemini-capacity.json) | 1 | 1 Gbit/s target, 12 total slots |
| [youtube-capacity.json](youtube-capacity.json) | 2 | Separate 1 Gbit/s target, 12 total slots |
| [warp-fixed.json](warp-fixed.json) | 3 | Ordinary WARP, no Gemini or region rejection |

Use one alternative per instance, with matching `--instance`:

```bash
sudo bash install.sh --instance 1 --config examples/gemini-capacity.json
sudo bash install.sh --instance 2 --config examples/youtube-capacity.json
```

These are source examples, not measurements. A target of 1 Gbit/s is not a guarantee. Changing only `instance` in a copied file does not rename its containers or ports; generate a fresh configuration for another number:

```bash
python3 - <<'PYGEN'
import json
from warp_profiles import defaults
print(json.dumps(defaults(3, 'youtube', 1), indent=2))
PYGEN
```

The [project checker](../scripts/check_project.py) validates every configuration and ensures the [Xray routing fragment](../docs/xray/youtube-gemini.fragment.json) matches the real exporter.
