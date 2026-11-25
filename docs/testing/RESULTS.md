# Anonymized staging results — 24 November 2026

Source runtime: 0.4.1. Test host: Ubuntu 24.04, **1 vCPU / 2 GiB RAM**, nominal 1 Gbit/s link. The TLS load generator shared that CPU with WARP and the relay. No public VPS address, hostname, PID, boot ID, SSH data or registration profile is included here.

## Throughput with eight clients

| Native WARP clients | Aggregate Mbit/s | Failed transfers |
|---:|---:|---:|
| 1 | 84.6 | 0/8 |
| 2 | 132.3 | 0/8 |
| 4 | 164.6 | 0/8 |
| 8 | 172.2 | 0/8 |

CPU reached 100% at the larger steps. Adding four more tunnels barely changed this result. The experiment did **not verify a gigabit** and is not an independent external-client measurement of the physical port's maximum.

## Eight real independent registrations

| Concurrent clients | Aggregate Mbit/s | Failures | Minimum available MiB |
|---:|---:|---:|---:|
| 8 | 172.2 | 0 | 700 |
| 32 | 139.5 | 0 | 625 |
| 64 | 65.5 | 0 | 483 |

All eight backend names received connections. The temporary worker memory/process limits did not fire; OOM kills were zero. Some exploratory larger-group attempts encountered changed exit IPs or temporary SOCKS/transport errors and were not treated as successful measurements. Eight tunnels were revalidated before the final passing matrix. A full load test of 12/30/128 native WARP tunnels was not completed.

## 128 real clients and the local control

Cold bursts of 128 clients produced many errors at 100% CPU. A gradual 12-second ramp in a 24-second trial with four WARP tunnels produced **45 successful clients out of 128**, 83 errors and 68.5 Mbit/s counted from successful transfers. Error-free 128 real clients were **not verified**.

The real relay with four synthetic loopback echo backends separately passed **128/128** in about 1.09 seconds. That is a local connection-control test, not WARP/TLS/Google capacity evidence. Do not substitute it for the failed real 128-client experiment.

## Service and failure/recovery checks

- Two anonymous Gemini probes returned `SERVING` with Google=DE. A stable full Gemini pool with spares was not established. Monitors were intentionally stopped/restarted between load trials, so historical success is not claimed as uninterrupted availability.
- YouTube exits passed the observed Google RU policy. No browser/account playback or absence-of-ads claim was tested.
- Real YouTube failover used a **100 Mbit/s control target**, not 1 Gbit/s: two primary nodes plus one reserve, simultaneous 134.3 Mbit/s before and 138.2 after recovery.
- One primary container was stopped; explicit `check` requests promoted the reserve in 2.92 s and automatic container/reserve recovery completed in 42.18 s. These are forced-check timings, not the default watchdog SLA.
- Existing host Xray/Psiphon/AdGuard/nginx/PostgreSQL service PIDs were unchanged. Their routing was not edited. Temporary stress containers/volumes were cleaned up.

## Measurement correction

A final request started near the end of a speed window could time out before receiving new bytes, incorrectly discarding earlier valid transfers. 0.4.1 preserves earlier actual bytes while counting the idle interval. Entirely empty trials, early timeouts, HTML and HTTP errors still do not certify speed. Separate tests cover immediate quarantine when an identity changes or a bad service verdict is known before final verification.

## Data and interpretation

[results-1g.json](results-1g.json) contains selected metrics with operational identifiers removed. The original 97-test runtime suite passed locally and on the staging VPS; this public archive adds source/distribution checks described in [TESTING](../TESTING.md). Configured GitHub CI jobs are future runs after publication, not evidence that they already ran online.

The Cloudflare test endpoint rejected a 100 MB request with HTTP 403, so repeated available 8 MB responses were used. A second public endpoint returned HTTP errors even on direct parallel requests; those rows were excluded from WARP capacity conclusions. Generator placement, endpoint behavior and CPU load must be reported when reproducing the figures.
