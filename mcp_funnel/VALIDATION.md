# Validation record

## Catalog 0.1.3 (2026-11-03)

- Synced pinned upstream commit `7f39ed8006c15ef57b501b7da1bed3fe60783fbd`.
  Runtime, healthcheck, container tests, config and dockerignore match upstream
  Git blob IDs. Dockerfile differs only in corrected source URL/revision labels.
  The upstream tzdata install layer is retained exactly. No pip runtime dependency.
- Upstream test suite retained with catalog ROOT/ADDON paths adapted; only the
  absent upstream workflow test is explicitly skipped. 40 tests discovered,
  39 passed, 1 skipped, including real subprocess EOF/exit races and winter/summer
  Brussels offsets. Test dependency: PyYAML (see requirements-test.txt).
- Checked YAML/security options, version/source labels, pinned multiarch bases,
  Docker COPY inputs, Python syntax, container fixture compilation and diff whitespace.
- User reports both upstream CI workflows succeeded for this exact commit.
  This worker could not independently confirm main-triggered CI: the connected
  query only covers PR runs. No Docker executable is available here, so no local
  image build or container execution is claimed.
- No image field or MCP Funnel publishing workflow exists in this catalog. HA
  builds this source locally on install/update; no separate image/tag publication
  is pending. Successful installation still depends on the HA build.
- No HA installation, restart, live Funnel acceptance or other add-on change.

Earlier records below are historical and do not establish build/live acceptance
results for 0.1.3. The user explicitly authorized this catalog release based on
upstream CI success; authenticated HAOS acceptance remains separate.

## Catalog 0.1.2 (2026-11-03)

- Runtime and container checks are byte-for-byte copies from personal-mcp-gateway
  commit `cd4969934aa90334719f1ccf6690f6729e38e67d`, verified by Git blob IDs.
- Ran 33 upstream unit/mocked tests against this catalog copy: all passed.
  Only ROOT/ADDON paths were adapted in a temporary test copy; the upstream
  workflow test was excluded because that workflow is absent from this catalog.
- Validated catalog YAML, version/label/source consistency, pinned bases, Docker
  COPY inputs, Python syntax, container fixture compilation and `git diff --check`.
- Docker is unavailable in this worker. Native amd64/arm64 builds, container
  execution and authenticated public Funnel acceptance remain unverified.
- Catalog 0.1.2 is prepared for HA repository refresh and local source builds;
  there is no prebuilt `image` field or external image publication requirement.
  No Home Assistant installation/deployment or live configuration was performed.

Earlier records below are historical and do not establish native build or live
acceptance results for 0.1.2.

## Catalog 0.1.1 (2026-11-03)

- Runtime and container checks synced from personal-mcp-gateway commit
  `97fbc4e65c082468e2453f082a3d049a30c25ac5`; runtime is byte-for-byte identical.
- Ran 31 upstream unit/mocked tests against this catalog copy: all passed.
  Only the test path was adapted and the source repository's workflow test was
  excluded: that workflow is not present in this catalog.
- Validated YAML, version/label consistency, pinned bases, Python syntax and
  `git diff --check`.
- No Docker executable is available in this worker; native image builds and
  container gates were not run here. No live HA or Auth0 changes were made.
- The catalog has no Funnel `image` field or Funnel publication workflow.
  HA builds the add-on from local source during installation/update. After the
  catalog is refreshed from main, 0.1.1 should be offered; successful installation
  still depends on that build. No external image publication is pending.

The older validation record below is historical and does not establish native
build or live acceptance results for 0.1.1.

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
