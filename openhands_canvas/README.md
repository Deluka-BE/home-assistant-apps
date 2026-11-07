# OpenHands Canvas v0.1 Alpha

Version: **0.1.0-alpha.4**. Experimental, aarch64 only.

Supervisor packaging for the existing pinned OpenHands Canvas runtime in
[Deluka-BE/home-assistant-apps](https://github.com/Deluka-BE/home-assistant-apps).
Boot defaults to manual; ingress is admin-only on internal port 8099, with no
host ports. Startup requires authenticated Supervisor discovery of the ingress
prefix and at least one trusted Home Assistant origin. Missing or
invalid prerequisites fail closed before any daemon starts. There is no security
bypass option. The native proxy retains Supervisor peer/identity/path checks,
exact origin enforcement and server-only session keys; backends use loopback
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
Core config remain allowed too. Local HTTP support is described below. Missing origins
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

## Local HTTP Home Assistant

Alpha.3 addresses the reported pattern of a correctly prefixed module request
returning 403: module GETs can carry an HTTP Origin while HTML navigation omits
Origin. Alpha.2 allowed only HTTPS origins, so its admission gate rejected those
modules. A diagnostic `category=js status=403 reason=origin` confirms that gate;
the browser report alone does not establish where the 403 was generated.

Authenticated Supervisor `/core/api/config` `internal_url` now establishes a local
HTTP origin when it is a literal RFC1918, loopback, link-local or IPv6 ULA address.
When `internal_url` is null or empty, an administrator can supply this fallback:

```yaml
ingress_local_origin: http://192.168.50.10:8123
```

Use exactly the local browser origin: no trailing slash, credentials, path, query
or fragment. Ports must be 1–65535; public IPs, DNS names and wildcards are rejected.
The option is ignored when Core has a nonempty `internal_url`; an invalid explicit
option always fails closed with `ingress_local_origin_invalid`. Browser Host,
forwarded headers and Origin never establish trust. HTTPS `ingress_origin` retains
its existing validation. HTTP transport relies on a trusted LAN; this does not
provide TLS. All Supervisor peer, identity, ingress path and write/WS Origin checks
remain required. The historical `https_origin_missing` code is retained when no
trusted origin is available. Diagnostic logs retain fixed, secret-free codes.

## Alpha.4: frontend telemetry opt-out
The native Ingress HTML injects `window.__AGENT_CANVAS_DO_NOT_TRACK__=true`
before the first JavaScript module. This matches the upstream runtime flag used
to disable **frontend product telemetry**. This setting does not guarantee zero
third-party network requests, remove previously stored browser data, or remove
the authenticated Home Assistant Ingress prefix from necessary browser requests.
The backend URL in the Canvas store still uses this prefix; do not share URLs
from the Canvas settings view or browser devtools.

Iris independently reviewed and approved the narrow opt-out code change.
The focused synthetic HTML test passed. Actual bundled frontend behavior and
prior localStorage entries remain to be checked in a fresh Brave profile.
No Ingress auth, origin, peer, user identity, or API/WS/SSE routing rules change.

## Build and release handoff
The dedicated `OpenHands Canvas ARM64 Alpha` workflow on GitHub Actions builds
and pushes a pinned ARM64 image on `main` using an immutable version tag.
This version is `0.1.0-alpha.4`. Installing it requires a successful public
GHCR release. Existing Alpha.3 remains available. The upstream Canvas image
digest is unchanged.
