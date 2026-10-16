# Apple Music Browser

Experimental ARM64 Home Assistant OS add-on. Source is maintained in this repository;
GitHub Actions builds the image on a native ARM64 runner. No HAOS install or restart
is part of development or CI. The five existing add-ons are independent and unchanged.

Open the Home Assistant sidebar panel and sign in at music.apple.com with your normal
Apple account, including 2FA. There is no Apple Developer account, developer-token
setup, or MusicKit developer authorization flow. A subscription is required for full
tracks. A real click on Play may be needed after browser startup.

The pinned upstream epheterson/applemusic-mcp browser engine owns the visible browser
and its Playwright connection. noVNC presents that same browser; it is not a separate
browser experiment. The restricted MCP facade calls upstream browser functions for
song playback, transport, current track and volume. It does not import upstream's
full MCP server or expose playlist, library, catalog, authentication or queue tools.
No playlist operations are performed by the add-on or tests. Avoid playlist pages
and operations when using the unrestricted Apple website manually.

## Architecture and persistence

Only HA `aarch64` / Docker `linux/arm64` is supported. Debian with native Google Chrome,
Widevine, Python/Playwright, Xvfb, Openbox, x11vnc, websockify and nginx run in one
container. Chrome is launched by upstream in a container with its browser sandbox
disabled; HA add-on protection remains enabled. Do not browse unrelated websites.

The browser profile is `/data/browser/chrome`; credentials/config and cache are
`/data/config` and `/data/cache`. The complete `/data` directory survives container
replacement and restart and is included in normal add-on data backups. Treat backups
as account secrets. Apple may expire sessions and require login again; persistence
does not promise permanent authentication. Do not run a second Chrome on this profile.

Ingress listens on 8099 and accepts only Supervisor's ingress source `172.30.32.2`.
Static assets and WebSockets use relative URLs so HA's ingress prefix is retained.
VNC, websockify and CDP bind only to loopback and have no published ports.
MCP is removed from Ingress; its URL and credential do not depend on HA Ingress tokens.
The noVNC desktop continues to use HA authentication and the existing relative paths.

## Secure LAN MCP (0.1.2)

MCP is disabled by default. Configure the following in the add-on configuration UI:

- `mcp_enabled`: enable after configuring the remaining fields.
- `mcp_token`: a unique cryptographically random bearer secret, at least 32 ASCII
  characters without whitespace. Generate locally in a password manager; do not use
  your Apple password, HA access token or an Ingress token. The password field hides
  UI display; `/data/options.json` and backups still contain the secret.
- `mcp_certfile`: path relative to `/ssl`, default `apple-music/fullchain.pem`.
- `mcp_keyfile`: path relative to `/ssl`, default `apple-music/privkey.pem`.

Provision a TLS certificate and private key in those locations on HAOS. The certificate
must cover the hostname/IP used by the client and chain to a CA trusted by that client.
The add-on mounts `/ssl` read-only. Never share the private key. Restart after configuration,
secret rotation or certificate renewal. Missing/invalid credentials or TLS material fails
closed; no plaintext fallback exists. Disable MCP and restart to retain browser-only use.

Stable endpoint: `https://<HAOS-LAN-hostname-or-IP>:8765/mcp`. Change the host port in
HA's Network settings if 8765 conflicts; the internal container port remains 8765.
Only this TLS port is published. MCP binds `0.0.0.0` **inside the bridge-network container**,
which is necessary for HAOS Docker port forwarding and sibling add-on communication;
loopback would break both. No host networking, privileged mode or Supervisor API access
is required. A port mapping is not a LAN firewall: restrict access to trusted LAN/VPN
clients with your network firewall; do not forward this port to the Internet.

Every HTTP method requires `Authorization: Bearer <secret>`, including initialization,
notifications and subsequent tool calls. Secrets in query strings are rejected. Unknown
paths expose no UI. Access logging is disabled. Origin-bearing requests return 403;
use Inspector's local proxy or a server-side client, which sends no browser Origin.
There is no CORS, OAuth, dynamic registration, legacy `/sse` endpoint or unauthenticated
health endpoint. SDK hostname checks are disabled deliberately because LAN IPs and HA
addon DNS names vary; TLS hostname verification, bearer authentication and rejection of
all browser Origins form the boundary instead. Keep certificate verification enabled.

### Windows MCP Inspector

Install Node.js and run `npx @modelcontextprotocol/inspector` locally. For a private CA,
set `NODE_EXTRA_CA_CERTS` to its PEM certificate **before** starting Inspector (PowerShell:
`$env:NODE_EXTRA_CA_CERTS = 'C:\certs\lan-ca.pem'`). Trust the CA, not the private key;
do not disable TLS validation. Use the local Inspector proxy, select **Streamable HTTP**,
enter the stable HTTPS URL, and set its Authorization bearer token field to the secret
(or custom header `Authorization: Bearer <secret>` depending on Inspector version).
The Inspector proxy's own session credential is separate; retain its default protection.
Connect, initialize and list tools. In Inspector versions with a protocol-era setting,
select **legacy** (the 2025 initialize handshake, not legacy SSE). New 2026-only clients
without that handshake mode are incompatible. Direct browser mode is intentionally unsupported.

An Inspector configuration file can use the following shape; replace the placeholder
locally and keep the completed file private. Start with `npx @modelcontextprotocol/inspector
--config <path-to-private-file>` and choose the server in the UI:

```json
{
  "mcpServers": {
    "apple-music-browser": {
      "type": "http",
      "url": "https://<HAOS-LAN-hostname>:8765/mcp",
      "headers": {"Authorization": "Bearer <secret-entered-locally>"},
      "protocolEra": "legacy",
      "suppressNotificationStream": true
    }
  }
}
```

