# Command reference / Команды

Installed command: `warp-pool`. Per-instance shortcut: `warp-pool-N`. Source checkout: `python3 warp_ctl.py ...`. Administrative commands need the relevant root permissions; automated tests do not install services.

## Scope and output

```bash
warp-pool --version
warp-pool menu
warp-pool --instance 2 easy
warp-pool --instance 2 advanced
warp-pool --all status --json
warp-pool --all config
warp-pool --all xray
warp-pool --all logs -n 100
```

`--instance N` and `--all` can appear before or after the subcommand. Interactive reads with several instances offer one or all; noninteractive use defaults to the first installed instance. `--all` supports **status, logs, config, xray, update**. It does not stop, delete, reconfigure or rotate all instances. `--root PATH` is read-only diagnostics and cannot mutate a real installation.

## Probe queue

```bash
warp-pool --instance 1 check all
warp-pool --instance 1 check warp2 --full
warp-pool --instance 1 search all
warp-pool --instance 1 speed all
warp-pool --instance 1 rotate warp2 --method registration
```

A successful exit means the monitor accepted a queued request, **not that the check passed**. Read `status` or `logs` for completion. `search` requests a full check; the worker performs any required repair under its normal limits. `rotate` targets exactly one node; methods are `registration`, `keys`, `reconnect`. Node names are `warp1`, `warp2`, etc., not Docker container IDs. `--full` clears the model-probe cache for a fresh service verdict.

## Capacity and configuration

```bash
warp-pool --instance 1 capacity --gbps 1 --max-tunnels 12
warp-pool --instance 1 capacity --gbps 0 --max-tunnels 3
warp-pool --instance 1 config
warp-pool --instance 1 config target_gbps
warp-pool --instance 1 config deny_regions RU,BY
warp-pool --instance 1 restart
warp-pool --instance 1 browser on
warp-pool --instance 1 browser off
```

`capacity --gbps` uses Gbit/s. Disabling it requires an explicit fixed count. Ordinary scalar `config KEY VALUE` saves settings and tells you to restart; structural settings (ports/bind/capacity) use a deployment transaction immediately. Nodes/instance identity cannot be edited with this scalar command. Use Easy/Advanced or a validated installer config for structural changes. Browser mode downloads the pinned SHA-256-verified curl-impersonate release.

## Lifecycle

```bash
warp-pool --instance 2 start
warp-pool --instance 2 stop
warp-pool --instance 2 restart
warp-pool --instance 2 autostart on
warp-pool --instance 2 autostart off
warp-pool --instance 2 logs -n 100 -f
```

`stop` stops this manager and its containers, preserving volumes. Restart invalidates old READY observations. Autostart and current run state are different settings.

## Creation and templates

```bash
warp-pool create --role gemini --target-gbps 1 --tunnels 12
warp-pool create --role youtube --target-gbps 0.1 --tunnels 6 --port 40120 --http
warp-pool create --role warp --tunnels 3 --no-http
warp-pool templates both
warp-pool templates both --yes
warp-pool mark-template 1,2
```

`templates` without `--yes` previews the plan. Templates create new instances; existing ones are retained. Marking provenance changes grouping metadata only. A role change through the menu offers a fresh independent instance and preserves edited capacity settings while allocating fresh identities/ports.

## Export, update, remove

```bash
warp-pool --instance 1 xray
warp-pool xray-routing --youtube-instance 2 --gemini-instance 1
warp-pool --instance 1 update /root/Warp-Manager
warp-pool --all update /root/Warp-Manager-v0.4.1.zip --sha256 "$EXPECTED_SHA256"
warp-pool --instance 2 uninstall
warp-pool --instance 2 uninstall --yes --purge-profiles --purge-deps
```

HTTPS update syntax: `update https://HOST/release.zip --sha256 EXPECTED_DIGEST`. No-argument newest-release discovery is not implemented. Menu update accepts a source directory/local ZIP; use the CLI for HTTPS plus digest. Removal without `--purge-profiles` keeps registration volumes; dependencies are removed only if owned and no longer needed. See [operations](OPERATIONS.md).

## Installer flags

| Flag | Meaning |
|---|---|
| `--instance N` | 1–99, default 1 |
| `--role gemini\|youtube\|warp` | Policy; omitted on update preserves the saved role |
| `--tunnels N` | Fixed count or total capacity budget, 1–128 |
| `--target-gbps N` | Capacity target, 0 disables; decimal point |
| `--config FILE` | Validated JSON configuration |
| `--update` / `--rebuild` | Update selected existing instance / rebuild the worker image |
| `--port N` / `--http` / `--no-http` | SOCKS port; HTTP uses next port |
| `--bind ADDR` | Loopback or existing private IPv4; YouTube is loopback-only |
| `--interval N` | **Minutes**, minimum 1; e.g. 1.5 = 90 seconds |
| `--min-speed N` | KiB/s floor; 0 disables |
| `--probe-mb N` | 0–10 MiB; 0 samples a page |
| `--wait N` | Startup grace, seconds, 20–300 |
| `--impersonate` | Install/enable pinned browser TLS profile |
| `--remove` | Internal removal operation; prefer the CLI |

The bootstrap adds `--source-repo`, `--source-ref`, `--source-sha256`; other options are forwarded unchanged to this installer.
