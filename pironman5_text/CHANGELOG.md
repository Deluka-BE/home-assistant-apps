# 0.2.0 — 2026-09-12

- font_size 8, 12, 16 of 24; maximaal 4, 3, 2 of 1 tekstregel.
- Negen zelfgetekende pictogrammen, naast tekst of zelfstandig.
- Animaties none, blink, pulse en spin (fan/spinner), circa 1 beeld per seconde.
- Bestaande API-berichten zonder extra opties blijven werken.
- Duidelijke HTTP 400-fouten voor ongeldige opties; actieve inhoud blijft behouden.
- 18 lokale tests met Pillow en het originele OLED-lettertype; hardwaretest nog nodig.

# 0.1.1

Herstelt hassio_api: true en hassio_role: manager, zoals de originele
SunFounder-add-on. De bestaande statusbibliotheek gebruikt de Supervisor-API
voor network/info. Zonder deze toegang mislukte JSON-verwerking en daarna
het tekenen van het standaardscherm. Hardwarebevestiging na update is nog nodig.

# 0.1.0

Eerste testversie met tijdelijke en blijvende OLED-tekst via HTTP.
