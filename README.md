# Anova Wi-Fi to MQTT Home Assistant Bridge

An ultra-lightweight, high-efficiency middleware proxy engine to bridge the official Anova Wi-Fi Developer CLI tool with Home Assistant over MQTT. Designed specifically for TrueNAS SCALE and Alpine Linux containers.

## 🚀 Features
- **Sub-second Command Latency**: Inline stdin stream injection.
- **ZFS Drive Protection**: Dead-Silent operation cuts disk log writes to 0%.
- **Database Optimization**: 0.2°C temperature filtering eliminates HA DB bloat.
- **Networkless Reboots**: Persistent localized dependency caching hooks.
- **Decoupled Power Staging**: Changes targets safely while the cooker remains off.

## 🛠️ TrueNAS SCALE App Configuration
1. Deploy a **Custom App** container utilizing image layout tag: `python:3.11-alpine`.
2. Mount a **Host Path Volume** mapping this repository to directory: `/app`.
3. Configure the **Container Entrypoint** options:
   - Command: `/bin/sh`
   - Argument: `/app/start.sh`
4. Set these secure **Environment Variables**:
   - `MQTT_BROKER`: Your Home Assistant server IP address.
   - `MQTT_USER`: Your MQTT broker configuration profile user.
   - `MQTT_PASS`: Your MQTT broker connection profile password.
   - `ANOVA_TOKEN`: Your Personal Access Token generated via the phone app.

## 🏡 Home Assistant Sensors Mapping Layout
Append this comprehensive tracker mapping framework configuration block directly to your `configuration.yaml` file:

```yaml
mqtt:
  sensor:
    - name: "Anova Current Temperature"
      state_topic: "anova/cooker/state"
      value_template: "{{ value_json.currentTemperature }}"
      unit_of_measurement: "°C"
      device_class: "temperature"
    - name: "Anova Target Temperature"
      state_topic: "anova/cooker/state"
      value_template: "{{ value_json.targetTemperature }}"
      unit_of_measurement: "°C"
      device_class: "temperature"
    - name: "Anova Timer Remaining"
      state_topic: "anova/cooker/state"
      value_template: "{{ value_json.timerInSeconds }}"
      unit_of_measurement: "s"
      device_class: "duration"
    - name: "Anova Firmware Version"
      state_topic: "anova/cooker/state"
      value_template: "{{ value_json.firmwareVersion }}"
    - name: "Anova Temperature Unit"
      state_topic: "anova/cooker/state"
      value_template: "{{ value_json.unit | upper }}"
    - name: "Anova Job Type"
      state_topic: "anova/cooker/state"
      value_template: "{{ value_json.currentJob.jobType if value_json.currentJob is defined else 'None' }}"
    - name: "Anova Job Stage"
      state_topic: "anova/cooker/state"
      value_template: "{{ value_json.currentJob.jobStage if value_json.currentJob is defined else 'None' }}"

  binary_sensor:
    - name: "Anova Cooking Status"
      state_topic: "anova/cooker/state"
      value_template: "{{ 'ON' if value_json.isCooking else 'OFF' }}"
      device_class: "running"
    - name: "Anova Connection Status"
      state_topic: "anova/cooker/state"
      value_template: "{{ 'ON' if value_json.isConnected else 'OFF' }}"
      device_class: "connectivity"
    - name: "Anova Alarm Active"
      state_topic: "anova/cooker/state"
      value_template: "{{ 'ON' if value_json.isAlarmActive else 'OFF' }}"
      device_class: "problem"
    - name: "Anova Timer Running"
      state_topic: "anova/cooker/state"
      value_template: "{{ 'ON' if value_json.isTimerRunning else 'OFF' }}"
    - name: "Anova Speaker Status"
      state_topic: "anova/cooker/state"
      value_template: "{{ 'ON' if value_json.isSpeakerOn else 'OFF' }}"
    - name: "Anova Keeping Warm"
      state_topic: "anova/cooker/state"
      value_template: "{{ 'ON' if value_json.isKeepingWarm else 'OFF' }}"
    - name: "Anova Ice Bath Monitoring"
      state_topic: "anova/cooker/state"
      value_template: "{{ 'ON' if value_json.isMonitoringIcebath else 'OFF' }}"
```
