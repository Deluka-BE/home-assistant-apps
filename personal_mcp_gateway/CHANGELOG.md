# Changelog

## 0.2.15

- Route Personal Codex tooling to the Linux Lex v2 bridge on the existing private HTTPS bridge path.
- Add bounded completion waiting to `codex_get_job` with optional `wait_seconds` (0-25) to reduce repeated polling.
- Keep research/review text-only, implementation repository-scoped, existing certificate pinning, idempotency and fail-closed behavior.
- Add a safe internal terminal-event projection for future MCP Events delivery; automatic ChatGPT wake-up is not enabled yet.
- Published as immutable multi-arch image `ghcr.io/deluka-be/hevy-personal-mcp:0.2.15` from source revision `4dbe6e71145394eccafefebd090da5020014c07d`.

## 0.2.12

- Add the first read-only Home Assistant integration to the Personal MCP Gateway.
- Add `ha_list_entities`, `ha_get_entity_state` and `ha_get_instance_info`.
- Enable the Home Assistant Core API permission for the add-on with `homeassistant_api: true`; all other Supervisor/API permissions remain disabled.
- Restrict Home Assistant access to fixed GET routes for `config`, `states` and `states/<entity_id>` through the Supervisor Core proxy; no services, writes, templates, WebSocket access or Supervisor management are included.
- Published as immutable multi-arch image `ghcr.io/deluka-be/hevy-personal-mcp:0.2.12` for amd64 and aarch64 from source revision `9af8d923fb09a1cedf8f79141de34a4b42a0067e`.

## 0.2.11

- Add the first read-only Node-RED integration to the Personal MCP Gateway.
- Add `nodered_list_tabs`, `nodered_get_flow_snapshot`, `nodered_get_inventory`, `nodered_get_runtime_summary` and `nodered_get_diagnostics`.
- Add Home Assistant add-on options for `node_red_base_url`, `node_red_username`, `node_red_password` and `node_red_timeout`.
- Keep Node-RED access limited to fixed GET routes; no deploy, inject, flow mutation or arbitrary HTTP proxy is included.
- Published as immutable multi-arch image `ghcr.io/deluka-be/hevy-personal-mcp:0.2.11` for amd64 and aarch64.

## 0.2.5

- Fix CalDAV mutation authentication: guarded mutation GET/PUT/DELETE requests now use the configured authentication.

## 0.2.4

- Safely delete exactly one recurring CalDAV occurrence using EXDATE and one conditional PUT; remove only its matching detached exception.
- Preserve original DATE, UTC, floating and TZID recurrence identity and all unrelated resource bytes; reject ambiguous or unsupported cases without writing.
- Keep standalone deletion, authentication and guarded transport behavior unchanged. No single-occurrence update support or configuration schema changes.
- Validation: 305 tests passed, including 36 occurrence deletion regressions; amd64/arm64 container checks and publication verification passed.
- Source revision: `543a7bd0609efb85101ef3eebc8393258e41299a`.

## 0.2.3

- Add `calendar_update_event` and `calendar_delete_event` for standalone CalDAV events with guarded ETag-based writes.
- Expand Spotify MCP coverage to the current Spotify Web API capabilities available to Development Mode apps, including library, playback, metadata, playlist management and cover uploads.
- Preserve all existing Hevy, Calendar, Codex, Auth0, Funnel and Spotify tools/configuration.
- No Home Assistant configuration schema changes.


## 0.2.2

- Use the public MCP endpoint `/mcp` as the Auth0 API audience.
- Keep OAuth protected-resource metadata, Auth0 audience and JWT resource validation aligned on the same `/mcp` URL.
- Verified OAuth discovery and unauthenticated MCP challenge behavior.
- Verified linux/amd64 and linux/arm64 images.
- Existing Home Assistant configuration fields remain unchanged.

## 0.2.1

- Stable Home Assistant update version for the verified OAuth discovery fix.
- Exact retag of the previously verified multi-arch OAuth-fix image.
- No configuration changes; existing Hevy, Auth0, CalDAV, Codex and Spotify options remain valid.

# Changelog

## 0.2.0-test-oauth-mcp-938d7f2-1

- Fix MCP OAuth protected-resource discovery for the public `/mcp` endpoint.
- Preserve the existing Auth0 API audience while advertising the correct MCP resource URL.
- Verified private multi-arch GHCR image for linux/amd64 and linux/arm64.
- Hevy, iCloud Calendar / CalDAV, Codex bridge and Spotify remain included.

## 0.2.0-test-spotify-d342904

- First repository-managed Home Assistant release.
- Hevy support.
- iCloud Calendar / CalDAV support.
- Codex bridge support.
- Spotify provider support.
