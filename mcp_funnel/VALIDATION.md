# Validation record

Baseline: branch `ha-container-packaging`, commit
`e67668274c426e9c00eacff5ccaf411e2875eb0f`.

## Executed locally (Windows, Python 3.14)

- Full `python -m unittest discover -q`: 62 passing tests, including all 43
  existing Gateway/regression tests and 19 new Funnel tests.
- Python compilation of add-on scripts and tests: passed.
- Unit/mocked checks: options and capability policy, private data/socket paths,
  preservation of existing state, privilege dropping, login URL allowlist,
  offline-to-online Gateway, no needless Funnel reconfiguration, wrong origin
  repair, timeout/API errors, exact status schema, no-Funnel null response,
  local HTTP-200 health semantics, shutdown handlers and child termination,
  read-only healthcheck, no-publication multiarchitecture workflow.
- Official Docker registry manifest for Tailscale v1.102.4 read and pinned:
  `sha256:2667499ed87ae29218f292556ba062918402dd5e92e93637af14867e4df12dd3`.
  Manifest contains Linux amd64 and arm64. This is not a build result.
- Protected existing Gateway, Render, Auth0 code/configuration and publication
  workflow unchanged. No production credentials used.

## Added but NOT executed here

No Docker daemon or installed WSL Linux environment is available on this host.

- Native amd64 and arm64 image build gates.
- Actual Linux userspace tailscaled startup without TUN and capabilities.
- Docker host-network loopback HTTP + mocked Tailscale lifecycle tests.
- Actual container PID1/SIGTERM, CapEff/UID and volume-recreation assertions.

These are provided in the manual-dispatch `funnel-test.yml` workflow and
`container_test.py`. The workflow has no publication step and no secrets.
It has not been pushed or dispatched as part of this validation.

## Still requires separate authorized live testing

- Authenticated public HTTPS Funnel -> userspace -> host-loopback origin.
- Public /mcp and /.well-known paths, Authorization/Host/method forwarding, SSE.
- Actual Tailscale identity/Funnel retention across restart and full host reboot.
- HAOS Supervisor/AppArmor behavior and simultaneous community Tailscale operation.
- Full production MCP/Auth0 integration with the new public hostname.

No real Tailscale credential or account was used to simulate this evidence.
Source-code inspection supports the origin connection design; it does not prove
the end-to-end runtime path. See README for the explicit manual acceptance gate.

**Publication decision: NO-GO until the native image-build/container gates pass.**
After those gates pass, a private test image can be considered for the remaining
live acceptance tests. Nothing has been published, installed on HAOS or merged.
