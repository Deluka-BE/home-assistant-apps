# Weather Camera PoC

## Local installation and access

This directory is self-contained and discoverable by Supervisor through its
`config.yaml`; no repository metadata changes are needed. For a local add-on,
copy this directory into Home Assistant's `/addons/weather_camera_poc`, refresh
the local add-on store, then build/install it manually. These are operator steps;
this implementation task does not install, deploy or publish anything.

The add-on advertises aarch64 (including Raspberry Pi 5) and amd64 and builds on
`python:3.11-slim-bookworm`, a glibc multi-architecture image. It uses ordinary
Docker init, not S6. All named direct Python dependencies are pinned; the base
image tag and transitive dependencies are not digest/lock pinned yet.

Start the add-on and use **Open Web UI** (Ingress). Port 8099 is internal only;
there is no host port mapping. Requests must come from Supervisor's documented
Ingress peer `172.30.32.2`; forwarded headers cannot override that check. Never
expose Flask directly. Ingress provides authentication. The lightweight Flask
server is intended for this single-user PoC; production needs a bounded WSGI
server and deployment-specific load testing.

`homeassistant_api: true` supplies Core API access using `SUPERVISOR_TOKEN`.
The fixed API origin is `http://supervisor/core/api/`. No user token, browser,
cookies, camera access token, external camera URL or MQTT credentials are used.
No secrets or exception payloads are logged. Child model workers do not inherit
the Supervisor token. MQTT discovery/sensors are deliberately deferred for v0.1.

## Options and scheduling

| Option | Default | Behavior |
| --- | --- | --- |
| `camera_entity` | `camera.woonkamer_vloeiend` | Lowercase `camera.` entity, letters/digits/underscores only |
| `daylight_only` | `true` | Require `sun.sun` state exactly `above_horizon` |
| `interval_minutes` | `60` | Integer 1–1440; checks start immediately, then at this interval |
| `minimum_confidence` | `0.65` | Inclusive threshold; below it the normalized result becomes `unknown` |

Missing/unknown sun state or failed state request fails closed without fetching
an image. **Analyze Now** runs immediately regardless of the scheduled interval.
It respects daylight by default; the explicit checkbox bypasses that restriction.
Disabling `daylight_only` also permits night analysis. Manual runs do not move
the scheduled deadline. If a run is active, another is rejected (409); a due
scheduled run retries on the next ten-second scheduler tick. A skipped/failed
attempt waits until the next interval. Scheduling uses monotonic time, without
catch-up bursts. Status includes attempt and completed analysis timestamps in UTC.
Skipped daylight attempts preserve the prior completed results; consult `skipped`
and `last_attempt`. Results and status reset on restart.

## A/B comparison and memory

Each permitted analysis makes exactly one Core `GET /api/camera_proxy/<entity>`
request. Redirects are rejected, requests have connect/read timeouts (5/20 s),
and streamed reads have a 30-second deadline checked between chunks. A blocked
read can extend the total deadline by up to the read timeout. Snapshots are
limited to 8 MiB encoded and 16 million pixels decoded. Invalid images fail
closed. The same immutable encoded bytes are passed through stdin to each
worker; both decode the same image into RGB in memory. There is no image write,
image endpoint, image thumbnail, image cache or temporary image file.

Workers run **sequentially**, MobileNet then Prithiv, each with a 15-minute timeout
including download/load/inference. They exit after inference (or are killed and
reaped on timeout), so no two models are resident together. The parent never
imports torch. This trades repeat disk loading/import latency for reliable model
memory release, including allocator memory that Python GC may retain. Only
weights/configs are persistently cached in `/data/model-cache`. No whole-repository
snapshot download, training checkpoint directories, optimizer files or datasets
are fetched. No quantization, conversion, fine-tuning or GPU execution is used.
Torch and native compute threads are limited to two per worker.

Model status reports `loaded: false` while idle; during the subprocess lifetime
`loaded: null` and `phase: loading_or_running` explicitly mean the parent cannot
observe the exact load boundary. Completion reports `unloaded`. Each successful
result includes current RSS before imports, after inference while the model is
resident, after the inference function returns, and process peak RSS (Linux
bytes), plus forward-pass inference seconds and total worker time including
load/download. This is process memory, not system-wide RAM usage. Pi5 figures
are **not measured yet**. Prithiv's ~372 MB float32 weights imply several hundred
MB minimum; plan provisionally for 1–2 GiB worker RAM plus Home Assistant and OS,
and measure `peak_rss_bytes` on the intended device. Resolution limits and serial
workers bound the workload; no cgroup hard RAM limit is configured in this PoC.

## Pinned candidates and preprocessing

| Key | Hugging Face model | Revision | Requested weights |
| --- | --- | --- | --- |
| `mobilenet` | `dronefreak/bdd100k-weather-mobilenetv4_conv_small` | `51a02f796c4a1ac07ec7e35a40a619af83c90854` | `best.pt`: 10,177,727 bytes |
| `prithiv` | `prithivMLmods/Weather-Image-Classification` | `07db1be6dbd384c67e36400b3fd0acb0988bd50c` | root `model.safetensors`: 371,577,220 bytes |

