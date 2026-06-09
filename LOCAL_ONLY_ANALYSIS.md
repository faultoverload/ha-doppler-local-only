# Local-Only Auth Conversion Analysis

## Overview

This document identifies every file that needs modification to convert the ha-doppler
integration from cloud-dependent (email/password auth through sandmandoppler.com) to
fully local-only (IP + port + local_key).

## Architecture Summary

The integration has two code layers:

1. **doppyler library** (v0.0.20) — Python package providing DopplerClient + Doppler model
2. **Home Assistant custom component** (`custom_components/sandman_doppler/`) — HA integration

The local API already works via nonce challenge:
- GET `https://{ip}:{port}/{dsn}/nonce` → returns nonce
- SHA256(nonce + local_key) → Bearer token
- All device control uses `_call_local_api()` — no cloud needed after discovery

## Files Requiring Modification

### doppyler Library (pip package, needs fork/patch)

| # | File | Changes Needed |
|---|------|----------------|
| 1 | `client.py` — `__init__` | Make `email`/`password` optional with a `local_key`, `ip_address`, `port` alternative. Or add `@classmethod from_local(ip, port, local_key)`. |
| 2 | `client.py` — `get_token()` | Skip cloud auth when local-only. Currently calls LOGIN_URL, will fail with ConnectionError if cloud is dead. |
| 3 | `client.py` — `_refresh_token()` | Same — skip when local-only. |
| 4 | `client.py` — `_call_copilot_api()` | Entire method unused in local mode. All cloud semaphore logic can be removed. |
| 5 | `client.py` — `call_cloud_api()` | Entire method unused. Can be removed or raise. |
| 6 | `client.py` — `_add_or_update_device()` | Calls cloud `/device` and `/localkey` endpoints. Replace with a method that directly stores a `Doppler` from user-supplied IP/port/key. |
| 7 | `client.py` — `get_devices()` | Calls cloud THINGS_URL. Replace with method that accepts a list of manually configured devices, or just let the caller add Doppler instances directly. |
| 8 | `const.py` | Cloud URLs (BASE_SANDMAN_API_URL, LOGIN_URL, REFRESH_URL, THINGS_URL) can be removed or left as dead config. |

### HA Custom Component (`custom_components/sandman_doppler/`)

| # | File | Changes Needed |
|---|------|----------------|
| 9 | **`config_flow.py`** | **Major rewrite.** Replace email+password form with IP address, port (default 443), and local_key. Validate by attempting a GET to `/nonce` locally instead of calling cloud `get_token()`. |
| 10 | **`__init__.py`** | **Major changes.** Replace `CONF_EMAIL`/`CONF_PASSWORD` with `CONF_IP_ADDRESS`/`CONF_PORT`/`CONF_LOCAL_KEY`. Remove `client.get_token()` call. Remove `_get_devices()` and periodic cloud polling. Create `Doppler` instances directly from config data (IP + port + local_key) rather than relying on `client.get_devices()`. |
| 11 | **`const.py`** | **Add new config keys:** `CONF_IP_ADDRESS`, `CONF_PORT`, `CONF_LOCAL_KEY` (and optionally `CONF_DSN`). |
| 12 | `services.py` | **No cloud changes needed** — already works through `client.devices` dict which gives access to `Doppler` objects returned by `client.get_devices()`. If device discovery changes, `get_dopplers_from_targets()` needs to look up devices differently. |
| 13 | `translations/en.json` | **Update strings.** Config flow step descriptions need to change from email/password to IP/port/key. |
| 14 | `strings.json` | **Update strings.** Same as translations file but for source-of-truth. |

### Files That Do NOT Need Changes

These files deal only with local device entities and do not reference cloud auth:

- `entity.py` — Base entity class. No cloud refs.
- `http.py` — Webhook handler. No cloud refs.
- `device_trigger.py` — Device automation triggers. No cloud refs.
- `binary_sensor.py`, `sensor.py`, `switch.py`, `light.py`, `select.py`, `number.py`, `siren.py` — Platform entities. No cloud refs.
- `helpers.py` — Enum helpers. No cloud refs.
- `manifest.json` — Only lists doppyler as dependency. May need version bump.

## Modification Strategy

### Option A: Patch doppyler in-place (simpler for prototyping)
Clone the doppyler source, modify client.py/const.py to support local-only mode,
then pip install -e the patched version.

### Option B: Fork doppyler as sub-package (cleaner for HA install)
Copy the entire doppyler source into a `doppyler_local/` directory within the
custom_component, modify it for local-only, and update manifest.json to require
your patched version instead.

### Recommended Order of Work
1. Patch doppyler `client.py` to support local-only constructor
2. Update `const.py` with new config constants
3. Rewrite `config_flow.py` for IP/port/key form
4. Rewrite `__init__.py` to skip cloud calls
5. Update `translations/en.json` and `strings.json`
6. Test with a real Doppler on the LAN

## Workspace

- **Local path:** `/home/hermesagent/.hermes/kanban/workspaces/t_2afdf1ff`
- **GitHub fork:** `https://github.com/faultoverload/ha-doppler-local-only`
- **Venv:** Python 3.11 with doppyler==0.0.20 + aiohttp installed
