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
browser experiment. The MCP facade calls upstream browser functions for playback and uses the upstream
platform-independent amp_api module for playlist and library management. The macOS
AppleScript engine is not used. Integration tests never mutate a real Apple account.
Playlist mutation tools act only when explicitly called. Playlist deletion additionally
requires confirm=true. Reordering tracks is not exposed because amp_api does not
implement it.

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

## Secure LAN MCP (0.1.3)

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
Version 0.1.7 exposes 40 MCP tools. Playback tools `play_song`, `control`,
`now_playing` and `volume` are no longer registered; their implementations remain
in the code. Browser settings tools remain available.
Playlist and library tools use the pinned cross-platform AMP module; no broad upstream server imports are used.

CI generates ephemeral secrets privately and tests missing/wrong authentication on all
methods, origin rejection, TLS validation, initialize/version negotiation, SDK handshake,
ping/list/call, internal DNS and a real Docker-published host port from a sibling container.
It also verifies disabled MCP has no listener, noVNC still works, and Chrome shuts down
cleanly with its profile intact. It retains native ARM64 Widevine, ingress/noVNC,
loopback VNC isolation and persistence tests.
This models HAOS bridge networking, not a real Supervisor installation or Windows test.

On-device verification after updating: configure TLS/secret, restart, connect from Windows
Inspector over the actual LAN URL, confirm missing/wrong tokens return 401, list the 40 tools, confirm the four playback tools are absent, and check
`local_preferences`. Confirm VNC ports 5900/6080 are unreachable over LAN and
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

## 0.1.4 phase 1: playlist workflows

Three additional MCP tools use the existing pinned upstream AMP session, with no
AppleScript or macOS dependency. The add-on image remains ARM64 only.

- `search_playlist_tracks(playlist_id, term, limit=25)` searches only the specified
  existing playlist, using case-insensitive title/artist substring matching. It reads
  all pages before returning up to 100 matches in playlist order, including 1-based
  positions, relationship IDs, library IDs and catalog IDs where available.
- `copy_playlist(playlist_id, name, description="")` creates a new playlist and copies
  its tracks in source order, in batches of 100. Catalog songs use catalog IDs;
  uploads/unmatched library songs use typed library-song IDs. Missing usable IDs
  fail before creation. The new description is caller-supplied; folders and other
  source metadata are not copied. Empty playlists are supported. The final sequence
  and count are checked. Apple may normalize duplicates/order or reject unavailable
  tracks; a mismatch is reported as failure rather than silently claimed as success.
- `replace_playlist_track(playlist_id, relationship_id, replacement_catalog_id)`
  requires a unique original relationship and an ASCII numeric catalog replacement.
  It adds the replacement, reads every page again, and requires the original sequence
  plus one appended replacement before deleting the original relationship. Existing
  replacement songs are allowed only when an additional appended occurrence is
  verified. Missing/duplicate originals and same-song replacement are rejected.
  Removal is verified with another complete read.

Search read failures raise MCP tool errors, including authentication, throttling,
malformed data and later-page errors. These workflows use strict reads because the
pinned upstream `get_tracks` can silently return empty or partial results on failure.
Existing tools retain their upstream behavior. Library IDs are never treated as
catalog IDs; deletion uses the original relationship ID, never the replacement ID.

Mutation results contain `ok`, `stage`, and `error` on failure. Copy also returns
`playlist_id` and `submitted_tracks` (acknowledged submissions, not a verified final
count). No automatic rollback deletes a partial copy or removes an added replacement.
Inspect the reported playlist before retrying a failed operation: an API timeout can
occur after Apple has accepted a write. Replacement reports `removal_attempted`;
failures before that flag becomes true retain the original. Once DELETE is sent,
a timeout or verification failure means the removal outcome is uncertain, so retaining
the original cannot be guaranteed. There is no transactional API.

