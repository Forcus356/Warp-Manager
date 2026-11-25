#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$source_dir/warp_install.py" --remove "$@"
