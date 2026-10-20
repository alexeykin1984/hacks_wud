# WUD (What's Up Docker) for Home Assistant

[Инструкция на русском](README.ru.md)

See and install Docker container updates right from Home Assistant.
The integration connects to [WUD (What's Up Docker)](https://github.com/getwud/wud)
and shows every container WUD watches in **Settings → Updates**, next to the
Home Assistant and add-on updates.

## Features

- **Every container is an update in Home Assistant** — installed and new version,
  with the container's name and icon, in *Settings → Updates* and on dashboards.
- **One-click install** — the *Install* button updates the container through WUD,
  the same way the WUD web UI does.
- **Live progress** — a percentage and the current stage (pulling the image,
  stopping, starting…) while the container is being updated.
- **Full changelog across skipped versions** — release notes come from GitHub, and
  when you jump several versions at once (e.g. `1.2.0` → `1.4.0`) you see the
  changes of every release in between: 1.4.0, 1.3.1, 1.3.0…
- **Short summary on the card** — the beginning of the latest release notes is shown
  right in the update card, with a link to the release on GitHub.
- **A device per container** — each container is its own device (image, installed
  version) linked to the WUD device and placed in the same area.
- **Container icons** — the icon set in WUD (`mdi:`, Simple Icons, selfh.st, Font
  Awesome, Iconify, image URLs); containers without one get the WUD icon.
- **Instant updates** — changes in WUD show up in Home Assistant immediately,
  no waiting for the next poll.
- **Useful attributes for automations** — kind of change (major / minor / patch),
  stack, watcher, container status, WUD errors.

## Installation

1. HACS → Integrations → ⋮ → *Custom repositories* → add
   `https://github.com/alexeykin1984/hacks_wud` (category *Integration*), then
   install **WUD (What's Up Docker)**.
   Or copy `custom_components/wud` to `<config>/custom_components/`.
2. Restart Home Assistant.
3. Settings → Devices & services → *Add integration* → **WUD**.
4. Enter the WUD URL (e.g. `http://192.168.1.10:3000`) and either the username and
   password or an API token. Leave them empty if WUD has no authentication.

The URL and credentials can be changed later with *Reconfigure*, without removing
the integration.

## Options

- **Polling interval** — how often the full container list is refreshed
  (default 5 minutes). Changes are pushed instantly anyway.
- **GitHub token** — optional. Without a token GitHub allows 60 requests per hour,
  which may be too few with many updates.

## Good to know

- The *Install* button appears only when WUD has an update trigger (`docker`,
  `dockercompose`, `command` or `nomad`) for the container. Without one the update
  is still shown, just without the button.
- Release notes need the image to point to its GitHub repository. WUD's MQTT
  integration uses the same rules: the `org.opencontainers.image.source` label, the
  release link (`wud.link.template`) or a `ghcr.io` image.
- Intermediate releases are listed for tag updates only (`1.2.0` → `1.4.0`), newest
  first, up to 20. Pre-releases are included only when updating to a pre-release.
  For `latest`-style images updated by digest the installed version is unknown, so
  only the newest release is shown.
- Containers removed from WUD become unavailable and are cleaned up on the next
  Home Assistant restart. Their devices can also be deleted manually.
- Updating the WUD container itself requires the trigger's `SELFUPDATE` option.

## How the progress is calculated

The percentage follows the stages reported by the WUD event stream. Between stages
it keeps creeping towards the next one, so it doesn't freeze during long pulls.
The current stage is in the `update_stage` attribute.

- **0%** `starting` — install requested
- **5 → 60%** `pulling` — the trigger is running (image pull)
- **65%** `stopped` — the old container stopped
- **75%** `removed` — the old container was removed
- **85%** `created` — the new container appeared in WUD
- **95%** `started` — the new container is running
- **98%** `finalizing` — the trigger finished
- **100%** `done` — WUD indexed the updated container

With the `dockercompose` trigger WUD answers before it notices the new container,
so 75 / 85 / 95% are usually skipped.

## Development

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt ruff
pytest --cov=custom_components.wud   # tests with mocked WUD and GitHub
ruff check . && ruff format --check .
```

## License

MIT. The WUD logo in `custom_components/wud/brand/` comes from the
[WUD project](https://github.com/getwud/wud) (MIT, © Manfred Martin).
