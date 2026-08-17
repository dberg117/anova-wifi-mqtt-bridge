# Anova Wi-Fi → MQTT Bridge for Home Assistant

Lightweight bridge that connects an Anova Precision Cooker (Wi-Fi models) to Home Assistant via MQTT.

It wraps the official Anova interactive CLI, publishes state with MQTT discovery, and accepts start / stop / target-temperature commands.

## Features

- Full Home Assistant MQTT discovery (sensors, binary sensors, switch, number)
- Availability topic + Last Will Testament so HA marks the device unavailable if the bridge dies
- 0.2 °C temperature dead-band (configurable) to reduce recorder spam
- Heartbeat every 5 minutes (configurable)
- Configurable device index when multiple cookers are present
- Target temperature can be staged while the cooker is off
- Automatic restart of the underlying CLI process
- Alpine-based Docker image (~50 MB)

## Quick start (Docker)

```bash
docker run -d \
  --name anova-bridge \
  --restart unless-stopped \
  -e MQTT_BROKER=192.168.1.10 \
  -e MQTT_USER=mqttuser \
  -e MQTT_PASS=secret \
  -e ANOVA_TOKEN=anova-xxxxxxxxxxxxxxxx \
  ghcr.io/dberg117/anova-wifi-mqtt-bridge:latest
```

## Or with Docker Compose:

```YAML
services:
  anova-bridge:
    image: ghcr.io/dberg117/anova-wifi-mqtt-bridge:latest
    container_name: anova-bridge
    restart: unless-stopped
    environment:
      MQTT_BROKER: 192.168.1.10
      MQTT_PORT: 1883
      MQTT_USER: mqttuser
      MQTT_PASS: secret
      ANOVA_TOKEN: anova-xxxxxxxxxxxxxxxx
      # ANOVA_DEVICE_INDEX: 1          # optional, 1-based
      # TEMP_DEADBAND: 0.2             # optional
      # HEARTBEAT_INTERVAL: 300        # optional (seconds)
```

## Environment variables

| Variable | Required | Default | Description |
| --- | --- | --- | --- |
| MQTT_BROKER | Yes | – | MQTT broker hostname / IP |
| MQTT_PORT | No | 1883 | MQTT port |
| MQTT_USER | No | (empty) | MQTT username |
| MQTT_PASS | No | (empty) | MQTT password |
| ANOVA_TOKEN | Yes | – | Personal Access Token from the Anova Oven app |
| ANOVA_DEVICE_INDEX | No | 1 | Which device to select (1-based) |
| TEMP_DEADBAND | No | 0.2 | Minimum °C change before publishing |
| HEARTBEAT_INTERVAL | No | 300 | Force a publish every N seconds |
| MQTT_CLIENT_ID | No | anova-wifi-mqtt-bridge | MQTT client ID |

## How to obtain and set the required variables

### 1. MQTT_BROKER / MQTT_PORT / MQTT_USER / MQTT_PASS
These come from your Home Assistant MQTT broker (usually the Mosquitto add-on).

1. In Home Assistant go to Settings → Add-ons → Mosquitto broker.
2. Note the IP address (or hostname) of the machine running Home Assistant — this is your MQTT_BROKER.
3. Default port is 1883 (or 8883 if you enabled SSL).
4. Create a dedicated MQTT user (recommended):
  - Go to Settings → People → Users (or use the Mosquitto configuration) and create a user/password.
  - Put those values into MQTT_USER and MQTT_PASS.
5. If you leave MQTT_USER and MQTT_PASS empty the bridge will connect anonymously (only works if your broker allows it).

### 2. ANOVA_TOKEN (Personal Access Token)

1. Install the Anova Precision Oven app on your phone (works even if you only own a sous-vide cooker).
2. Log in with the same Anova account that is paired with your cooker.
3. Open the app → tap More (bottom right) → Developer → Personal Access Tokens.
4. Tap Create Token (or Generate).
5. Copy the long string that starts with "anova-". This is the value for the ANOVA_TOKEN environment variable.
6. Keep the token private — anyone with it can control your cooker through the Anova cloud.

### 3. Optional variables

- ANOVA_DEVICE_INDEX – Only needed if you have more than one Anova device on the same account. Set it to 1, 2, etc. (1-based) to choose which device the bridge controls.
- TEMP_DEADBAND – Increase (e.g. 0.5) if you want even fewer temperature updates written to the Home Assistant database.
- HEARTBEAT_INTERVAL – How often (in seconds) a state message is forced even if nothing changed. Useful for keeping the availability status fresh.

## Home Assistant
Once the container is running, entities appear automatically under a device named Anova thanks to MQTT discovery. No YAML is required for the basic sensors / switch / number entity.

### Optional Lovelace card example
```YAML
type: conditional
conditions:
  - condition: state
    entity: binary_sensor.anova_connection_status
    state: "on"
card:
  type: vertical-stack
  cards:
    - type: grid
      columns: 3
      square: false
      cards:
        - type: button
          entity: switch.anova_control
          name: Anova
        - type: gauge
          entity: sensor.anova_target_temperature
          name: Target
        - type: gauge
          entity: sensor.anova_current_temperature
          name: Current
    - type: entities
      entities:
        - entity: number.anova_target_temperature
          name: Set Target Temp
        - entity: sensor.anova_job_stage
        - entity: sensor.anova_timer_remaining
```

## Command topics
- anova/cooker/set
  - "start" / "stop"
  - a number (e.g. 56.5) – sets target temperature
  - JSON: {"command":"start","temp":56.5,"timer":3600}

State is published to anova/cooker/state (pruned) and a fuller payload to anova/cooker/diagnostic.
Availability: anova/cooker/status → online / offline.

## Building locally
```bash
git clone https://github.com/dberg117/anova-wifi-mqtt-bridge.git
cd anova-wifi-mqtt-bridge
docker build -t anova-wifi-mqtt-bridge .
```

## Notes and limitations
- The bridge drives the official interactive CLI via stdin. Menu changes by Anova can break command injection. A future version may switch to the pure websocket protocol / anova-wifi library.
- Only the first (or configured) device is used.
- Temperature unit is reported but not currently changeable through the bridge.
- Some older Bluetooth-only or very early Wi-Fi models may not be supported by the official CLI.

## License
MIT
