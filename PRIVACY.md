# Privacy & Data Flow — GeoEdge AI QGIS Plugin

GeoEdge AI is a network-dependent thin client. This document lists every piece of information the plugin sends to GeoEdge Cloud (`publicapi.geoedge.com.au`), what is **not** sent by default, and the settings that control optional data sharing.

Last updated: 2026-05-18. Plugin version: 1.0.9.

## What gets sent to the server (default behaviour)

When you ask the agent to do something, the plugin sends:

| Field | Why |
|---|---|
| Your chat message (verbatim) | The agent needs the request to plan. |
| Project CRS | Coordinate-aware planning. |
| Layer list — id, name, type (vector/raster/mesh), geometry type, CRS, field names + types, feature count, geometry bbox, layer source path | Lets the agent pick the right layer and tool; source path is required for geoprocessing code generation. |
| Active layer id | Disambiguates "this layer". |
| Viewport bbox | Spatial context for "what's on screen". |
| Conversation history (prior chat turns this session) | Lets the agent resolve replies to its own clarification questions. |

Plus, on every authenticated request:

| Field | Why |
|---|---|
| JWT access token | Authenticates you. |
| Plugin protocol version | Negotiates compatible server behaviour. |

## What is NOT sent (default behaviour)

By default, the plugin does **not** send:

- Full feature geometries.
- Attribute values from layers.
- Project file (`.qgs`/`.qgz`) contents.

## Settings toggles

In *Plugins → GeoEdge AI → Settings → Privacy*:

| Toggle | Default | When enabled |
|---|---|---|
| Send file paths in layer metadata | **on** | Layer source paths are included by default — required for geoprocessing operations (buffer, reproject, etc.). Disable to strip them from the payload. |
| Anonymous usage telemetry | **off** | Sends event names, timing, and error classes — never query content. (Planned; toggle is wired but no telemetry is currently emitted.) |
| Crash reports | **off** | Sends Python tracebacks with PII redaction. (Planned; toggle is wired but no reports are currently emitted.) |

All toggles persist across plugin upgrades.

## Server-side retention

- **Chat messages and project metadata:** retained for the duration of a conversation session, then expired after 30 days for debugging and quality.
- **Telemetry events (when opted in):** retained 90 days.
- **Crash reports (when opted in):** retained 180 days.

See [https://public.geoedge.com.au/privacy](https://public.geoedge.com.au/privacy) for the full GeoEdge Cloud privacy policy, including data-deletion and export requests.

## Network endpoints

The plugin connects to:

- `https://publicapi.geoedge.com.au/v1/auth/*` — login, token refresh.
- `https://publicapi.geoedge.com.au/v1/agent/stream` — agent SSE channel.
- `https://publicapi.geoedge.com.au/v1/agent/cancel` — cancel a turn.
- `https://publicapi.geoedge.com.au/v1/agent/capabilities` — protocol negotiation.
- `https://publicapi.geoedge.com.au/v1/usage` — token balance display.
- `https://publicapi.geoedge.com.au/v1/plans` — plan info display.

No other domains are contacted.

## Questions

Email [support@geoedge.com.au](mailto:support@geoedge.com.au) for privacy-specific questions or data requests.
