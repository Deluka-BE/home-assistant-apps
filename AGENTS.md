# home-assistant-apps — Project instructions

## Scope
This file defines project-specific rules for `home-assistant-apps`. It starts from the default project policy; extend it when project-specific constraints are established. Do not duplicate or weaken central rules.

## Shared instructions and paths
- VM-level architecture, project registration, security and GitHub workflow are defined centrally in `/home/philip/.codex/AGENTS.md` on the VM host.
- Lex workers run in an isolated container. This repository is mounted at `/workspace`; this file is `/workspace/AGENTS.md` inside the worker. Do not require worker access to host `/home/philip/...` paths.
- The Bridge selects the repository from the leading `[PROJECT:home-assistant-apps]` marker and the central registry. Do not infer alternative project paths.
- Follow central safety, authorization and release requirements; project-specific additions must not weaken them.

## Project-specific rules
- For every Lex job targeting this repository, the task MUST start with the exact leading marker `[PROJECT:home-assistant-apps]`. Conversation/project labels such as `home-assistant-music-addon` are not Lex aliases and must never be copied into the marker. Before every `codex_submit_job`, verify the alias against the central project registry instead of guessing from chat context.
- If Lex returns a generic `codex_bridge_request_failed`, first re-check the leading marker and registered alias before diagnosing Bridge transport or authentication.
