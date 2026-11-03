# Changelog

## 0.1.2

- Sync runtime and container checks from `Deluka-BE/personal-mcp-gateway`
  commit `cd4969934aa90334719f1ccf6690f6729e38e67d`.
- Suppress transient startup error categories when Tailscale becomes ready within
  the 30-second grace period; retain sanitized timeout and later diagnostics.
- Align container assertions with successful startup suppression.
- Update catalog version, Docker version label and upstream source revision.

## 0.1.1

- Sync runtime and container checks from `Deluka-BE/personal-mcp-gateway`
  commit `97fbc4e65c082468e2453f082a3d049a30c25ac5`.
- Reuse authenticated Tailscale state and wait through transient startup failures.
- Enroll only on explicit `NeedsLogin`, once per supervisor lifetime; apply node
  settings independently of login and prefix logs with UTC timestamps.
- Update catalog version and Docker version label together. This add-on is built
  from the catalog's local source; no prebuilt image publication is required.
