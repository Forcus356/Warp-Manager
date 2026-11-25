#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
set -euo pipefail
cli() { warp-cli --accept-tos "$@"; }
configure() {
    cli debug qlog disable
    cli mode proxy
    cli proxy port 40000
    cli tunnel protocol set "${WARP_PROTOCOL:-MASQUE}"
    cli connect
}
rotate() {
    # These are dedicated consumer registrations in this project's volumes.
    # Existing host WARP / panel accounts are never used.
    case "$1" in registration|keys|reconnect) ;; *) exit 2 ;; esac
    cli disconnect
    case "$1" in
        registration)
            if [[ -f /var/lib/cloudflare-warp/reg.json ]]; then
                cli registration delete
            fi
            cli registration new
            ;;
        keys) cli tunnel rotate-keys ;;
        reconnect) ;;
        *) exit 2 ;;
    esac
    configure
}
if [[ "${1:-}" == rotate ]]; then
    # The docker exec client may be cancelled while its container process keeps
    # running. Keep the lock inside the container and bound the real operation.
    exec python3 - "$0" "${2:?rotation method required}" <<'PY'
import fcntl, os, signal, subprocess, sys
with open(os.environ.get('WARP_POOL_ROTATION_LOCK','/run/warp-google-pool-rotation.lock'),'a') as lock:
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:sys.exit(75)
    child=subprocess.Popen(['/bin/bash',sys.argv[1],'rotate-locked',sys.argv[2]],start_new_session=True)
    try:sys.exit(child.wait(timeout=110))
    except subprocess.TimeoutExpired:
        try:os.killpg(child.pid,signal.SIGKILL)
        except ProcessLookupError:pass
        child.wait()
        sys.exit(124)
PY
fi
if [[ "${1:-}" == rotate-locked ]]; then
    trap 'cli connect >/dev/null 2>&1 || true' EXIT
    rotate "${2:?rotation method required}"
    exit
fi
[[ "${1:-}" == start ]] || exit 2
mkdir -p /run/dbus /dev/net
if [[ ! -e /dev/net/tun ]]; then mknod /dev/net/tun c 10 200; fi
dbus-uuidgen --ensure
dbus-daemon --system --fork --nopidfile
warp-svc --accept-tos &
daemon_pid=$!
bridge_pid=''
cleanup() {
    [[ -z "$bridge_pid" ]] || kill "$bridge_pid" 2>/dev/null || true
    kill "$daemon_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' TERM INT
ready=0
for attempt in {1..60}; do
    if cli status >/dev/null 2>&1; then ready=1; break; fi
    sleep 1
done
[[ "$ready" == 1 ]] || { echo 'WARP daemon not ready' >&2; exit 1; }
if [[ ! -f /var/lib/cloudflare-warp/reg.json ]]; then cli registration new; fi
configure
python3 /app/tcp_bridge.py &
bridge_pid=$!
wait -n "$daemon_pid" "$bridge_pid"
exit 1
