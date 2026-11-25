# Operations / Эксплуатация

## Read current state

```bash
warp-pool --all status
warp-pool --instance 1 status --json
warp-pool --instance 1 logs -n 100 -f
```

A running service is not a verified exit. Node `READY` is fresh service health; capacity mode also requires a valid speed sample before using that node. Capacity phase `READY` additionally means the simultaneous aggregate target and required reserve are complete. `ready_tunnels` is not the number of Gbit/s channels.

| State / phase | Meaning |
|---|---|
| `STARTING` / `SEARCHING` | Startup or search; not verified |
| `READY` on a node | Fresh checks accepted; capacity sample is evaluated separately |
| `BAD` | Explicit region/model/speed rejection; excluded |
| `UNKNOWN` | Inconclusive or changed identity; excluded from new traffic |
| `NETWORK` | Transport not verified |
| `ROLLING` / `COOLDOWN` | Repair or its rate limit/backoff |
| `DORMANT` | Unallocated slot; no capacity container started |
| capacity `BUILDING` | Partial working composition; target not yet certified |
| `RESERVE_SEARCH` | Target reached; required spares still missing |
| capacity `READY` | Common target plus full required reserve |
| `SATURATED` | Growth paused after small gains in repeated expansions |
| `LIMIT` / `RESOURCE_LIMIT` | Budget or resource floor prevents more allocation |

`PRIMARY`, `RESERVE`, `SEARCH`, `DORMANT` describe a node's capacity role. Stale observations after restart are shown as previous/UNKNOWN, not current `SERVING`. Status checks the manager PID, boot ID and freshness.

## Queue fresh checks

```bash
warp-pool --instance 1 check all --full
warp-pool --instance 1 speed all
warp-pool --instance 1 rotate warp2 --method reconnect
```

Acknowledgement is not a successful verdict. Watch status/logs. A speed endpoint failure is inconclusive and must not rotate an otherwise verified model exit merely because the endpoint is broken. During an IP change, new streams are excluded before the long service recheck finishes.

## Updates

From a full source tree or verified archive:

```bash
warp-pool --instance 1 update /root/Warp-Manager
warp-pool --all update /root/Warp-Manager-v0.4.1.zip --sha256 "$EXPECTED_SHA256"
```

For a published remote ZIP, supply its full HTTPS URL and trusted digest. Updates are staged and worker images are built before the selected service is stopped. Configuration, role, ports, registration volumes, run state and autostart are preserved unless explicitly changed. A failed transaction restores the previous selected installation; all-instance updates report partial failures accurately.

For source updates through the standalone bootstrap after publication:

```bash
curl -fsSL https://raw.githubusercontent.com/Forcus356/Warp-Manager/main/install_warp_manager.sh -o /tmp/install_warp_manager.sh
sudo bash /tmp/install_warp_manager.sh --source-ref main --instance 1 --update
```

This is an explicit source update. Opening the menu does not download a release. The menu's update action accepts a local source directory or ZIP; HTTPS with a digest uses the CLI. Newest GitHub Release discovery is not implemented.

Backups are under `/opt/warp-google-pool-backups/`. Keep the previous archive/configuration available when applying a release. To reapply an earlier source deliberately, use that source's installer with `--instance N --update`; validate compatibility with any fields added since that version. There is no blanket guarantee that arbitrary downgrades understand newer configurations.

## Files and registration storage

- Instance 1: `/opt/warp-google-pool`, `warp-google-pool.service`.
- Instance N: `/opt/warp-google-pool-N`, `warp-google-pool-N.service`.
- Shared management code: `/usr/local/lib/warp-google-pool`.
- State: `state.json`, `previous-state.json`, `runtime.json`, private `requests/`.
- Registrations: dedicated named Docker volumes, one per native WARP client.
- Dependency receipts: `/var/lib/warp-google-pool-system`.

Runtime files are owner-readable with restrictive permissions. Do not publish them as configuration examples. A directory backup of manager files is not a backup of registration volumes. Shrinking a pool retains excluded registration volumes for later use. Source updates do not intentionally copy a host's unrelated WARP account.

Docker logs are capped per worker (5 MB × 2 files); WARP qlog is disabled. System journal retention remains a host administration setting.

## Removal

```bash
warp-pool --instance 2 uninstall
# Explicitly discard local registration volumes as well:
warp-pool --instance 2 uninstall --purge-profiles
# Also request cleanup of this project's owned, unused dependencies:
warp-pool --instance 2 uninstall --purge-profiles --purge-deps
```

Default removal keeps local registration volumes. `--purge-profiles` deletes them. Dependency cleanup uses ownership receipts, APT simulation and checks for other consumers; preexisting Docker and other applications are preserved. Removal is per instance. `--all uninstall` is rejected. `--yes` skips the selected-instance deletion prompt for explicit automation.

## Troubleshooting

| Symptom | First checks |
|---|---|
| SOCKS closed / HTTP 503 | No eligible verified node; inspect role, verdict, speed sample and freshness |
| Google RU in Gemini | Expected rejection; country filtering does not assign WARP country |
| CAPTCHA / REFUSED | Explicit service failure; inspect current versus previous observation |
| INCONCLUSIVE | Probe did not establish success; compare transport, Google page and model fields |
| `RESOURCE_LIMIT` | Available RAM/disk on the host, not the configured tunnel count |
| More tunnels but no gain | Shared network/CPU/test endpoint; inspect simultaneous rate and local load generator |
| Port cannot bind | Existing listeners and other saved instances, including stopped configurations |
| Xray cannot reach loopback | Xray may be in Docker; use host networking or an intentional private bind |
| Both modes enabled on one instance | Use independent instances; validation intentionally rejects this combination |

For reproducible evidence, redact addresses/identifiers before sharing a status dump. Never send registration files, private keys or SSH credentials to an issue.