These hashes and file sizes were read from the Hub model metadata during
implementation. MobileNet follows its model card: restricted `torch.load` with
`weights_only=True`, timm architecture/state dictionary/class names from the
checkpoint, square resize to checkpoint `imgsz`, and checkpoint mean/std.
No pretrained base weights are downloaded. Prithiv uses the pinned repository's
`AutoImageProcessor` and `SiglipForImageClassification`, safetensors only, with
remote code disabled. Both use float32 CPU evaluation/inference mode and softmax.
Native labels come from the actual model metadata, not an invented label list.

| Native labels | HA normalized state |
| --- | --- |
| `clear`, `sun/clear` | `sunny` |
| MobileNet `partly cloudy` | `partly_cloudy` |
| `overcast`, `cloudy/overcast` | `cloudy` |
| `rainy`, `rain/strom` (native typo), `rain/storm` | `rain` |
| `foggy`, `foggy/hazy` | `fog` |
| `snowy`, `snow/frosty` | `snow` |
| `unknown`, unrecognized labels | `unknown` |

Prithiv has no partly-cloudy class and never synthesizes it. `native_label`,
`mapped_state`, confidence and all native class probabilities remain visible even
when the threshold sets `normalized_state` to `unknown`. Match means both
normalized states agree and are known. `higher_confidence` is a simple nominal
winner; differing label spaces and uncalibrated probabilities mean it is **not
an accuracy comparison**. One candidate failure does not prevent the other from
running; errors are reported per candidate without leaking exception details.

## HTTP API

All paths are relative to the Ingress base path; the UI uses relative URLs.

- `GET /`: diagnostics, JSON status display, Analyze Now and bypass checkbox.
- `GET /api/status`: camera/options, busy/attempt/completion state, loaded phases,
  pinned identities, results and summary. Responses use `Cache-Control: no-store`.
- `POST /api/analyze`: JSON `{}` or `{"bypass_daylight": true}`; returns
  `{"accepted": true}` with 202 immediately, or 409 when busy. Invalid bodies
  return 400. HTML form and non-JSON requests are rejected.

A successful `results.<candidate>` contains `native_label`, `mapped_state`,
`normalized_state`, `confidence`, `probabilities`, `inference_seconds`,
`load_and_inference_seconds`, `rss_before_bytes`, `rss_after_inference_bytes`,
`rss_after_bytes`, and `peak_rss_bytes`. No image bytes enter the JSON response.
`last_analysis` means the A/B attempt completed; inspect per-model errors to
establish whether both succeeded. No weather state is published to HA yet.

## Downloads, build size and validation

Initial use requires internet access to Hugging Face (including its weight/CDN
hosts). Approximately **381,754,947 bytes (~382 MB / 364 MiB)** of weights plus
small configuration files are downloaded, with a similar persistent cache
footprint. Initial latency depends on network and CPU: allow minutes on Pi5;
there is no measured timing yet. At a sustained 10 Mbit/s, transfer alone takes
roughly five minutes. Later runs load the cache without downloading weights
again, although Hub metadata checks may still need network; offline mode is not
configured. Removing `/data/model-cache` forces redownloads.

The Docker build installs Python/torch/torchvision/transformers/timm and **does
not download model weights**. CPU-only torch 2.6.0 and torchvision 0.21.0 wheels
exist for CPython 3.11 aarch64 on the PyTorch CPU index. The dependency image is
still large: budget roughly 1–2 GB uncompressed including the Python base and
scientific stack; this is an estimate, not a measured Docker image size. Actual
ARM64 build/linking, inference correctness and Pi5 speed/RSS remain validation
items. The unpinned base tag/transitive dependency set should be locked and
security-reviewed before production.

Focused tests use mocked HA state/snapshot calls and model runners: no live HA,
model downloads, training data or heavyweight ML imports. To run in an isolated
environment, install Flask, requests, Pillow, PyYAML and pytest, then run:

```sh
python -m pytest -q weather_camera_poc/tests
python -m compileall -q weather_camera_poc
sh -n weather_camera_poc/run.sh
```

Validation in this worker: **30 focused tests passed** (Python 3.13 test host;
all application/test sources also parsed with Python 3.11 grammar). YAML and
add-on contract checks and `sh -n` passed. No live Supervisor schema validation
was performed.

The worker environment had no Docker executable, so only static Dockerfile,
entrypoint, YAML/schema consistency and Python validation were possible. No
claim of container or Raspberry Pi runtime success is made.

## Later fine-tuning

First evaluate both candidates against manually labeled, representative camera
stills across weather, seasons and lighting. A later opt-in collection workflow
must define privacy, consent, retention/deletion and labeling before saving any
images. Split by capture date/event to avoid near-duplicate train/test leakage;
reserve a held-out camera test set, examine class confusion/calibration, then
fine-tune and compare against these pinned baselines. No dataset retention or
training workflow is implemented here.

## Sources

- [Supervisor add-on configuration and discovery](https://contributors.home-assistant.io/apps/reference/configuration/)
- [Ingress integration](https://developers.home-assistant.io/docs/add-ons/ingress/)
- [MobileNet model card and usage](https://huggingface.co/dronefreak/bdd100k-weather-mobilenetv4_conv_small)
- [Prithiv model configuration](https://huggingface.co/prithivMLmods/Weather-Image-Classification/blob/07db1be6dbd384c67e36400b3fd0acb0988bd50c/config.json)
- [PyTorch CPU wheel index](https://download.pytorch.org/whl/cpu/)

Model cards list Apache-2.0 weight licenses; the MobileNet card also describes
BDD100K research/non-commercial dataset terms. Review source terms for uses
beyond this experimental PoC. No datasets are downloaded by this add-on.
