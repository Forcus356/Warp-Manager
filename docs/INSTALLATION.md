# Installation / Установка

## Requirements

Ubuntu/Debian x86_64, root, systemd, `/dev/net/tun`, Python 3.10+ and network access. Ubuntu 24.04/Python 3.12 was exercised on real VPS machines; other supported interpreter versions are covered by the configured CI matrix after publication. The installer does not upgrade an already installed old Python. Debian 11's default Python 3.9 is too old for this code.

Docker, Compose v2, curl and host tools are installed if absent. Existing Docker is not replaced. Docker builds the official `cloudflare-warp` package inside Ubuntu 24.04. WARP is started in proxy mode inside dedicated containers; the host's default route is not intentionally redirected.

## Full source archive

```bash
unzip Warp-Manager-v0.4.1.zip
cd Warp-Manager
sudo bash install.sh
```

No-argument terminal installation opens the console. Choose `t` for templates, `c` to create an instance, or `1/2` for Easy/Advanced. The new services start before exits are verified; inspect their current state rather than interpreting installation success as Gemini availability.

## GitHub bootstrap

This URL becomes available after the owner uploads this project to **Forcus356/Warp-Manager**, branch `main`:

```bash
curl -fsSL https://raw.githubusercontent.com/Forcus356/Warp-Manager/main/install_warp_manager.sh -o /tmp/install_warp_manager.sh
sudo bash /tmp/install_warp_manager.sh
```

For a published tag:

```bash
sudo bash /tmp/install_warp_manager.sh --source-ref v0.4.1 --instance 1 --role gemini --target-gbps 1 --tunnels 12
```

`--source-repo OWNER/REPO` selects a trusted fork. `--source-ref` accepts a branch, tag or full commit. Refs are resolved through GitHub to an immutable commit before downloading from codeload. `--source-sha256` optionally checks the **codeload ZIP**, not the differently packaged release ZIP. The bootstrap rejects unsafe archive paths, symlinks, duplicate members and oversized downloads before installation. TLS/commit identity remains the source trust boundary; a checksum is not a maintainer signature.

A full local checkout takes a local fast path to `install.sh` unless a `--source-*` option explicitly requests remote source. A standalone download stages the complete repository; it does not run an incomplete collection of Python files.

## Explicit installations

```bash
# Fixed three-tunnel Gemini pool.
sudo bash install.sh --instance 1 --role gemini --tunnels 3 --impersonate
# Capacity mode: 1 Gbit/s target; 12 total slots including reserves/search.
sudo bash install.sh --instance 1 --role gemini --target-gbps 1 --tunnels 12 --impersonate
# Separate YouTube policy. Never enable both service checks on one instance.
sudo bash install.sh --instance 2 --role youtube --target-gbps 0.1 --tunnels 6
# Ordinary WARP: Gemini disabled; use configuration/menu to clear deny_regions if desired.
sudo bash install.sh --instance 3 --role warp --tunnels 3
```

`--target-gbps 0` is fixed mode. Enabling capacity mode without an explicit tunnel limit reserves 64 configurable slots; dormant slots do not create containers. The actual limit can be 1–128. A fixed tunnel count is required when disabling capacity mode through an installer update.

```bash
sudo bash install.sh --instance 1 --update --target-gbps 0 --tunnels 3
sudo bash install.sh --instance 2 --update --port 40120 --no-http
```

Port defaults: instance 1 = 40010/40011, instance 2 = 40012/40013. Backend ports are loopback-only and must not overlap another application. YouTube requires loopback; other roles may use an existing private IPv4. Public binding is rejected.

Use the matching instance numbers in [example configs](../examples/README.md):

```bash
sudo bash install.sh --instance 1 --config examples/gemini-capacity.json
sudo bash install.sh --instance 2 --config examples/youtube-capacity.json
```

## После установки

```bash
warp-pool --all status
warp-pool --instance 1 logs -n 100
warp-pool --instance 1 check all --full
```

Статус `active` у systemd означает работающий монитор. Узлы и целевая скорость подтверждаются отдельно. Для интеграции с панелью откройте [руководство Xray](xray/README.md); установщик её автоматически не меняет.
