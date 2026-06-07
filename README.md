# Beko AC IR Control — Home Assistant Integration

Home Assistant integration for Beko 31225 / 30925 air conditioners via Zigbee IR blaster (Moes UFO-R11).

Two problems are solved:
- Encoding logical AC state into the IR blaster wire format
- Integrating the solution with Home Assistant

> This project makes heavy use of AI tooling. You will see artefacts of that. That's just life now.

## Protocol

Full IR protocol documentation for Beko 31225 / 30925 — frames, timings, encoding, verified hex values: [PROTOCOL.md](PROTOCOL.md)

## Requirements

- Home Assistant with MQTT integration
- Zigbee2MQTT
- pyscript custom component (via HACS or manual install)

## Why buttons instead of a climate entity

Home Assistant's climate card has a guard that prevents re-sending a command if the UI state
already matches the selected value (`value === oldValue`). Since Beko ACs give no feedback over IR,
the UI state can drift from the real AC state (e.g. someone used the physical remote).
A button-based dashboard has no such guard — every tap always sends the IR signal.

## Files

| File | Purpose |
|------|---------|
| `beko_ir.py` | pyscript — IR frame generation and MQTT triggers |
| `beko.yaml` | HA package — buttons, switches, input_number entities |
| `configuration.yaml` | Example configuration.yaml with package include |
| `lovelace-beko.yaml` | Lovelace dashboard for two AC units |

## Installation

### 1. Install pyscript

Via HACS: `Home Assistant Community Store → Integrations → pyscript`.

Or manually: clone `custom-components/pyscript` into `/config/custom_components/pyscript`.

### 2. Deploy the script

```bash
scp beko_ir.py <ha-host>:/path/to/config/pyscript/beko_ir.py
```

Example:
```bash
scp beko_ir.py 192.168.1.100:~/smarthome/homeassistant/config/pyscript/beko_ir.py
```

### 3. Create the packages directory and copy the package

On the HA server:
```bash
mkdir -p /path/to/config/packages
cp beko.yaml /path/to/config/packages/beko.yaml
```

### 4. Update configuration.yaml

Add to `configuration.yaml`:

```yaml
pyscript:
  allow_all_imports: true
  hass_is_global: true

homeassistant:
  packages:
    beko: !include packages/beko.yaml
```

If `homeassistant:` already exists, add only the `packages:` block inside it.

### 5. Configure devices in beko_ir.py

Find the `DEVICES` dict at the top of `beko_ir.py` and set the correct IR blaster names from Zigbee2MQTT:

```python
DEVICES = {
    "kabinet": {
        "ir_topic":     "zigbee2mqtt/Black Box IR/set",
        "input_number": "input_number.beko_kabinet_temp",
    },
    "salon": {
        "ir_topic":     "zigbee2mqtt/Plugged IR/set",
        "input_number": "input_number.beko_salon_temp",
    },
}
```

The IR blaster name (`Black Box IR`, `Plugged IR`) must match the `friendly_name` in Zigbee2MQTT.

### 6. Restart Home Assistant

```bash
docker restart homeassistant
```

### 7. Add the Lovelace dashboard

In HA: `Settings → Dashboards → Add Dashboard`.

Open the new dashboard → three dots → `Edit Dashboard` → `Raw configuration editor`.

Paste the contents of `lovelace-beko.yaml`.

## MQTT Topics

Format: `beko/<device>/set/<command>`

| Topic | Payload | Description |
|-------|---------|-------------|
| `beko/<dev>/set/mode` | `off` / `cool` / `heat` / `dehum` / `fan_only` / `auto` | Mode |
| `beko/<dev>/set/temperature` | `16`–`30` | Set temperature |
| `beko/<dev>/set/temp_up` | `1` | Temperature +1°C |
| `beko/<dev>/set/temp_down` | `1` | Temperature −1°C |
| `beko/<dev>/set/fan_mode` | `1`–`5` | Fan speed |
| `beko/<dev>/set/swing` | `pos1`–`pos6`, `auto`, `stop` | Vane position |
| `beko/<dev>/set/display` | `toggle` | Backlight toggle |
| `beko/<dev>/set/turbo` | `on` / `off` | Turbo mode |

## Adding a new AC unit

1. Add the device to `DEVICES` in `beko_ir.py`
2. Add entities to `beko.yaml` following the `kabinet` / `salon` pattern
3. Add a card to `lovelace-beko.yaml`
4. Deploy `beko_ir.py`, copy the updated `beko.yaml`, restart HA

## Supported hardware

Tested with:
- IR blasters
  - Moes UFO-R11 (Zigbee, model TS1201)
  - Model iH-F8260 Universal Smart IR Remote Control (Tuya Wi-Fi)
- AC units
  - [Beko 31225](https://www.beko.com.tr/split-klima/31225-ekolojik-klima)
  - [Beko 30925](https://www.beko.com.tr/split-klima/30925-ekolojik-klima)

 Shares the same remote (5401568401-5401552301) as
 
 - BEKO
   - Beko 30925
   - Beko 31225
   - Beko 31825
 - arcelik (see list in the link below)
   - https://www.umurstore.com.tr/arcelik-inverter-yeni-tip-klima-kumanda-5401568401-5401552301-arcelik-orjinal

## Debugging

Check that pyscript loaded:
```bash
docker logs homeassistant 2>&1 | grep beko
```

Monitor MQTT traffic:
```bash
mosquitto_sub -h <ha-host> -p 1883 -t 'beko/#' -v
```
