# Changelog

## 0.2.23

- Add optional read-only `nodered_read_logs` with bounded JSONL reads, UTC/node/flow filters, and safe pagination.
- Mount Home Assistant `/share` read-only and add disabled-by-default log-reader settings with explicit source allowlist.
- Preserve all existing MCP tools and authentication. Node-RED debug-file producer requires a separate update and configuration.
- Published image is `ghcr.io/deluka-be/hevy-personal-mcp:0.2.23` from source `75c89deac206ad179ef6a27a8a5eb997c31e910f`.

## 0.2.22

- Add read-only Home Assistant tools `ha_get_system_log`, `ha_list_statistics` and `ha_get_statistics`.
- Reuse the existing authenticated Home Assistant WebSocket transport for bounded, sanitized system log and Recorder statistics reads.
- Keep the existing Home Assistant configuration schema and permissions unchanged.

## 0.2.21

- Add read-only `ha_observe_raw` with a bounded BLE adapter for short live raw-observation windows.
- Require an address and/or name filter, cap observation at 1–5 seconds and 1–100 results, and distinguish initial cache replay from live callback updates.
- Preserve BLE manufacturer/service data, RSSI, scanner source, UUIDs and optional raw packet hex with explicit semantics, redaction and completeness/truncation metadata.
- Reuse the existing authenticated Home Assistant WebSocket transport; no GATT reads, writes, service calls, active-scan requests or new Home Assistant permissions are added.
- Published as immutable multi-arch image `ghcr.io/deluka-be/hevy-personal-mcp:0.2.21` from source revision `1d6d2005b5df565c0d560d7213aac8b7d19e65ad`.

## 0.2.20

- Add read-only Home Assistant debugging tools `ha_get_entity_history` and `ha_get_logbook`.
- Bound history/logbook reads to explicit offset timestamps, at most 24 hours per request, 1–10 entity filters, and a local 1–500 record limit with explicit completeness/truncation metadata.
- Reuse the existing Supervisor Core read-only transport and safe projection rules; no new Home Assistant permissions, write capabilities, auth layers, or governance are added.
- Published as immutable multi-arch image `ghcr.io/deluka-be/hevy-personal-mcp:0.2.20` from source revision `b38d16fcd97440805223712f5792764357c2bd14`.

## 0.2.19

- Add the Lex v2 MCP Events callback stack for owner-bound reserved Codex jobs: native Events discovery/subscription, verified signed webhook delivery, durable terminal ingress, deduplication, retries and restart recovery.
- Add `codex_reserve_job`, `codex_start_reserved_job` and `codex_notification_status` while preserving the existing direct Codex submission flow.
- Automatically provision private persistent Events storage on HAOS, including a reusable Fernet key, without requiring a new user-entered secret.
- Keep callbacks bound to the originating verified subscription; no conversation IDs, broadcasts, or surprise delivery to old chats are introduced.
- Published as immutable multi-arch image `ghcr.io/deluka-be/hevy-personal-mcp:0.2.19` from source revision `6da49179a1e1b8a86b4bd120c036deb277cf2455`.

## 0.2.18

- Refine Lovelace projection diagnostics so actual dashboard content omission is distinguished from truncation of the diagnostic manifest itself.
- Add explicit `content_omitted` and `manifest_truncated` status flags plus `manifest_entries_omitted` counting, while preserving the legacy `omitted` and `entries_omitted` aliases.
- Keep `projection_complete` tied to policy redactions or actual content omissions; manifest truncation alone no longer marks the projected dashboard content incomplete.
- Preserve the existing pagination, discovery, detection and strict `complete` semantics introduced in 0.2.17.
- Published as immutable multi-arch image `ghcr.io/deluka-be/hevy-personal-mcp:0.2.18` from source revision `4f05e6438b0c7618d84676603b1031eec172ea45`.

## 0.2.17

- Make dashboard read completeness explicit instead of overloading one boolean.
- Add `pagination_complete`, `projection_complete`, `discovery_complete` and `detection_complete` where applicable.
- Add deterministic projection status/counts so redactions and size-driven omissions can be distinguished from pagination.
- Keep the existing `complete` field backward-compatible as the strict combination of all applicable completeness dimensions.
- Preserve conservative caveats for custom cards, strategy-generated content and dynamic entity discovery.
- Published as immutable multi-arch image `ghcr.io/deluka-be/hevy-personal-mcp:0.2.17` from source revision `38a791ac86cf13b448ea05a13830e77134412799`.

## 0.2.16

- Add read-only Lovelace dashboard discovery with `ha_list_dashboards`.
- Add sanitized, paginated dashboard configuration reads with `ha_get_dashboard`, preserving lossless private snapshots for future safe editing.
- Add `ha_get_dashboard_entity_usage` for structural entity-reference discovery with optional labeled heuristic matches.
- Use the Supervisor Core WebSocket proxy with a fixed read-only Lovelace command allowlist; no dashboard writes, service calls, templates, arbitrary RPC or extra HAOS permissions are enabled.
- Correct completion semantics so `complete` accurately reflects whether the current dashboard/entity-usage query is fully represented, while retaining caveats for dynamic/custom-card content.
- Published as immutable multi-arch image `ghcr.io/deluka-be/hevy-personal-mcp:0.2.16` from source revision `5ad2cfef59647e381853abad57ce5ea79be6aec0`.

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