Writes through this facade are serialized within one process. Concurrent edits from
Apple clients or other processes cannot be locked; detected changes abort replacement
before removal. An external edit between verification and deletion remains possible.
Upstream removal uses `mode=all`, so repeated original relationship IDs are rejected
before any write. Replacement appends at the end; restoring the original position
would require unsupported reorder operations. There is no destructive rebuild fallback.
AMP may reject writes to Music.app-created, smart, or curated playlists; no browser
mutation fallback or universal playlist editability is claimed.

Mocked unit tests cover pagination, error propagation, scoped search, mixed ID types,
batching, empty/partial copy, add/verify/remove sequencing and failure retention.
CI also checks the three new tool registrations over MCP. No real account, HAOS,
Windows client, or live playlist mutation was used for development validation.

## 0.1.5 phase 2: migration matching and read-only discovery

This is a staged release, not completion of every migration operation. Twelve new
read-only tools bring the MCP registration count to 31. Existing playback and
playlist tools retain their 0.1.4 behavior. The new tools use the public Apple Music
API routes already used by the pinned upstream server, with `amp_api._headers()`:
the captured web-player token, captured music-user-token and music.apple.com Origin.
They do not prefer a generated developer token, import upstream's macOS server,
launch a browser, or change authentication/settings. The upstream pin remains
[`6226528`](https://github.com/epheterson/applemusic-mcp/tree/6226528a37e8fa7e0ec72e3e96dd280bef20d532).

| New tool | Supported operation and boundaries |
| --- | --- |
| `resolve_isrc(isrcs, storefront="us")` | 1..100 ISRCs, deduplicated and normalized for case/hyphens; batches of 25. Returns every unique catalog candidate per ISRC, explicit `missing` and `ambiguous` lists. No automatic release selection. |
| `catalog_identifiers(identifiers, content_type="songs", storefront="us")` | Batch song, album or artist details for 1..100 ASCII numeric IDs. Deduplicates inputs and reports missing IDs. Includes raw Apple metadata, such as duration, ISRC, album, content rating, play parameters and relationships when supplied. This is also the single-item details tool. |
| `album_tracks(album_id, storefront="us", limit=25, cursor="")` | One page of catalog album tracks in Apple's order. |
| `artist_discovery(artist_id, view="top-songs", storefront="us", limit=25, cursor="")` | Exact artist ID; `top-songs` or `similar-artists` view where available. No first-name-search-hit matching. |
| `catalog_genres(storefront="us", limit=25, cursor="")` | One catalog genre page. |
| `catalog_suggestions(term, storefront="us")` | Up to 10 search-term suggestions. These are not migration matches. |
| `browse_library(content_type="songs", limit=25, cursor="")` | Library songs, albums, artists or music-videos; library IDs remain distinct from catalog IDs. |
| `library_history(kind="recently-added", limit=25, cursor="")` | Recently added library resources or `recently-played` tracks where Apple supplies history. No locally reconstructed history. |
| `music_recommendations(kind="recommendations", limit=10, cursor="")` | Recommendation groups or `heavy-rotation`; preserves nested contents and relationships. Group count is not song count. |
| `music_charts(content_type="songs", storefront="us", limit=25)` | Songs, albums or music-videos; explicitly first page only, retaining chart metadata and nested next links. Continuation is not exposed. |
| `account_storefront()` | Authenticated `/me/storefront`, with no preference mutation. |
| `session_diagnostics()` | One storefront probe, or cached throttle result. Returns API accessibility and sanitized error; no tokens, paths, browser cookies, settings dump or audit history. Does not prove playback/subscription capability. |

Catalog storefront is explicit and defaults to `us`, matching upstream's default;
it is **not inferred from the account**. Read `account_storefront` and pass the
returned country ID for migration matching. Catalog availability differs by country.
One ISRC can identify several releases; compare all candidates' album, duration,
explicit status and availability before choosing a catalog ID. A successful empty
lookup is different from a failed lookup. Input duplicates make no extra requests.
These reads are idempotent with respect to account state, but Apple's results can change.

Paged tools return `{data, next}` with raw Apple resources. Except recommendations
(1..10), page limits are 1..25. Pass the returned `next` as `cursor` with the same
tool arguments to request the next page. Continuations must use the exact API host,
route, filters and page size and an increasing numeric offset; unexpected cursor
formats fail closed. Relationship pagination inside a returned resource is retained
but not automatically followed. Catalog ID and ISRC matching follow top-level pages
to completion, with a maximum of 100 pages per batch. If a later page or batch fails,
the call raises an error and returns no partial match/missing report.

New HTTP reads have a 30-second upstream timeout, reject redirects and never retry.
401/403 report authorization/access problems; 404 reports resource/endpoint
unavailability, not an empty library or un-rated item; 429 reports throttling and
records the public API rail. Recent throttling on that rail prevents further probes.
Other failures and malformed JSON/data also raise tool errors. Raw HTTP bodies,
headers and exception strings are not included in errors. An unsupported region,
subscription, endpoint or session may prevent a particular read; mocked coverage
does not establish live support for every account. Existing upstream wrappers still
have their original error handling; these stronger guarantees apply to the new tools.

### Explicit remainder and unsupported operations

| Requested operation | Status at 0.1.5 (superseded by 0.1.6 below) |
| --- | --- |
| Library add song/album | Deferred to the next mutation stage. Upstream `_add_to_library_api` supplies the route; needs bounded batching, idempotency and ambiguous-outcome tests before exposure. |
| Remove library item with confirmation | Deferred. Pinned AMP verifies **song** removal by library ID only. Generic album/artist library deletion is not established and must not be invented. |
| Delete playlist folder with confirmation | Deferred. AMP supplies `delete_folder`; root-folder protection, confirmation, child handling and uncertain-outcome behavior still need implementation/tests. |
| Ratings/favorites | Deferred web love/dislike/clear and rating reads; AMP supplies `rate` and `get_rating`, but `get_rating` currently conflates failures with unrated. Five-star ratings and the native favorites listing are macOS-only upstream and unsupported here. Do not equate a rating write with a verified favorites-list API. |
| Safe settings/audit diagnostics | Only minimal session/API accessibility is delivered. Settings inspection, an allowlisted audit summary and audit recording/redaction policy are deferred. No raw audit/token/config files are exposed. |
| Charts continuation / nested relationship traversal | Not exposed in this stage. First-page charts are explicitly labeled. |
| Fuzzy migration matching, UPC or Spotify ID conversion | Not exposed. Use ISRCs or known Apple catalog IDs; missing ISRCs need a separately reviewed metadata matcher. |
| Native operations | No AppleScript, native star ratings, native snapshots, playlist reorder, unsupported generic delete, or fabricated endpoints. |

Capability inspection covered upstream `amp_api.py` (web-session operations) and
`server.py` (catalog resolve/details, library browse/history/add, discovery and
ratings). The upstream server mixes API and native behavior, so it is deliberately
not imported by this facade. New routes are implemented as small strict reads rather
than reusing formatting helpers that can suppress errors or select the first match.

The earlier release report claimed 30 account-free unit tests, including every new tool, real
FastMCP schema registration, existing playlist workflows and TLS/auth configuration.
Coverage includes ISRC ambiguity/batching, missing IDs, later-page failures, unsafe
cursors, non-advancing pagination, redirects, 401/403/404/429/500, malformed responses,
sanitized diagnostics and version agreement. That historical local claim was not
reproducible in an independent host environment: discovery reported 29 tests with
two import errors because Starlette/Uvicorn were absent. It must not be treated as
a green host test result. Version 0.1.6 adds an explicit dependency preflight and
runs verification in a disposable Debian dependency environment instead.
Native ARM64 build, network MCP,
Widevine/ingress smoke tests and publication remain CI gates; they were not run in
the development sandbox, which has no Docker. CI checks both release and commit image
tags and refuses overwrite or an inconclusive registry response. No account mutations,
HAOS installation, Windows live test or bridge changes are part of this release.

## 0.1.6: remaining supported web operations

Thirteen additional tools bring registration to **44 tools**. All routes come from
the pinned upstream `amp_api.py`, `_add_to_library_api` and folder children reads;
the native upstream server is never imported. Apple documents the public
[ratings API](https://developer.apple.com/documentation/applemusicapi/ratings-api);
this facade follows upstream's captured-session AMP transport for ratings.
The add-on remains ARM64 only; the web operations themselves have no macOS dependency.

| Tool | Exact support |
| --- | --- |
| `add_library_catalog(catalog_ids, content_type="songs", storefront="us")` | 1..100 ASCII numeric catalog IDs, deduplicated, songs or albums only. Fresh complete catalog validation in the explicit storefront must find every ID before a single POST to public `/me/library` with `ids[songs]` or `ids[albums]`. No fuzzy match, hidden batch loop, automatic retry or rollback. Adding an album can add its tracks. |
| `remove_library_song(library_song_id, confirm=false)` | Exactly one `i.*` library song ID, never a catalog ID or batch/query selector. Requires JSON boolean `confirm=true`. Sends one AMP DELETE for that song. Removal can affect playlists containing that library song; there is no undo. |
| `delete_playlist_folder(folder_id, confirm=false)` | Exactly one `p.*` folder ID; root `p.playlistsroot` is forbidden. Requires JSON boolean `confirm=true` and a successful children response with empty `data` and no continuation. Nonempty, partial, malformed or unavailable children reads prevent DELETE. Move children individually first. No recursive delete, move or cascade fallback. |
| `web_rating(catalog_id, content_type="songs")` | Exact song/album catalog ID. Successful empty `data` means unrated; one valid rating means love (+1) or dislike (-1). Auth, network, 404 and malformed responses remain errors rather than becoming unrated. |
| `set_web_rating(catalog_id, action, content_type="songs", confirm=false)` | Actions `love`, `favorite`, `dislike`, `clear`. `favorite` is an explicit alias for the upstream +1 love write, not a separate favorites-list guarantee. Clear requires JSON boolean `confirm=true`; love/favorite/dislike overwrite the prior web rating when explicitly called. No native stars. |
| `web_auth_diagnostics()` | One strict AMP playlists probe (`limit=1`), or cached throttle response. Complements public-API `session_diagnostics`. Reports access/error only; no credential claims, tokens, headers, cookies, private paths or subscription/playback guarantees. No login reset. |
| `browser_settings_info()` | Current web-player volume/shuffle/repeat via upstream browser settings. May initialize the signed-in player. |
| `set_browser_settings(volume=-1, shuffle=null, repeat=null)` | Volume 0..100 (-1 leaves it), boolean shuffle, repeat `none`/`one`/`all`. Uses the same browser owner as playback. No native settings, audio output routing or quality selection. |
| `local_preferences()` | Only facade cache TTL, entry count, capacity and process-local scope. Never upstream config or account preferences. |
| `set_local_preferences(cache_ttl_seconds)` | Opt in to 0..300 seconds of catalog metadata caching; default 0 (off). Clears existing entries. Preferences reset on process restart and do not change storefront, engine, login, auto-add or content filtering. |
| `cached_catalog_identifiers(identifiers, content_type="songs", storefront="us")` | Same bounded catalog validation/metadata as `catalog_identifiers`, with optional cache keyed by explicit storefront, resource type and ordered deduplicated IDs. Returns `cached` and `age_seconds`. At most 128 entries; expired entries are removed, returned values are detached copies, and failures are never cached. This cache is not consulted for write validation. |
| `clear_catalog_cache(confirm=false)` | JSON boolean `confirm=true` clears only the facade's memory cache. No filesystem deletion, browser-profile clearing, logout, credential changes, upstream-cache clearing or audit deletion. |
| `audit_summary(limit=20)` | Last 1..100 events for the thirteen new tools in this process. Bounded to 100 fixed tool names, sequence numbers, timestamps and `returned`/`error` outcomes. No arguments, IDs, metadata, results, error strings, secrets or existing upstream audit files. Not a durable account history. Schema rejection before tool dispatch is not recorded. |

The new account writes reject redirects, respect per-rail throttling, use the
upstream 30-second timeout and captured origin-bound web headers, and never retry.
They do not call upstream error formatters that log HTTP bodies. Errors are sanitized
and never return raw transport exceptions or response bodies. Successful write
results contain `acknowledged=true`, HTTP `status`, and `verified=false`: even 202 is
only acceptance, not proof of completed synchronization. Invalid JSON/API errors in
a successful response remain failures. A timeout or failed response may occur after
Apple applied the change. Inspect account state before repeating any write; there
is no automatic cleanup or rollback. Repeating a request is never offered as an
exactly-once operation. Catalog availability checks do not prove library completion.

New library/folder/rating writes share the existing process write lock. Other Apple
clients can still edit concurrently. In particular, an external edit between the
empty-folder check and DELETE cannot be locked or conditionally protected by this
API. Only delete a folder while other clients are not changing it. The facade never
intentionally deletes a populated folder; it cannot guarantee Apple's concurrent
child behavior. If the children route is unsupported for an account, deletion fails
closed. An attempted write through any new write tool clears the facade catalog
cache, including on failure. Existing 0.1.5 tools retain their original behavior;
their calls are outside the new audit scope.

Unsupported: generic library album/artist deletion, bulk or whole-library deletion,
nonempty/recursive folder deletion, native 1..5 star ratings, favorites listing or
verification of a favorites collection, native snapshots/reorder, Music.app and
AppleScript, native engine preferences, account settings mutation, durable audit
history/preferences, raw settings/audit files, upstream track/export/cache cleanup,
login/logout/reset, token renewal controls, chart continuation and nested relationship
traversal. No speculative endpoints or automatic fallback to native/UI writes.

### Dependency-aware verification and release

`tests/run_unit.py` checks installed upstream 0.20.3 and MCP 1.30.0 and explicitly
imports Starlette, Uvicorn, Playwright and the upstream AMP/browser modules before
discovering **all** `test_*.py` files. CI first runs `pip check`, then this runner
inside the built add-on image. Missing dependencies/import failures fail the gate;
there are no skip-on-import checks. The Dockerfile still installs upstream at commit
`6226528a37e8fa7e0ec72e3e96dd280bef20d532` and MCP 1.30.0.

Development verification uses a disposable **Debian trixie, Python 3.13** container
with that exact upstream commit and full dependency installation, including
Starlette/Uvicorn/Playwright; the musl worker cannot install the upstream Playwright
distribution and is not used to claim green unit tests. This Debian x86_64 test is
dependency-equivalent to the image's Python environment, not an ARM64 image/browser
or HAOS test. The final 0.1.6 development run passed **49 tests with no errors or
skips**, plus `pip check`; installed Starlette 1.8.0, Uvicorn 0.54.0 and Playwright
1.64.0 imported successfully. Coverage includes failed/ambiguous writes, redirects,
HTTP/auth/throttle failures, malformed responses, catalog preflight refusal,
confirmation including SDK coercion rejection, folder/root safeguards, bounded
cache isolation/expiry and audit redaction. These are mocked account operations,
not proof of live Apple behavior. The same runner can be invoked as:

```sh
PYTHONPATH=apple_music_browser /path/to/venv/bin/python apple_music_browser/tests/run_unit.py
```

The network MCP smoke test now asserts all 40 registrations and checks invalid
library IDs, unconfirmed destructive calls, root-folder deletion and safe local
diagnostics without making any Apple account request. Full native ARM64 build,
Widevine, ingress, sibling-DNS/published-port TLS MCP tests and publication remain
the main-push CI gates. Version and commit image tags retain the existing fail-closed
overwrite protections. No HAOS installation, real account mutation, Windows live
test or bridge modification is included.


## 0.1.7: playback MCP registrations removed

Removed only the registrations for `play_song`, `control`, `now_playing` and
`volume`, reducing the exposed MCP list from 44 to 40 tools. Their function bodies
and underlying browser implementation remain intact. All other registrations,
including `browser_settings_info` and `set_browser_settings`, remain available.
Registration and network checks assert the count and reject calls to these four
unregistered names.

The user-supplied blue Apple/circuit artwork is pending transfer from the ChatGPT
attachment. No replacement has been generated or installed. Transfer the exact
original 1254 × 1254 RGBA PNG with transparency to
`apple_music_browser/icon.png` and `apple_music_browser/logo.png` in this repository
(use identical original bytes for both). These asset files are not included in
this code-only release.

Version 0.1.7 development validation passed all 49 mocked unit tests with no
failures or skips, dependency preflight and `pip check` in a disposable Debian
trixie x86_64/Python 3.13 container using the pinned upstream revision and MCP
1.30.0. An AST comparison confirmed that the server differs only by the four
removed registration decorators. Native ARM64/browser and sibling-container TLS
network smoke tests remain CI checks; they were not run in this development test.

### Spotify Discover Weekly source reader (phase 1)

`read_spotify_discover_weekly(playlist="37i9dQZEVXcT3aus8zyY7o")` is a read-only
MCP tool on the existing authenticated MCP interface. Supply an explicit playlist
ID or `https://open.spotify.com/playlist/ID` to override the known default. It has
no Apple Music writes, sync, scheduling or new authentication configuration.

The source reader uses Spotify's public embed HTML hydration data, specifically
an ordered `trackList` in a matching playlist entity. This is not a documented
Spotify playlist-data API contract. A changed or unsupported schema fails closed;
there is no browser-scrolling fallback. Only a bare 22-character ID or an exact
HTTPS `open.spotify.com/playlist/ID` URL is accepted. Query parameters are ignored;
credentials, ports, fragments, encoded paths and whitespace are rejected. The
reader constructs the embed URL internally and never fetches the supplied URL.

Each call makes two independent uncached, cookie-free TLS reads, separated by
750 ms. Redirects, non-HTML content and unexpected compression are rejected.
Requests use a project User-Agent, identity encoding, 5-second socket timeouts,
a 15-second socket deadline and a streamed 2 MiB body cap. A guarded daemon
worker bounds caller waiting to 16 seconds even if system DNS stalls; at most
one transport worker can remain outstanding, blocking further fetches until it
finishes. A late result is discarded. JSON script count,
bytes, traversal depth and visited nodes are bounded. One reader runs at a time;
failed requests are paced too. No browser/profile access or new dependency is used.

The known `__NEXT_DATA__` entity path is preferred. If absent, only JSON script
payloads are searched for a matching playlist owning the recognized `trackList`
array. Contradictory known entities or conflicting matching candidates fail closed.
The playlist must retain the Discover Weekly name. Non-30 counts (1..1000) are
accepted with warning metadata; empty or unreasonable collections fail. Source
order and duplicate occurrences are preserved, with positions assigned 1..N.
Track URIs, IDs, titles, artist credits and any duration are validated. Unplayable
tracks remain in the result with a warning. Structured artists are preferred;
a subtitle is preserved as one credit in `artists`, with
`artists_source="display_credit"` indicating that it is not artist segmentation.
Optional `duration_ms` remains compatible with downstream resolution.

Success requires matching normalized ordered snapshots and returns
`completeness_basis="embedded_tracklist"`: this does not claim an independently
verified total. The version-2 SHA-256 fingerprint covers canonical source identity
and ordered normalized tracks, excluding timestamps and diagnostics. Failures
return `source_incomplete`, `source_unstable` or `source_busy` without tracks or
fingerprint. Diagnostics contain only reason codes and structural metadata.
No authentication inference is made from HTTP failures.

Pass returned metadata records to `check_discover_weekly` in batches of at most 30;
larger source collections do not
change the enrichment tool's batch limit. Offline fixture tests cover parsing,
validation, snapshot stability, transport limits and safe failures. Live embed
schema compatibility should be checked before release; this implementation does
not authorize or perform a release.

### Read-only Spotify ISRC enrichment

`lookup_spotify_isrc(tracks)` accepts 1..30 Spotify track IDs or HTTPS
`open.spotify.com/track/ID` URLs. Query parameters and fragments are stripped.
Results retain input order and repeated positions; network lookups are deduplicated
within the call, including linked MusicBrainz recording lookups.

MusicBrainz is the only provider: exact canonical Spotify URL relationships lead
to recording ISRCs, with no title/artist search fallback. Each result includes
`spotify_id`, `canonical_url`, `isrcs`, `recordings` (MusicBrainz IDs, title, artist
credit, duration and disambiguation where available), `status` and `reason`.
Statuses are `resolved` (one distinct ISRC), `missing` (no URL, recording relation
or ISRC), `ambiguous` (multiple distinct ISRCs, all retained), or `error` (invalid
input or provider failure). Provider errors never become missing matches or partial
resolved results. A resolved status describes MusicBrainz data, not a verified
cross-service match. MusicBrainz coverage may be incomplete or incorrect.

The module uses public unauthenticated GET requests with a meaningful User-Agent,
a process-wide request limiter (at least 1.05 seconds between request starts),
5-second connect / 15-second read timeouts, no redirects and no retries. It bounds
relationship fan-out to 100 recordings per track; exceeding that returns an error.
No Spotify Web API, browser scraping, credentials or paid providers are involved.

Callers can pass track IDs from `read_spotify_discover_weekly()` to this tool, then
pass validated ISRCs to the existing `resolve_isrc(isrcs, storefront)` tool to obtain
Apple Music catalog candidates. Review title, artists, duration and version/release
metadata before selecting any candidate. Neither enrichment nor resolution writes
playlists, schedules synchronization or selects among conflicting ISRCs.

### Read-only Discover Weekly matching

`check_discover_weekly(tracks, storefront="be")` accepts 1..30 source-reader
track records with title, artists and duration_ms. It enriches via
`enrich_deezer_isrc(tracks)`, then checks Apple ISRC candidates before strict
Apple catalog search for missing or metadata-incompatible matches. Conflicting
multi-ID ISRC results remain ambiguous without a metadata-search override.
MusicBrainz lookup remains available independently and is no longer a gate.

Deezer uses public HTTPS `/search` and `/track/{id}`, no credentials, no redirects, a
5-second connect / 10-second read timeout, and at most 300 candidates per query.
Search uses up to three pages of 100 with explicit original-query/index params;
upstream next URLs are never followed. Completion must cover the highest reported
total with no continuation; overlapping or malformed pages fail closed.
Queries use full title plus all artists, then primary artist, then title alone;
queries broaden only after empty results, never after metadata rejection.
Requests are serialized at least 250 ms apart; 429, 5xx and network failures
activate a 30-second cooldown with no automatic retries. Diagnostics expose
fixed statuses/reasons, not upstream bodies or links.

Acceptance requires identical normalized title words (including every version
qualifier), equal artist-credit sets, and duration within 2000 ms. Normalization
ignores case and punctuation; credit separators are conservative. Only candidates passing title/version and duration checks fetch track details (at most
300 unique IDs per search). Full contributor credits are deduplicated without role
matching; malformed details or conflicting IDs, titles, durations or ISRCs fail closed.
Apple combined artist credits use the same conservative separator parsing. Different
ISRCs remain ambiguous. Multiple Apple IDs collapse only when every candidate has
one identical validated ISRC, equal normalized title/version and full artist-credit
sets, and durations within 2000 ms of both the source and each other. ISRC lookup
also requires that code to equal the requested ISRC. Missing codes or any conflicting
candidate prevent collapse. The lowest numeric catalog ID wins (lexical ID breaks
numeric ties), independent of API order. Such rows report
`reason="equivalent_catalog_releases"` and sorted `equivalent_catalog_ids`.
Unique matches retain their existing behavior, including metadata fallback for
Apple ISRC misses. Truncated results are not selected.
This deliberately leaves spelling variations, incomplete credits and unavailable
track-detail ISRC fields unresolved rather than inferring a match.

The report preserves every input position and duplicate, includes enrichment
status and matching method, and exposes `desired_catalog_ids` only if all rows
resolve. No playlists are read or mutated, and no sync operation is introduced.
Apple service failures are explicit errors, not catalog misses. Existing write
tools remain separate. Fixture tests do not establish live 30/30 coverage.
