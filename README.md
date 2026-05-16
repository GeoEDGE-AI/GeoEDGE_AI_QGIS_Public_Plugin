# GeoEdge AI — QGIS Plugin

GeoEdge AI is an AI assistant for QGIS that translates natural-language requests into PyQGIS spatial analysis, cartography, and automation. It runs as a thin client inside QGIS and communicates with the GeoEdge Cloud agent at `https://api.geoedge.ai`.

> **Network-dependent.** GeoEdge AI requires a connection to `api.geoedge.ai` during use. There is no offline mode.

## What it does

- **Describe in plain English** — "buffer the roads layer by 100 m and intersect with parcels".
- **Inspects your data** — checks loaded layers, fields, CRS, viewport.
- **Runs PyQGIS analysis** through a curated, audited tool registry.
- **Iterates** — observes results, retries, asks for approval on destructive actions.
- **Designs maps** with data-aware symbology, layouts, and exports.

The agent itself runs on `api.geoedge.ai`; tool execution happens locally inside your QGIS session, so your project files and full feature data never leave your machine unless you explicitly enable a related setting.

## Account & pricing

- **Free starter plan** — sign up at [https://app.geoedge.com.au/](https://app.geoedge.com.au/). No credit card required.
- Paid plans and pay-as-you-go tokens available for heavier use.
- See [https://geoedge.ai/pricing](https://geoedge.ai/pricing) for current limits.

## Install

### From the QGIS Plugin Manager (recommended, post-listing)

1. In QGIS, open *Plugins → Manage and Install Plugins…*.
2. Search for **GeoEdge AI** and click *Install Plugin*.
3. Open *Plugins → GeoEdge AI → Sign in*. Use your GeoEdge Cloud account.

### From this repository

1. Download the latest release zip from [Releases](https://github.com/GeoEDGE-AI/GeoEDGE_AI_QGIS_Public_Plugin/releases).
2. In QGIS, *Plugins → Manage and Install Plugins… → Install from ZIP*.
3. Sign in.

## Requirements

- QGIS 3.40 or later (current LTR or newer).
- Outbound HTTPS access to `api.geoedge.ai`.
- A GeoEdge Cloud account (free starter plan available).

## Privacy

The plugin sends your chat message and a metadata description of your project (layer names, field types, CRS, viewport bbox) to the agent. It does **not** send full feature geometries or attribute values by default. See [PRIVACY.md](PRIVACY.md) for the complete data-flow inventory and the per-feature settings toggles.

## License

GPL-2.0-or-later. See [COPYING](COPYING) and [LICENSE](LICENSE) (identical content). All `.py` files carry an SPDX header.

## Status

Pre-release. Will be submitted to [plugins.qgis.org](https://plugins.qgis.org) marked `experimental=True`.

## Support

- Issues: [GitHub Issues](https://github.com/GeoEDGE-AI/GeoEDGE_AI_QGIS_Public_Plugin/issues)
- Email: support@geoedge.ai
- Web: [https://geoedge.ai](https://geoedge.ai)
