# MCP Funnel (experimental, not published)

Independent HAOS add-on: public Tailscale HTTPS 443 -> host loopback
`http://127.0.0.1:8000`. The existing Gateway 0.2.0, Render, Auth0 and community
Tailscale add-on are not modified by this package.

## Design and security

- Tailscale v1.102.4 and Python base images are pinned by multiarchitecture digest.
- Python standard library supervisor; no pip dependencies or extra reverse proxy.
- Userspace `tailscaled --tun=userspace-networking --port=0`.
- Own machine hostname `mcp-funnel`; actual allocated DNS name is read from status.
- Own state, certificates and socket in `/data/tailscale`; no shared volumes.
- No TUN, requested NET_ADMIN/NET_RAW/SYS_ADMIN, API access, host DNS changes,
  subnet routes or exit node. No local TCP 443 publication.
- Host networking is necessary to reach the existing published host port 8000.
- A short root bootstrap creates/chowns only its own data directory and drops to
  UID/GID 10002 before starting any Tailscale process. CHOWN/SETUID/SETGID are
  ordinary bootstrap capabilities, not additional Supervisor privileged options.
  Main process, daemon and CLI then have no effective Linux capabilities.
  Healthcheck also drops privileges before contacting the local socket.
- Root bootstrap never reads/rewrites state or recursively changes other data.
- Docker build context is allowlisted to two runtime files and the Dockerfile.

The community Tailscale add-on stays independent with `share_homeassistant: disabled`.
Never copy its state, hostname identity or socket into this add-on. There is no
attempt to reuse `homeassistant.tail1e9b9e.ts.net`.

## First login (when installation is authorized separately)

Start the add-on, open its private HAOS log and follow the official
`https://login.tailscale.com/a/...` link. The add-on does not accept an auth-key,
write tokens to options or ship credentials. The temporary login link is sensitive:
do not share the log while it is usable. All other CLI/daemon output is suppressed;
only allowlisted login URLs and fixed diagnostic messages are printed.

Enable tailnet MagicDNS, HTTPS certificates and Funnel permission for this machine
in Tailscale as needed. The add-on does not edit policy itself. Keep the machine
registration valid (review key expiry for unattended operation). Login is reused
from `/data`; expired/revoked identity requires browser authorization again.

## Startup and recovery

The controller installs SIGTERM/SIGINT handlers, prepares private storage, starts
tailscaled and waits for its LocalAPI. It runs `tailscale up --json` with hostname,
DNS/route acceptance disabled and netfilter off. Authentication waits do not block
signal handling. Disconnected nodes wait without state reset.

Gateway readiness requires exact HTTP 200 at `http://127.0.0.1:8000/health`.
Redirects and environment HTTP proxies are disabled. Offline Gateway is retried
every five seconds, without reauthentication or restarting the container.

Once connected and origin-ready, read `tailscale funnel status --json`. A correct
persistent HTTPS 443/root proxy is kept. Missing or wrong root proxy is configured
with `tailscale funnel --bg --yes --https=443 --set-path=/ http://127.0.0.1:8000`.
The subsequent status check must prove the stored configuration. Unknown JSON
fields/additional handlers cause degraded readiness rather than assumed success;
this minimal controller does not delete extra handlers or reset node state.
Permission/network/configuration errors are retried, with generic diagnostics.

The configured background Funnel may resume as soon as tailscaled reconnects on
restart, even before the startup origin check succeeds. While Gateway is offline,
requests may receive an upstream error. Preserving Funnel without `off` means the
origin check cannot be a guarantee against this temporary availability condition.

On shutdown terminate the CLI children, then tailscaled, wait up to 15 seconds,
and kill only processes that fail to exit. Never invoke logout, down, reset or
funnel off. State and Funnel survive container replacement when `/data` is kept.
Uninstalling the add-on may delete its data: back up if reusing the identity later.

## Health and limitations

Local healthcheck: daemon LocalAPI, Running + Self.Online, Gateway HTTP 200,
exact persistent Funnel configuration. No public requests. First login/disconnection
can legitimately be unhealthy; Docker health alone does not automatically restart
the add-on. The controller exits if its daemon dies; enable HAOS watchdog only
through a separately reviewed health strategy, not a restart loop on login waits.

The Gateway public URL/Host allowlist and Auth0 audience/discovery must eventually
match the new hostname for full MCP use. This package does not change them. `/health`
does not demonstrate working OAuth, `/mcp`, discovery, or SSE.

## Verification (no credentials in automated tests)

From repository root:

```sh
python -m unittest discover -v
docker buildx build --platform linux/amd64 --load -t mcp-funnel:test homeassistant/mcp-funnel
python homeassistant/mcp-funnel/container_test.py mcp-funnel:test
```

Repeat the build and container test on a native arm64 Linux runner with
`--platform linux/arm64`. `.github/workflows/funnel-test.yml` provides both native
matrix jobs, manual dispatch only, read-only repository permission and `push: false`.
No registry login, credentials, GHCR publication or production changes.

`container_test.py` distinguishes a real unauthenticated userspace daemon test
from mock CLI/daemon lifecycle tests. The latter use a real host-loopback HTTP
server, verify capability dropping, SIGTERM, late Gateway readiness and preserved
state across container recreation. They DO NOT prove public Funnel forwarding.

### Mandatory manual gate, after separate authorization

There is no authenticated Funnel end-to-end test without a Tailscale identity.
Do not invent credentials or claim a mock has tested the encrypted public path.
On an authorized disposable Linux test machine (or later on HAOS):

1. Run a host test HTTP server on 127.0.0.1:8000 providing /health (200), /mcp,
   /.well-known/test and an SSE endpoint with timed events.
2. Run this real image with host networking and a dedicated persistent /data.
3. Authorize the disposable `mcp-funnel` machine through its log URL; allow HTTPS/Funnel.
4. From outside its tailnet, verify HTTPS requests reach each origin path; check
   Host/Authorization/method preservation and that SSE arrives progressively.
5. Restart/recreate the container with the same /data and reboot the test host;
   verify identical node ID/domain and working Funnel without a new login.
6. On HAOS additionally verify AppArmor, Supervisor startup, graceful stop and
   simultaneous operation with the community Tailscale add-on.

Until the build/container gates succeed: NO-GO for publication. A private test
image, once those gates pass, can be used for this explicitly still-unproven manual
gate; it is not a production-readiness claim.

## Official references

- https://developers.home-assistant.io/docs/apps/configuration/
- https://tailscale.com/docs/reference/tailscaled
- https://tailscale.com/docs/reference/tailscale-cli/up
- https://tailscale.com/docs/reference/tailscale-cli/funnel
- https://github.com/tailscale/tailscale/blob/v1.102.4/ipn/ipnlocal/serve.go
- https://github.com/tailscale/tailscale/blob/v1.102.4/net/tsdial/tsdial.go

The reverse proxy uses SystemDial for origin connections; host networking makes
that connection use HAOS loopback. This source verification is separate from the
mandatory authenticated end-to-end test above.
