# Apple Music Offline Diagnostics

Versie 0.1.0 — uitsluitend aarch64, bedoeld voor Raspberry Pi 5 met HAOS.
Deze app voert bij handmatig starten een in-memory MCP-zelftest uit en blijft
vervolgens wachten tot Stoppen. Verwachte logregel: `OFFLINE_SELF_TEST_OK`.
Geen Apple-login, credentials, muziekacties, webinterface of bereikbaar MCP-endpoint.

Installatie en updates via **Deluka’s Home Assistant Apps**: zie [DOCS.md](DOCS.md).
De runtime opent geen sockets; dit is geen containerfirewall. Installatie bouwt
lokaal en vereist internet voor de basisimage en Python-afhankelijkheden.

De zeven Python-modules en Dockerfile zijn ongewijzigd overgenomen uit de bestaande
`apple-music-mcp` offline distributie. Desktop/browsermodules zijn niet inbegrepen.
Deze voorbereiding is geen bewijs van een geslaagde Docker/aarch64- of HAOS-test.
