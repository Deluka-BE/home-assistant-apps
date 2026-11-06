# OpenHands Canvas v0.1 Alpha

Version: **0.1.0-alpha.1**. Experimental, aarch64 only.

Supervisor packaging for the existing pinned OpenHands Canvas runtime in
[Deluka-BE/home-assistant-apps](https://github.com/Deluka-BE/home-assistant-apps).
Boot defaults to manual; ingress is admin-only on internal port 8099, with no
host ports. Startup requires authenticated Supervisor discovery of the ingress
prefix and at least one configured HTTPS Home Assistant origin. Missing or
invalid prerequisites fail closed before any daemon starts. There is no security
bypass option. The native proxy retains Supervisor peer/identity/path checks,
HTTPS origin enforcement and server-only session keys; backends use loopback
and VSCode stays disabled.

## Build and release handoff

The dedicated `OpenHands Canvas ARM64 Alpha` workflow follows the repository's
native `ubuntu-24.04-arm` / Docker Buildx convention. It is manual and build-only:
no registry login, package write permission, image push, or automatic trigger.
Its Docker build includes the existing image/runtime checks; it does not start
Canvas. The build context allowlist excludes evidence, tests, caches and local
files. No credentials belong in the image or repository.

The manifest expects
`ghcr.io/deluka-be/ha-openhands-canvas-aarch64:0.1.0-alpha.1`.
This job does **not** publish that image. Installation through the repository is
blocked until a separately authorized release builds, reviews and publishes
that exact tag with public pull access. Do not overwrite released version tags.
No `build.yaml` is needed: the Dockerfile pins its upstream rootfs by digest and
does not use Home Assistant base-image build arguments.

Docker is unavailable in this worker, so no image build was attempted. Only a
focused YAML/packaging consistency check was performed. This Alpha wrapper has
**not been tested on Pi or HAOS**; earlier manual upstream runs do not validate it.
After independent review, a separate authorized Pi/HAOS validation must verify
installation, valid startup and fail-closed rejection paths. Actual Supervisor
authentication, admin-only access and HTTP/WS/SSE behavior remain unverified.

See [DOCS.md](DOCS.md) and [FEASIBILITY.md](FEASIBILITY.md) for retained findings;
The obsolete TLS relay remains disabled; native startup no longer calls its
unconditional block. Historical evidence describes earlier implementation stages.
