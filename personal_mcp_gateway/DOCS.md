# Persoonlijke MCP HAOS

Deze add-on biedt twee afzonderlijke Auth0-beveiligde MCP-routes:

- `/mcp`: Hevy, iCloud Calendar en Spotify; geen `codex_*`-tools.
- `/codex/mcp`: uitsluitend `codex_submit_job`, `codex_get_job`,
  `codex_get_job_events`, `codex_get_job_result` en `codex_cancel_job`.

Beide routes gebruiken dezelfde OAuth-resource/audience (`PUBLIC_BASE_URL/mcp`)
en dezelfde publieke Funnel. De Codex-VM is niet rechtstreeks publiek
bereikbaar; verzoeken lopen via de bestaande beveiligde Bridge.

Vul de add-onopties in via de Home Assistant-configuratie. De catalogusversie
verwijst naar een al gepubliceerde image met een onveranderlijke versietag;
historische tags worden niet overschreven.
