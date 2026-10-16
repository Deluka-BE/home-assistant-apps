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
VNC, CDP and MCP backend ports bind to loopback; no host ports are exposed. Streamable
HTTP MCP is available at the panel's authenticated ingress base plus `/mcp`. Ingress
requires an HA session; this is not a stable unauthenticated LAN MCP endpoint or an
automatic HA media_player integration. External MCP clients need a valid HA ingress
session and access to that URL. The browser controls remain available in the panel.

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

Branch builds validate without publishing. Release publication must remain disabled
until native ARM64 image and smoke tests pass and the account/HA audio limits are
reviewed. Do not present an unverified DRM fallback as a working Apple Music player.
