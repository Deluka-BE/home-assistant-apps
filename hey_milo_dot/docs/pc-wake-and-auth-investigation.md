# [PROJECT:hey-milo-dot] PC wake en reconnect-onderzoek

## Wake-controle

`milo_dot.pc.ensure_pc_awake` leest eerst uitsluitend
`binary_sensor.pc_waker_pc_bereikbaar`. `on` geeft `already_awake` zonder puls.
`off`/`unavailable` reserveren één poging in SQLite, waarna nogmaals gelezen
wordt en uitsluitend `button.pc_wakker_maken` via `button.press` wordt bediend.
Andere/ongeldige sensorstatussen falen gesloten. Polling gebruikt 1, 2, 4,
maximaal 5 seconden wachttijd, standaard maximaal 90 seconden inclusief
requests (elk maximaal 10 seconden binnen het resterende budget).

Resultaten bevatten `ok` en `status`: `already_awake`, `awake`, `timeout`,
`ha_read_error`, `wake_action_error` of `interrupted`. Alleen de eerste twee
laten Bridge-verzoeken toe. De worker controleert ook vóór status/resultaat-
requests voor bestaande jobs. Een mislukte controle zet een nog niet verstuurde
job terug in de wachtrij; een bestaande remote ID blijft behouden. De foutstatus
staat in het `pc_readiness`-event. SIGTERM/SIGINT onderbreken de wachttijd van de
daemon; lopende requests blijven begrensd.

De globale `pc_wake_attempt`-reservering overleeft herstarts en geldt voor alle
jobs/workers met dezelfde database. Crashes, timeouts en onzekere wake-fouten
leiden tot verder lezen zonder automatisch opnieuw pulsen. Waargenomen `on`
verwijdert de reservering voor een volgende slaapcyclus. Bij blijvend `off`
moet een operator eerst HA/pc controleren. Alleen na expliciete toestemming
voor opnieuw proberen en met alle workers gestopt kan de operator
`DELETE FROM pc_wake_attempt` uitvoeren in de betreffende state-database.
Gebruik bij meerdere workers dezelfde database; afzonderlijke databases bieden
geen onderlinge deduplicatie. Een crash vlak vóór de puls kan bewust tot een
geblokkeerde poging leiden; veiligheid krijgt voorrang op automatisch herhalen.

## Minimale rechten en configuratie

Standaard blijft wake uit en blijven bestaande flows werken. Optioneel krijgt
het externe Bridge-JSON een `pc_wake`-object met `endpoint`, `token_file` en
optioneel `ca_file`. Bestanden moeten absolute paden buiten de repository zijn.
Voor de add-on zijn de equivalente opties `pc_wake_endpoint`, `pc_wake_token`
en `pc_wake_ca_pem`; provision schrijft private bestanden onder `/data/state/config`.
Een vertrouwde gateway-CA is voor de add-on verplicht. Geen secrets in deze doc.

**Voorwaarde vóór live inschakeling:** het HTTPS-endpoint moet een gateway zijn
met een eigen beperkt credential. Die gateway mag uitsluitend doorlaten:

- `GET /api/states/binary_sensor.pc_waker_pc_bereikbaar`
- `POST /api/services/button/press` met exact het JSON-object
  `{"entity_id":"button.pc_wakker_maken"}` (geen extra targets of velden).

Alle overige paden, methoden en bodies moeten geweigerd worden. Geen redirects.
Gebruik geen algemeen HA-beheertoken als workercredential. De client heeft zelf
ook een vaste allowlist, maar dat begrenst niet de rechten van een gestolen
credential. Een gateway met serverzijdige autorisatie wordt niet door deze repo
geleverd; provisioning daarvan is een externe resterende stap. Supervisor-
`homeassistant_api`, `hassio_api`, netwerk- en hostrechten blijven ongewijzigd.
De wake-integratie is daarom gereed voor configuratie, maar hier niet live
geactiveerd of getest.

## Auth0 / ChatGPT-MCP na reconnect

Onderzocht: alle broncode, tests, voorbeelden, manifest en documentatie in deze
checkout via zoektermen `auth0`, `oauth`, `refresh.token`, `chatgpt`, `mcp`,
`session`, `login`. Alleen een algemene ChatGPT-vermelding in `docs/design.md`
werd gevonden. Deze repo bevat geen MCP-server, OAuth-callback, Auth0-client,
autorisatie-URL of refresh-tokenopslag. `milo_dot.bridge.Bridge` leest per request
een bearercredential uit een extern bestand en gebruikt `/v1/submit`, `/v1/get`,
`/v1/result`; er is geen interactieve login of reconnect-authenticatiepad.
`ha_config.py` schrijft uitsluitend externe workerconfiguratie en credentials.

Daarom is een repo-oorzaak niet aangetoond en is geen auth-wijziging gedaan.
Vermoedelijke onderzoekslocatie: de externe MCP/OAuth-gateway waar ChatGPT
verbindt, diens persistente OAuth-grant/tokenopslag en de bijbehorende Auth0-
applicatieconfiguratie. Mogelijke oorzaken die nog bewijs vereisen zijn verloren
sessie/grantopslag na procesherstart, een mislukte refresh of een geforceerde
interactieve authorization request. Dit onderzoek heeft geen toegang tot die
componenten of hun configuratie.

Veilige vervolgstap: laat de beheerder daar één reconnect volgen met alleen
correlatie-ID, tijdstippen en geschoonde OAuth-foutcodes. Controleer of de grant
herstartbestendig opgeslagen is, of refresh plaatsvindt en welke foutcode Auth0
teruggeeft; controleer ook de gegenereerde authorization parameters op geforceerde
login. Deel geen tokens, cookies, authorization codes of volledige callback-URLs.
Wijzig scopes, MFA, consent of sessiebeveiliging niet zonder vastgestelde oorzaak.

## Omgeving

Origin gecontroleerd: `git@github.com:Deluka-BE/hey-milo-dot.git`.
Bestaand ongecommit werk behouden. `/workspace/AGENTS.md` gelezen;
`/home/philip/.codex/AGENTS.md` bestaat niet in deze Bridge-worker. De hostpolicy
kon daardoor niet worden gelezen. Geen live HA-mutaties, credentialwijzigingen,
commit, push of deployment uitgevoerd.

## Validatie in deze worker

- `python -m unittest discover -s tests -q`: 54 tests, geslaagd.
- `python -m compileall -q milo_dot tests`: geslaagd.
- `git diff --check`: geslaagd (alleen tracked wijzigingen); nieuwe Python-code
  is daarnaast gecompileerd en via unittest uitgevoerd.
- Docker is niet geïnstalleerd in deze worker; containerbuild niet uitgevoerd.
- Tests gebruiken mocks en lokale SQLite; geen live HA- of Auth0-aanroepen.
