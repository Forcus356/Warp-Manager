# Testing / Тестирование

## Automated suite

On Linux with Python 3.10+:

```bash
python3 scripts/check_project.py
python3 -m unittest discover -s tests -v
```

The suite uses unittest and the Python standard library. It opens real loopback sockets and starts dummy local subprocesses, but does not install Docker, contact Cloudflare/Google or use SSH credentials. A sandbox that forbids sockets needs loopback permission; root is not required.

The 0.4.1 runtime suite has 97 tests. Public packaging adds distribution/bootstrap/documentation checks; use the test runner's count for the current total. The previous real VPS runtime result and the new public-repository checks are distinct evidence. CI is configured for Python 3.10–3.14 on Ubuntu 24.04; those GitHub jobs run after publication, not while this archive is being prepared.

Covered behavior includes parsing, rejection/quarantine, generation changes, model cache, stale PID/boot state, HTTP CONNECT, TCP failover, pending-aware selection, spare promotion, speed windows, failed endpoints, rotation locks, backups, update failure and dependency ownership. Configuration examples and generated Xray exports are checked against the actual code. Bootstrap tests reject unsafe ZIP members and failed digests without executing a download.

## Package checks

```bash
python3 scripts/build_release.py
unzip -t dist/Warp-Manager-v0.4.1.zip
cd dist
sha256sum -c SHA256SUMS
```

Release archives contain an internal `MANIFEST.sha256`; the source builder excludes caches and runtime/private files. It fixes ZIP metadata and is reproducible with the same Python/zlib toolchain. A GitHub codeload source ZIP has different packaging and therefore a different whole-file checksum.

## Manual live staging tests

These require a disposable Linux server and real provider/network access. They are **not run by CI** and their success cannot be inferred from unit tests.

| Script | Scope |
|---|---|
| `tests/live_capacity.py` | Requires a complete capacity pool and an explicit `--stop-primary`; stops one real primary container, observes promotion/recovery and sends real HTTPS requests |
| `tests/live_management.py` | Explicitly adopts a disposable single-node YouTube instance, updates all instances including instance 1, changes config/ports and removes the adopted instance |
| `tests/live_transport.py` | Temporary diagnostic front using a real WARP backend; does not certify Gemini |
| `tests/live_load.py` | Dedicated temporary Compose project, real registrations, load steps, memory guard and cleanup of only its own test resources |
| `tests/live_relay_control.py` | Synthetic local echo test of the relay without WARP/TLS/Google |

```bash
# Existing disposable capacity instance with a complete target and reserve:
sudo python3 tests/live_capacity.py --instance 2 --stop-primary --output /tmp/failover.json
# Whole staging-server management exercise, not a production maintenance command:
sudo python3 tests/live_management.py --adopt-test-instance 2 --source "$PWD" --report /tmp/management.json
# Native transport scale, all sample results explicitly marked transport-only:
sudo python3 tests/live_load.py --maximum 8 --sizes 1,2,4,8 --clients 1,8,32,64 --output /tmp/load.json
# Pure local relay control; no root or real WARP needed:
python3 tests/live_relay_control.py --clients 128 --output /tmp/relay-control.json
```

The load test requires an installed or prebuilt `warp-google-pool-worker:VERSION` image (the normal installer builds it). It does not build/download that image itself. The staging load test does not stop existing WARP services automatically; isolate competing test traffic yourself on a disposable host. It uses separate containers, never edits panel routing and verifies transport before measuring. Report all failed clients, CPU, resource limits and address changes. `completed` means the experiment ended; `success` additionally requires no failed transfers. Do not publish raw registration/state files as test evidence.

## Interpretation

The measured [1-vCPU results](testing/RESULTS.md) are a bounded load experiment with the generator on the VPS. They do not certify an external VPN client's gigabit speed, a logged-in Gemini account, a capacity of 128 native tunnels, or uninterrupted service-country identity. The reference JSON is anonymized; raw operational evidence stays outside the public project.