Inspector settings follow its [configuration documentation](https://github.com/modelcontextprotocol/inspector/blob/main/docs/mcp-server-configuration.md).
HAOS port publishing and internal DNS follow [HA app networking](https://developers.home-assistant.io/docs/apps/communication/)
and [configuration](https://developers.home-assistant.io/docs/apps/configuration/).

### Future Persoonlijke MCP HAOS bridge

No bridge changes are included here. It needs Streamable HTTP client support, custom
Authorization headers, and configurable CA trust. The portable configuration contract is:

```json
{
  "transport": "streamable-http",
  "url": "https://<HAOS-LAN-hostname>:8765/mcp",
  "headers": {"Authorization": "Bearer <from-bridge-secret-storage>"},
  "ca_file": "<client-path-to-trusted-CA.pem>"
}
```

This describes required settings, not a promise about the bridge's current config schema.
Use the same LAN hostname/port from that add-on, or Supervisor's actual addon hostname
(e.g. `<repository-id>-apple-music-browser`) and internal port 8765. Obtain the real hostname
from Supervisor; do not assume `localhost`, an example repository ID, a fixed container IP,
or that Docker's underscore-form container name is a valid TLS hostname. Internal DNS must
also be present in the certificate SAN if used. LAN URL avoids this extra certificate name.
If the future bridge lacks TLS trust/header support, that must be implemented there before
connection; do not weaken authentication here. The bridge separately manages any ChatGPT
exposure/authentication. ChatGPT cannot directly reach this private LAN endpoint.

### Protocol and validation

Requests are capped at 64 KiB with a 32-connection concurrency limit.
Pinned MCP Python SDK 1.30.0 (the current v1 security maintenance release) serves stateless Streamable HTTP with JSON responses.
Clients initialize normally, send `notifications/initialized`, and may call tools without
a session ID. Protocol versions 2025-03-26, 2025-06-18 and 2025-11-25 are tested.
GET/DELETE return authenticated 405 because no standalone SSE stream or session deletion
is needed. Do not require legacy SSE or server-initiated notifications/resumption.
Exactly four playback tools remain: `play_song`, `control`, `now_playing`, `volume`.
No playlist operations or broad upstream server imports are added.

CI generates ephemeral secrets privately and tests missing/wrong authentication on all
methods, origin rejection, TLS validation, initialize/version negotiation, SDK handshake,
ping/list/call, internal DNS and a real Docker-published host port from a sibling container.
It retains native ARM64 Widevine, ingress/noVNC, loopback VNC isolation and persistence tests.
This models HAOS bridge networking, not a real Supervisor installation or Windows test.

On-device verification after updating: configure TLS/secret, restart, connect from Windows
Inspector over the actual LAN URL, confirm missing/wrong tokens return 401, list the four
tools and check `now_playing`. Confirm VNC ports 5900/6080 are unreachable over LAN and
noVNC still opens through HA. Sign in normally, test a single song/transport/audio, restart
and confirm login persistence. Later test the bridge's CA trust/header configuration from
its own container. Account, audio, real HAOS routing, Windows Inspector and ChatGPT bridging
remain unverified until performed on the device.

## Audio and DRM limits

noVNC transports screen and input, **not audio**. Audio is sent to HA's audio service
through `/run/audio/pulse.sock` (`audio: true`), then the output selected in HA. It
will not play on the phone/computer viewing the panel. No AirPlay, Chromecast, remote
speaker routing, lossless quality or spatial audio support is promised.

Google Chrome's native ARM64 package is required. Bundled Playwright Chromium is not
a substitute: it lacks the Widevine support required for full protected tracks.
CI tests native architecture and actual EME MediaKeys creation and fails if unavailable.
Even that test cannot prove Apple's license server will grant playback: Apple account,
subscription, CDM compatibility, autoplay rules and HA audio output must be validated
on the target. CI uses no Apple credentials and performs no account or playlist calls.
A successful image build alone is not proof of full-track playback or audible output.

## Release gate

Main-branch push builds publish only after native ARM64 image and smoke tests pass. Pull request
builds validate without publishing. Account/HA audio limitations remain unverified
by account-free CI. Do not present an unverified DRM fallback as a working Apple Music player.

## 0.1.1 ingress correction

The standalone nginx configuration now includes Debian's MIME table. In 0.1.0,
CSS, ES modules and SVG icons returned HTTP 200 but `Content-Type: text/plain`,
preventing Chrome from loading the noVNC UI. Relative URLs were already correct;
no prefix rewriting or changes to the browser/MCP server are necessary.

Native ARM64 CI reproduces the old CSS response on a separate nginx listener,
then checks the fixed UI through `/api/hassio_ingress/<token>/` on a proxy at
Supervisor's source IP. It checks the redirect, CSS/JS/icon status, MIME and exact
package content, browser module loading, a rendered connected noVNC canvas, and
WebSocket RFB protocol data under the prefix. This models Ingress routing; it does
not install the add-on on HAOS or verify a real HA session, Apple login or audio.

## 0.1.2 release evidence

Pre-publication native ARM64 [validation run 38951675815](https://github.com/Deluka-BE/home-assistant-apps/actions/runs/38951675815)
passed the image build, authentication/configuration tests, TLS MCP tests through both
sibling DNS and Docker host publishing (including reconnection after restart), Widevine,
noVNC ingress and profile persistence. Publication remains gated by a fresh successful
main-branch build and the same tests. This is CI evidence, not an on-device HAOS or
Windows Inspector verification.
