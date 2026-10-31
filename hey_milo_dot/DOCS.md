# Milo Dot Alpha 0.1.0

Eerste experimentele persistente Lex-worker. Geen chat-UI, spraak, wake-up of planning.

## Configuratie

Vul via de add-onconfiguratie bridge_endpoint (HTTPS), bridge_token en bridge_ca_pem (volledig vertrouwd PEM-certificaat) in. Vul tokens nooit in chat, broncode of logs in. Certificaat en hostnaam worden altijd gecontroleerd. De Bridge moet bereikbaar zijn vanaf Home Assistant. Het certificaat moet overeenkomen met het opgegeven IP-adres of de hostnaam.

Gwen blijft standaard uit. Vul gwen_endpoint pas in als Qwen via een gecontroleerde verbinding bereikbaar is. Een Windows localhost-adres is vanuit deze add-on niet bereikbaar.

De database staat onder /data/state/jobs.sqlite3 en hoort bij de add-ondata/back-up. Herinstalleren met verwijderen van data wist de wachtrij. Worker draait na initialisatie als UID 10001.

## Gebruik en grenzen

De alpha verwerkt reeds in zijn database ingevoerde Bridge-jobs. De CLI in /opt/milo-dot ondersteunt create-bridge, list, events en review. Er is nog geen gebruikersinterface/API voor het invoeren vanuit Home Assistant. Resultaten blijven review_pending; Gwen-advies geeft geen autorisatie. Geen HA-beheerrechten, Supervisor API of hostnetwerk vereist.

## Installatie

Werk de bestaande add-oncatalogus bij in Home Assistant en installeer Milo Dot Alpha. Home Assistant bouwt de container lokaal uit deze broncode. De eerste installatie op een echte Supervisor is nog niet getest.
