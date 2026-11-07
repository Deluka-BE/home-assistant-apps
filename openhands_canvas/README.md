# OpenHands Canvas v0.1 Alpha

Version: **0.1.0-alpha.2**. Experimental, aarch64 only.

Supervisor packaging for the existing pinned OpenHands Canvas runtime in
[Deluka-BE/home-assistant-apps](https://github.com/Deluka-BE/home-assistant-apps).
Boot defaults to manual; ingress is admin-only on internal port 8099, with no
host ports. Startup requires authenticated Supervisor discovery of the ingress
prefix and at least one configured HTTPS Home Assistant origin. Missing or
invalid prerequisites fail closed before any daemon starts. There is no security
bypass option. The native proxy retains Supervisor peer/identity/path checks,
HTTPS origin enforcement and server-only session keys; backends use loopback
and VSCode stays disabled.

## Home Assistant Cloud automatic HTTPS

Internal HTTP with automatic Nabu Casa HTTPS is supported without changing Home
Assistant network settings. Core `/api/config` may have `external_url: null`.
In that case set this optional add-on configuration value to the HTTPS origin
shown in Home Assistant Cloud, then restart the add-on:

```yaml
ingress_origin: https://your-instance.ui.nabu.casa
```

Use only the origin (optional port or trailing slash), without credentials,
paths, query strings or fragments. The administrator supplies the trust value;
browser Origin/Host headers never establish it. HTTPS origins from authenticated
Core config remain allowed too. HTTP origins are never allowed. Missing origins
fail closed with `https_origin_missing`; an invalid explicit value fails closed
with `ingress_origin_invalid`, even if Core has a usable origin. Logs contain
fixed reason codes, never the configured hostname, URL or Supervisor token.

[Official Core cloud source](https://github.com/home-assistant/core/blob/dev/homeassistant/components/cloud/http_api.py)
exposes `remote_domain` through authenticated WebSocket `cloud/status`. This
patch uses explicit configuration rather than inventing an HTTP cloud endpoint
or adding a second authentication protocol. Supervisor discovery remains required.

The reported alpha.1 first HAOS install stopped at `supervisor_discovery` with
two ValueErrors. Internal HTTP plus null external URL explains the missing-origin
path; no local raw install trace establishes both individual causes. Alpha.2
covers this configuration with synthetic tests; actual HAOS startup and browser
HTTP/WS/SSE behavior still require runtime validation after a separate release.

## Build and release handoff

The dedicated `OpenHands Canvas ARM64 Alpha` workflow follows the repository's
native `ubuntu-24.04-arm` / Docker Buildx convention. GitHub Actions builds and
pushes the aarch64 image on pushes to `main` affecting this add-on or its workflow.
Pull requests and manual runs build only. Publication uses the temporary GitHub
Actions token, with no additional configured secret. Before building for release,
an authenticated registry preflight rejects existing tags and fails closed on
ambiguous registry or authentication errors. Workflow runs are serialized per ref.
Its Docker build includes the existing image/runtime checks; it does not start
Canvas. The build context allowlist excludes evidence, tests, caches and local
files. No credentials belong in the image or repository.

The manifest expects
`ghcr.io/deluka-be/ha-openhands-canvas-aarch64:0.1.0-alpha.2`.
This preparation job does **not** trigger that workflow or publish an image.
Installation requires that exact tag to be published with public pull access
after independent review. Do not overwrite released version tags.
No `build.yaml` is needed: the Dockerfile pins its upstream rootfs by digest and
does not use Home Assistant base-image build arguments.

No image build was attempted for this patch. Focused startup and ingress tests
passed (one installed-frontend check skipped). Alpha.1 failed at startup on the
reported HAOS install; alpha.2 has **not been validated on Pi or HAOS**.
After independent review, a separate authorized Pi/HAOS validation must verify
installation, valid startup and fail-closed rejection paths. Actual Supervisor
authentication, admin-only access and HTTP/WS/SSE behavior remain unverified.

See [DOCS.md](DOCS.md) and [FEASIBILITY.md](FEASIBILITY.md) for retained findings;
The obsolete TLS relay remains disabled; native startup no longer calls its
unconditional block. Historical evidence describes earlier implementation stages.
