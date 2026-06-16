# Anova Wi-Fi to MQTT Home Assistant Bridge

A ultra-lightweight, high-efficiency middleware proxy to bridge the official Anova Wi-Fi Developer CLI tool with Home Assistant over MQTT. Optimized specifically for TrueNAS SCALE and Alpine Linux containers.

## 🚀 Features
- **Sub-second Command Latency**: Inline stdin stream piping.
- **ZFS Protection**: Dead-Silent operation cuts disk write wear to 0%.
- **Database Optimization**: 0.2°C temperature deadband filtering eliminates HA DB bloat.
- **Zero Idle Overhead**: Disables cyclic garbage collection; drops idle CPU usage to 0%.

## 🛠️ TrueNAS SCALE Installation
1. Launch a **Custom App** using the `python:3.11-alpine` image.
2. Mount a **Host Path Volume** pointing to this repository folder to `/app`.
3. Set the **Container Entrypoint** to:
   - Command: `/bin/sh`
   - Argument: `/app/start.sh`
4. Pass these **Environment Variables**:
   - `MQTT_BROKER`: Your Home Assistant MQTT IP
   - `MQTT_USER`: Your MQTT username
   - `MQTT_PASS`: Your MQTT password
   - `ANOVA_TOKEN`: Your Personal Access Token from the mobile app

## 🏡 Home Assistant Configuration
Append the corresponding sensors to your `configuration.yaml` file:

```yaml
mqtt:
  sensor:
    - name: "Anova Current Temperature"
      state_topic: "anova/cooker/state"
      value_template: "{{ value_json.currentTemperature }}"
      unit_of_measurement: "°C"
      device_class: "temperature"
```
