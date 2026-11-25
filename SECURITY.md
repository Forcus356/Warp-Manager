# Security policy

Warp-Manager runs administrative installation and dedicated native WARP clients. Use source you trust. The project has no independent security audit or signed-release infrastructure; checksums establish integrity relative to their trusted source, not independent publisher identity.

## Reporting

Use this repository's **Security → Report a vulnerability** if the maintainer has enabled private reporting. Otherwise open an issue with a non-sensitive summary and ask for a private channel. Never publish SSH credentials, WARP registration files, private keys, cookies or live runtime/configuration dumps. The project does not supply an invented support email or a guaranteed response SLA.

## Boundaries

- SOCKS/HTTP fronts are unauthenticated local/private endpoints. Public binds are rejected; private binding still requires the operator to control access to that private network.
- Backend ports remain loopback-only. YouTube mode requires loopback. Xray routing is exported, not applied automatically.
- Gemini/Google checks use the selected SOCKS path and no intentional direct fallback. `SERVING` is an anonymous probe result, not a guarantee for an authenticated account.
- Dedicated registration volumes are private application state. Updates keep them. Default uninstall keeps them; explicit purge removes local volumes.
- Runtime and request files have restrictive permissions. Previous observations are not restored as current READY. Untrusted or stale state is not an authorization mechanism.
- Rotation is bounded and locked inside each container. Docker/root administrators can still inspect or alter the system; this manager is not a sandbox against the VPS owner.

## Downloads

The Docker image obtains the official Cloudflare package through its signed APT repository. It passes `--accept-tos` when starting the client. Optional curl-impersonate and standalone Compose downloads are pinned by SHA-256 before execution. Existing Compose plugins are not overwritten by that fallback.

The standalone GitHub bootstrap resolves a ref to a commit over HTTPS and displays the codeload ZIP digest. It validates archive members, size and source syntax before installation. Trust in the downloaded bootstrap and selected repository remains necessary. HTTPS CLI ZIP updates require an expected SHA-256; a release ZIP and a GitHub codeload ZIP have different packaging/digests.

CI workflows have read-only repository permissions and do not deploy to a VPS, use SSH secrets, register WARP or automatically publish GitHub Releases. Actions are pinned to immutable commits; review them when upgrading.

## Supported fixes

Use the latest reviewed public source/release. Report vulnerabilities against that version and include a safe minimal reproduction. No long-term security support for old private prototype archives is promised. Source licenses and third-party terms are listed in [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES.md).
