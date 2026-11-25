#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
[[ "$EUID" == 0 ]] || { echo 'Run as root' >&2; exit 1; }
source_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null; then
    apt-get update
    apt-get install -y python3 ca-certificates
fi
if [[ $# == 0 && -t 0 && -t 1 ]]; then
    exec python3 "$source_dir/warp_ctl.py" menu
fi
exec python3 "$source_dir/warp_install.py" "$@"
