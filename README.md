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
  -e MQTT_BROKER=your_broker_IP_address_xx.xx.xx.xx \
  -e MQTT_USER=mqttuser \
  -e MQTT_PASS=secret \
  -e ANOVA_TOKEN=anova-xxxxxxxxxxxxxxxx \
  ghcr.io/dberg117/anova-wifi-mqtt-bridge:latest
