# Persoonlijke MCP HAOS

Deze add-on biedt twee afzonderlijke Auth0-beveiligde MCP-routes:

- `/mcp`: Hevy, iCloud Calendar, Spotify, read-only Node-RED en read-only Home Assistant; geen `codex_*`-tools.
- `/codex/mcp`: uitsluitend `codex_submit_job`, `codex_get_job`,
  `codex_get_job_events`, `codex_get_job_result` en `codex_cancel_job`.

De Node-RED-integratie is uitsluitend read-only en biedt:

- `nodered_list_tabs`
- `nodered_get_flow_snapshot`
- `nodered_get_inventory`
- `nodered_get_runtime_summary`
- `nodered_get_diagnostics`

Voor de Home Assistant Node-RED add-on kan de directe lokale route worden gebruikt,
bijvoorbeeld `http://192.168.0.3:1880`. Gebruik hiervoor bij voorkeur een aparte
lokale Home Assistant-gebruiker zonder beheerderrechten. Vul die gegevens in via
`node_red_username` en `node_red_password`. De bridge gebruikt alleen vaste GET-routes;
deploys, injects en andere Node-RED-wijzigingen zijn niet beschikbaar in deze versie.

De Home Assistant-integratie is eveneens uitsluitend read-only en biedt:

- `ha_list_entities`
- `ha_get_entity_state`
- `ha_get_instance_info`

Home Assistant-toegang loopt intern via de Supervisor Core proxy met de door Supervisor
geïnjecteerde token. De add-on gebruikt alleen vaste GET-routes voor `config`, `states`
en `states/<entity_id>`. Service-calls, writes, templates, WebSocket-toegang en Supervisor-
beheer zijn niet beschikbaar. Er hoeft geen aparte Home Assistant-URL of token in de
add-onconfiguratie te worden ingevuld.

Beide publieke MCP-routes gebruiken dezelfde OAuth-resource/audience (`PUBLIC_BASE_URL/mcp`)
en dezelfde publieke Funnel. De Codex-VM is niet rechtstreeks publiek
bereikbaar; verzoeken lopen via de bestaande beveiligde Bridge.

Vul de add-onopties in via de Home Assistant-configuratie. De catalogusversie
verwijst naar een al gepubliceerde image met een onveranderlijke versietag;
historische tags worden niet overschreven.
