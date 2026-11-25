# Attribution

This package is distributed under GPL-3.0-only; see LICENSE.

`gemini_probe.py` is copied without changes from Forcus356/Psiphon-Auto-Pin-Manager,
commit cfe8c3b2d798a7ad9180d31d249b9f17f7af9ba5, under GPL-3.0:
https://github.com/Forcus356/Psiphon-Auto-Pin-Manager/blob/cfe8c3b2d798a7ad9180d31d249b9f17f7af9ba5/gemini_probe.py
Its complete original upstream attribution, including the Chara Freedom MIT
copyright and permission notice, is included in `UPSTREAM_NOTICES.md`:
https://github.com/Forcus356/Psiphon-Auto-Pin-Manager/blob/cfe8c3b2d798a7ad9180d31d249b9f17f7af9ba5/THIRD_PARTY_NOTICES.md

Cloudflare WARP is a separately downloaded proprietary service/client; it is not
included in this source archive and is not relicensed by this project's GPL.
The Docker build installs the official package from https://pkg.cloudflareclient.com/.
Starting the containers passes --accept-tos to the official client.

Container startup requirements were checked against cmj2002/warp-docker:
https://github.com/cmj2002/warp-docker
No GOST binary or source from that repository is included.

`warp_system.py` is adapted from `autopin_system.py` at the same upstream commit
(GPL-3.0). Instance templates, menu behavior and Xray routing export were ported
from that project. Psiphon-specific Go/BoltDB/PIN components are not included.

Optional curl-impersonate v2.3.2 is downloaded from lexiforest/curl-impersonate
and checked against the upstream asset SHA-256 pinned by Forcus Auto-Pin.
It is not bundled into this source archive; its own license notices accompany
the separately downloaded release.

When no compatible Compose v2 package is available, an absent Docker Compose
plugin may be installed from the official docker/compose v2.39.4 release,
verified against the SHA-256 of docker-compose-linux-x86_64. Existing plugins
are not overwritten. This binary is also not bundled into the source archive.
