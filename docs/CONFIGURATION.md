# Configuration / Настройки

Examples in [examples](../examples/README.md) are generated with the same defaults and validated by the same code as installed instances. Never copy live `reg.json`, private keys or runtime state into a public repository.

## Identity and policy

| JSON field | Default / range | Meaning |
|---|---|---|
| `instance` | 1; 1–99 | Independent directory, service, ports and registrations |
| `nodes` | 1–128 objects | Only `name`, `container`, `port`; unique within an instance |
| `listen_host` | `127.0.0.1` | Loopback/private IPv4; YouTube requires loopback |
| `listen_port` | 40010 + 2×(N−1) | SOCKS front, 1024–65534 |
| `http_port` | next port on a new install | `null` disables HTTP; saved upgrades preserve it |
| `gemini_check` | true for Gemini | Requires `SERVING` when enabled |
| `youtube_mode` | false | Mandatory observed Google RU; cannot coexist with Gemini checks |
| `deny_regions` | `["RU"]` | Observed Google codes to reject in ordinary/Gemini mode |
| `allow_regions` | `[]` | Empty permits any non-denied observed region |
| `selection_strategy` | `failover` | Fixed mode: `failover` or `least_connections` |
| `impersonate_dir` | empty | Verified browser profile path; manage with `browser on/off` |

Region lists do not select WARP's country. YouTube overrides ordinary allow/deny acceptance with mandatory RU but keeps those saved lists. For ordinary unrestricted WARP, disable Gemini and clear deny/allow lists explicitly.

## Timing and repair

| JSON field | Default | Unit / condition |
|---|---:|---|
| `check_interval` | 60 | Seconds, minimum 60 |
| `gemini_interval` | 600 | Seconds between full model checks; changed IP forces a fresh check |
| `health_ttl` | 360 | Seconds; at least `check_interval + 300` |
| `wait_timeout` | 75 | Startup grace, 20–300 seconds |
| `confirmation_delay` | 5 | Seconds before quick post-repair verification |
| `rotation_delay` | 20 | Initial repair backoff, seconds, minimum 2 |
| `max_backoff` | 900 | Seconds; at least rotation_delay |
| `max_rotations_per_hour` | 12 | Per node, 1–120 |
| `bad_threshold` | 2 | Matching explicit failures before repair, 2–20 |
| `fail_threshold` | 3 | Transport failures before reconnect, 2–20 |
| `bad_ip_ttl` | 3600 | Rejected-IP cache, seconds |
| `rotation_method` | `registration` | `registration`, `keys`, `reconnect` |

The first known bad/uncertain result removes routing eligibility. Repair waits for confirmation/backoff. Limits and rejected IPs persist through restarts; READY/model/speed claims do not.

## Speed fields

| Field | Default | Meaning |
|---|---:|---|
| `min_speed_kib` | Gemini 0 / new YouTube 1500 | KiB/s floor; 1500 ≈ 12.3 Mbit/s |
| `probe_mb` | 4 | 0–10 MiB fixed-mode probe; 0 = YouTube page |
| `slow_threshold` | 2 | Consecutive slow samples before rejection |
| `speed_interval` | 900 | Seconds between fixed-mode speed probes |
| `target_gbps` | 0 | Capacity mode target in Gbit/s; 0 = fixed mode |
| `capacity_seconds` | 6 | Each capacity stage, 2–30 seconds |
| `capacity_interval` | 900 | Seconds between repeated capacity measurements |
| `capacity_sample_ttl` | 2400 | Seconds; at least 2×capacity_interval |
| `capacity_parallel_search` | 2 | Concurrent searching/unmeasured slots, 1–8 |
| `capacity_url` | Cloudflare 8 MB URL | HTTPS test file without credentials or fragment |

Capacity mode serializes individual speed stages and intentionally measures primary tunnels together for the aggregate stage. Its per-node measurements also enforce an enabled speed floor. Probe errors are inconclusive, not proof of a broken Gemini exit. Changed IP, stale health or expired speed information invalidates eligibility for a new capacity composition.

A 10 Gbit/s × 6 second stage can transfer roughly 7.5 GB, plus calibration and repeat stages. The saved target is nominal user policy; the common measured rate confirms it. CLI `--interval` and menu timing are **minutes**, while JSON timing fields are **seconds**. Use a decimal point (`0.1`), not a comma.

## Capacity semantics

`nodes` is the total budget including primary, reserve and searching slots. First activation without an explicit budget reserves 64 slots; they do not all start. The balancer uses active/connecting TCP counts weighted by measured speed. Spares do not carry new user traffic until promoted. Required reserve is `max(1, floor(primary_count/3))` once a primary exists.

`SATURATED` pauses further growth after three expansions with less than 5% gain; it retries after the interval. `LIMIT` means the configured budget is exhausted. `RESOURCE_LIMIT` prevents another container start when disk is below 512 MiB or available RAM below 256 MiB. These checks are bounds, not a resource reservation or a guarantee that any configured pool fits a VPS.

## Editing

Easy preserves hidden Advanced settings. Changes are drafted, previewed and applied only on confirmation. A direct scalar `config` save usually needs `restart`; structural changes use the installer transaction. Use `capacity --gbps 0 --max-tunnels N` to disable with an explicit fixed size. Do not hand-edit a running Compose file or copy runtime READY state to seed a new server.
