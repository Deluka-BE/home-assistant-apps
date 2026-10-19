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
- `nodered_list_logs` en `nodered_read_logs` (alleen wanneer ingeschakeld)

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

## Voorbereiding optionele Apple Music MCP

Deze opties verbinden de bestaande Apple Music MCP via de Gateway vanaf versie `0.2.29`.
Activeer de koppeling pas nadat de nieuwe image is gepubliceerd en geïnstalleerd.

- `apple_music_mcp_enabled`: optionele schakelaar, standaard `false`.
- `apple_music_mcp_url`: URL van de Apple Music MCP-server, standaard leeg.
- `apple_music_mcp_token`: toegangstoken, standaard leeg. Het schema `password?`
  maskeert de waarde in de interface; dit is geen versleuteling.
- `apple_music_mcp_ca_file`: optioneel pad naar een expliciet vertrouwd TLS-certificaatbestand,
  standaard leeg. Gebruik een persistent pad dat binnen de add-on leesbaar is,
  bijvoorbeeld `/share/certs/apple-music-ca.pem`; `/share` is uitsluitend-lezen
  gekoppeld. Zorg dat de runtimegebruiker (`10001:10001`) de mappen kan doorzoeken
  en het bestand kan lezen, ook na vervanging van het certificaat.

Maak vóór elke update een lokale back-up van de geïnstalleerde versie,
add-onopties en add-ondata, en leg de geïnstalleerde image-digest vast voor
herstel. Bewaar deze back-up veilig: opties kunnen tokens bevatten.

## Gateway-logdiagnostiek (vanaf 0.2.30)

De add-on logt bij het opstarten de configuratiebron, of Apple Music ingeschakeld is,
of URL/token ingesteld zijn en of het opgegeven publieke TLS-certificaat
beschikbaar en leesbaar is. Alle nieuwe Gateway- en providerdiagnostiek heeft
een tijdstempel en logniveau. Bij Apple Music-discovery toont het log de status
(`disabled`, `connected` of `failed`), een veilige foutcategorie en het aantal
beschikbare upstream-tools. Identieke meldingen worden maximaal eenmaal per
minuut herhaald. Tokens, certificaatinhoud en upstream-foutdetails worden niet gelogd.

## Optionele Node-RED JSONL-logreader

Standaard uitgeschakeld. De MCP-add-on koppelt `/share` uitsluitend-lezen.
Stel `node_red_log_directory` in op de gedeelde logmap (standaard
`/share/nodered-logs`) en schakel `node_red_log_reader_enabled` pas in nadat
de producer en bestandsrechten gecontroleerd zijn. De gateway maakt geen map
of logs aan en wijzigt geen rechten. Private Node-RED `/data` is niet zichtbaar.

`nodered_list_logs` toont begrensde metadata voor reguliere `.log`/`.jsonl`
bestanden; gebruik `filename` in `nodered_read_logs`. Paden, symlinks en
niet-reguliere bestanden worden geweigerd. Oude `node_red_log_sources`
JSON-maps en `sourceId` blijven werken; `filename` en `sourceId` zijn exclusief.
Berichtwaarden worden standaard weggelaten; producer-redactie blijft nodig.

De gateway draait als numerieke UID/GID `10001:10001` zonder extra groepen.
Die identiteit moet de map kunnen doorzoeken/lezen en de bestanden lezen,
ook na logrotatie. Ontbrekende bestanden en geweigerde rechten geven vaste,
padvrije foutcodes. De gateway leest uitsluitend en voert geen retries uit
bij bestandswijzigingen; herstart paginering bij rotatie of een gewijzigde log.

## Optionele LoopData (vanaf 0.2.31)

Vanaf `0.2.31` bieden `loopdata_list_running_sessions` en
`loopdata_get_running_session` op `/mcp` optionele leestoegang, standaard
uitgeschakeld (`loopdata_enabled: false`). Activeer pas na installatie. De verwachte interne host is `core-mariadb:3306`;
runtimeconnectiviteit is nog niet geverifieerd.

Gebruik uitsluitend de SELECT-only reader `loopdata_reader` en vul gebruikersnaam
en wachtwoord privé in via `loopdata_username` en `loopdata_password`.
De vaste bron is `loopdata.running_workouts`, met bronfilter `apple_health`.
Stel `loopdata_host` in; `loopdata_port` is standaard `3306` en
`loopdata_timeout` standaard `10` seconden (1–30). Host, gebruikersnaam,
wachtwoord en `loopdata_ca_file` zijn standaard leeg.

`loopdata_tls_mode` is standaard `verify`; gebruik zo nodig `loopdata_ca_file`
voor een vertrouwd CA-bestand dat binnen de add-on leesbaar is.
Kies `off` uitsluitend expliciet voor een vertrouwde lokale HAOS-verbinding als
de server geen TLS ondersteunt. Er is nooit een automatische TLS-downgrade.

Opslag gebruikt UTC; datumfilters gebruiken `Europe/Brussels`.
Resultaten bevatten samenvatting, kilometergegevens, hartslag en herstel met
expliciete dekking en aantallen weggelaten gegevens; geen ruwe JSON, GPS of weer.
Bronrevisie: `fbeadbc140ec75d5431d94b0298e3d0fcc7512f3`; volgens de
bronvalidatie zijn 573 gatewaytests geslaagd. Rollbackversie: `0.2.30`.
