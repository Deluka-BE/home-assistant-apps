# Pironman 5 OLED tekst — testversie

Stop de originele Pironman-add-on (inclusief watchdog en automatisch starten)
voordat je deze start. Er mag maar één hardwarebesturing actief zijn.

Deze versie voegt `/api/v1.0/set-oled-text`, `clear-oled-text` en `get-oled-text`
toe op poort 34001. Gebruik voor Node-RED de hostnaam van DEZE add-on.

Voorbeeld POST naar set-oled-text:
`{"lines":["Hallo!"],"duration":30}` met Content-Type application/json.

Nieuw in 0.2.0: voeg `font_size` (8/12/16/24), `icon` en `animation` toe.
Bijvoorbeeld `{"icon":"heart","animation":"pulse","duration":30}`.
De animaties wisselen ongeveer één keer per seconde van beeld.
Zie [VISUALS.md in de bronrepository](https://github.com/Deluka-BE/pironman5-oled-text/blob/main/VISUALS.md) voor alle opties en voorbeelden.

Zie [LEESMIJ.md in de bronrepository](https://github.com/Deluka-BE/pironman5-oled-text/blob/main/LEESMIJ.md) voor installatie, instellingenovername,
Node-RED, testresultaten en terugschakelen. Tien lokale softwaretests geslaagd; een eerste gebruiker heeft tekstweergave
en terugkeer naar het standaardscherm op een Pi bevestigd. Verdere hardwaretests blijven nodig.
