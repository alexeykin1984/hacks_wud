# WUD (What's Up Docker) for Home Assistant

[Инструкция на русском](README.ru.md)

Custom integration (installable through HACS) that connects Home Assistant to the
[WUD](https://github.com/getwud/wud) REST API and creates one `update` entity per
watched container.

## Features

- **Update entity per container** — installed / latest version (tag, or tag + short
  digest for digest-watched images), semver diff, watcher, stack, status, errors as
  attributes.
- **Device per container** — each container is its own device (named after the
  container, model = image, version = installed tag), linked to the WUD hub device.
  New container devices are placed in the hub's area. This is what the
  *Settings → Updates* list shows as the title of each update.
- **Install button** — runs the update trigger WUD associates with the container
  (`GET /api/containers/{id}/triggers`, preferring `docker` / `dockercompose`, then
  `command` / `nomad`), honoring `wud.trigger.include` / `wud.trigger.exclude` the
  same way the WUD UI and the MQTT install button do.
- **Progress percentage** — derived from update stages observed through the WUD
  event stream (`/api/events`):

  | Stage        | %      | Signal                                          |
  |--------------|--------|-------------------------------------------------|
  | starting     | 0      | install requested                               |
  | pulling      | 5→60   | trigger running (image pull)                    |
  | stopped      | 65     | old container left the `running` state          |
  | removed      | 75     | old container removed from WUD                  |
  | created      | 85     | replacement container indexed (new id)          |
  | started      | 95     | replacement container `running`                 |
  | finalizing   | 98     | trigger call returned                           |
  | done         | 100    | WUD indexed the updated container               |

  Between stages the value creeps towards the next one, so it keeps moving during
  long pulls. The current stage is exposed as the `update_stage` attribute.
- **Release notes from GitHub** — summary (255 chars) on the entity and full notes
  in the update dialog. For tag updates the dialog shows every release in between
  (e.g. `1.2.0` → `1.4.0` lists 1.4.0, 1.3.x and 1.3.0, newest first, up to 20;
  pre-releases only when updating to a pre-release). The repository is resolved like WUD's MQTT integration does:
  `org.opencontainers.image.source` label → release link (`wud.link.template`) →
  `ghcr.io` image name.
- **Icons** — from the container `displayIcon` (`wud.display.icon` label), using the
  WUD UI conventions: `mdi:` icons natively, `sh:`/`selfhst:`/`hl:`, `si:`, `fa*:`
  and any Iconify `prefix:name` or URL as entity picture. Containers without an icon
  of their own (WUD's default `mdi:docker`) get the WUD icon.
- **Live updates** — changes are pushed through the SSE event stream; a full poll
  (default every 5 min) resynchronizes everything.

## Installation

1. HACS → Integrations → ⋮ → *Custom repositories* → add
   `https://github.com/alexeykin1984/hacks_wud` (category *Integration*), then install **WUD (What's Up Docker)**.
   Or copy `custom_components/wud` to `<config>/custom_components/`.
2. Restart Home Assistant.
3. Settings → Devices & services → *Add integration* → **WUD**.
4. Enter the WUD URL (e.g. `http://192.168.1.150:3000`) and either the basic-auth
   username/password or an API token (leave empty if WUD has no auth).

Options: polling interval and a GitHub token (raises the 60 req/h anonymous GitHub
rate limit used for release notes).

## Notes

- The install button only appears when WUD has an update trigger
  (`docker`, `dockercompose`, `command`, `nomad`) associated with the container.
- Entities and devices of containers that disappeared from WUD become unavailable
  and are removed on the next Home Assistant restart (or delete the device manually).
- Updating the WUD container itself requires the trigger's `SELFUPDATE` option.

## Development

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements_test.txt ruff
pytest --cov=custom_components.wud   # unit/integration tests (mocked WUD + GitHub)
ruff check . && ruff format --check .
```

## License

MIT. The WUD logo in `custom_components/wud/brand/` comes from the
[WUD project](https://github.com/getwud/wud) (MIT, © Manfred Martin).
