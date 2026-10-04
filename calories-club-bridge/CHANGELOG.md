# Changelog

## 0.4.4

- Herstel de add-onstart: de tijdstempellogger zit nu zelfstandig in de
  add-onstarter en is dus altijd beschikbaar.

## 0.4.3

- Alle bridge-logregels krijgen een tijdstip in Europe/Brussels.
- Voeg een Home Assistant-add-onpictogram en logo toe.

## 0.3.0

- Toegevoegd: beveiligde, alleen-lezen `GET /tools`-route om de exacte
  Calories Club MCP-toolnamen en invoerschema's te inspecteren vóór een
  entry-update wordt uitgevoerd.

## 0.2.1

- Als Recordo een refresh token definitief intrekt, start de bridge automatisch
  een nieuwe Device Authorization-flow.
- `GET /status` toont tijdens die flow de tijdelijke verificatie-URL en code,
  zonder tokens of client secrets te tonen.
- Meerdere gelijktijdige mislukte requests delen één nieuwe loginflow.

## 0.2.0

- Eerste Home Assistant-appversie.
- Ondersteuning voor `aarch64` en `amd64`.
- Persistente OAuth-state in `/data`.
- Externe HTTP-poort 3107, zonder Ingress.
