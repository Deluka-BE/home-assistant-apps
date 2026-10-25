# Weather Camera PoC — 0.1.0

One experimental Home Assistant add-on compares two CPU classifiers against the
**same single camera still**. It does not create weather entities or retain images.
See [DOCS.md](DOCS.md) for setup, limits, API, model provenance and validation.

The default camera is `camera.woonkamer_vloeiend`. Open the add-on's authenticated
Ingress interface for diagnostics and **Analyze Now**. First use needs internet
and downloads approximately 382 MB of model weights. This is a proof of concept;
Raspberry Pi 5 runtime and prediction quality have not yet been validated.
