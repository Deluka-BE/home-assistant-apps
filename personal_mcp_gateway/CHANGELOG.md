# Changelog

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
