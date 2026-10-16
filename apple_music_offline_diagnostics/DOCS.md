# Installatie en updates

Deze stappen gelden nadat de beheerder versie 0.1.0 heeft gepubliceerd in
[Deluka’s Home Assistant Apps](https://github.com/Deluka-BE/home-assistant-apps).
Er is tijdens de voorbereiding niets gepubliceerd of op HAOS uitgevoerd.

1. Open **Instellingen → Apps → Appwinkel** (oudere UI: **Add-ons → Add-onwinkel**).
   Bestaande abonnees behouden de repository; voeg geen nieuwe repository toe.
   Kies via het menu **Controleren op updates / Check for updates**.
2. Zoek **Apple Music Offline Diagnostics** onder **Deluka’s Home Assistant Apps**.
   Alleen aarch64 is ondersteund; op amd64 wordt deze app niet aangeboden.
   Kies **Installeren**. De lokale Docker-build heeft internet en vrije schijfruimte
   nodig. Er is geen vooraf gebouwde image.
3. Laat **Starten bij opstarten** en **Watchdog** uit. Opties zijn leeg; voer geen
   Apple-gegevens in. Kies **Starten**, open **Logboeken** en verwacht binnen circa
   20–22 seconden na de Python-start `OFFLINE_SELF_TEST_OK`. De app blijft daarna
   rustig draaien. Kies **Stoppen** om af te sluiten.
4. Voor een latere versie: vernieuw de winkel, controleer versie en changelog,
   maak desgewenst een back-up en kies **Bijwerken** op de bestaande app. Controleer
   daarna opnieuw de logregel bij handmatig starten. De beheerder moet voor iedere
   release de versie verhogen; ongewijzigd 0.1.0 levert geen update op.

Heb je de lokale diagnose eerder geïnstalleerd? Stop en verwijder die lokale
installatie vóór installatie uit deze repository. Een lokale app en een app uit
een Git-repository hebben verschillende Supervisor-identiteiten; dit is geen
in-place migratie. Kopieer geen lokale appmap naar de catalogus op HAOS en voeg
`apple-music-mcp` niet toe als Supervisor-repository.

Niet zichtbaar: controleer repository, architectuur en winkelverversing; bekijk
Supervisorlogs voor manifestfouten. Build mislukt: controleer image-/packagebereik,
schijfruimte en buildlogs. `OFFLINE_STARTUP_FAILED`: stop en controleer of alle
bestanden bij dezelfde release horen. Voeg geen credentials of extra rechten toe.
Geen webinterface is verwacht. De zelftest bewijst geen Apple- of HA-integratie.

Docker/aarch64-build, Supervisor-installatie en signalen op echte HAOS zijn nog
niet getest. Python-dependencyranges en basisimagetag zijn geen volledige lock.
Windows-live-tests horen bij een afzonderlijke job met afzonderlijke toestemming
voor live acties; zij zijn niet nodig voor deze offline runtime.
