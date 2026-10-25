# Weather Camera PoC — 0.1.1

One experimental Home Assistant add-on compares two CPU classifiers against the
**same single camera still**. It retains a bounded image history and analysis
records by default, and publishes `sensor.weather_camera_mobilenet` and `sensor.weather_camera_prithiv`
after each candidate finishes. Set `save_history: false` to disable collection.
See [DOCS.md](DOCS.md) for setup, limits, API, model provenance and validation.

The default camera is `camera.woonkamer_vloeiend`. Open the add-on's authenticated
Ingress interface for diagnostics and **Analyze Now**. First use needs internet
and downloads approximately 382 MB of model weights. This is a proof of concept;
Raspberry Pi 5 runtime and prediction quality have not yet been validated.
