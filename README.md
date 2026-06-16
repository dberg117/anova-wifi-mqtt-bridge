[![GitHub release](https://shields.io)](https://github.com)
[![License: MIT](https://shields.io)](https://github.com)
[![Platform](https://shields.io)](https://truenas.com)
[![Home Assistant](https://shields.io)](https://home-assistant.io)

# Anova Wi-Fi to MQTT Home Assistant Bridge

An ultra-lightweight, high-efficiency middleware proxy engine to bridge the official Anova Wi-Fi Developer CLI tool with Home Assistant over MQTT. Designed specifically for TrueNAS SCALE and Alpine Linux containers.

## 🚀 Features
- **Sub-second Command Latency**: Inline stdin stream injection.
- **ZFS Drive Protection**: Dead-Silent operation cuts disk log writes to 0%.
- **Database Optimization**: 0.2°C temperature filtering eliminates HA DB bloat.
- **Networkless Reboots**: Persistent localized dependency caching hooks.
- **Decoupled Power Staging**: Changes targets safely while the cooker remains off.

---

## 🔑 Obtaining Your Anova Developer Token

The official Anova cloud API requires a developer token to authorize your container. Even if you only own an Anova Sous Vide cooker, you must use the Oven app interface to generate this string.

1. Download and log into the **Anova Precision Oven App** on your iOS or Android smartphone.
2. Navigate to the **More** tab in the bottom-right corner of the app layout.
3. Tap on **Developer** options.
4. Select **Personal Access Tokens**.
5. Click **Create Token** (or *Generate*), copy the long alphanumerical string payload, and save it safely. 
   - *Note: Your token string will always begin with the `anova-` prefix.*

---

## 🛠️ TrueNAS SCALE Deployment Guide

Follow these step-by-step instructions to configure and deploy this container cleanly on TrueNAS SCALE.

### 📋 Prerequisites
1. Set up a secure dataset folder directory on your storage pool.
   - *Example Path Base*: `/mnt/YOUR_POOL/anova-wifi`
2. Obtain your **Anova Developer Token** using the Oven app steps above.
3. Obtain your Home Assistant **MQTT Broker IP** and login credentials.

### 📂 Step 1: Clone the Core CLI Repository
Open your TrueNAS Host Shell (**System Settings** > **Shell**) or an active SSH terminal session to clone the underlying framework.

1. Move into your target storage network directory:
   ```bash
   cd /mnt/YOUR_POOL/anova-wifi
   ```
2. Clone the official Anova device command line library:
   ```bash
   git clone https://github.com developer-project-wifi
   ```
3. Move into the newly created folder asset layout:
   ```bash
   cd developer-project-wifi
   ```
4. Move your custom `start.sh` and `mqtt_bridge.py` files directly into this directory folder alongside the cloned script files.

### 📦 Step 2: Initialize the Application
1. Navigate to the **Apps** page on your TrueNAS SCALE sidebar.
2. Click **Discover Apps** in the top right corner.
3. Click **Custom App** to open the configuration wizard.
4. Set the **Application Name** to: `anova-wifi-cli`.

### 💾 Step 3: Container Image Configuration
1. Locate the **Container Image** section.
2. Set the **Image Repository** to: `python`.
3. Set the **Image Tag** to: `3.11-alpine`.

### ⚙️ Step 4: Entrypoint & Command Configuration
1. Scroll down to the **Container Entrypoint** section.
2. Leave the **Entrypoint** field completely blank or empty.
3. Under **Command**, add exactly one item entry:
   - `/app/start.sh`

### 🔑 Step 5: Environment Variables (Secrets Setup)
1. Locate the **Environment Variables** subsection.
2. Click **Add** to map your configuration tokens sequentially:
   - **Key**: `MQTT_BROKER` | **Value**: `Your_Home_Assistant_IP`
   - **Key**: `MQTT_USER` | **Value**: `Your_MQTT_Username`
   - **Key**: `MQTT_PASS` | **Value**: `Your_MQTT_Password`
   - **Key**: `ANOVA_TOKEN` | **Value**: `Your_Anova_Personal_Access_Token`

### 🌐 Step 6: Network Configuration
1. Scroll down to the **Network Configuration** menu.
2. Check the box for **Host Network** to bypass internal Docker bridges. 
   - *Note: This is mandatory to route network packets to local Home Assistant IPs.*

### 📂 Step 7: Host Path Storage Volumes
1. Scroll down to the **Storage Settings** section.
2. Under **Host Path Volumes**, click **Add** to configure persistent file mappings:
   - **Host Path**: `/mnt/YOUR_POOL/anova-wifi/developer-project-wifi`
   - **Mount Path**: `/app`

### 🎛️ Step 8: System Resource Allocation Limits (Optional)
*Note: Setting container resource limits is entirely optional but recommended to maintain a minimal runtime environment.*

1. Locate the **Resources / Limits** options block.
2. Check the box to enable **CPU and Memory Limits**.
3. Set the **CPU Limit** to exactly: `1` *(This restricts the container to a maximum of 1 whole CPU core)*.
4. Set the **Memory Limit** to exactly: `64MB`. *(This should be a safe amount as I've only seen about 32mb typically)*.

### 🚀 Step 9: Save and Launch
1. Scroll to the bottom of the installation wizard.
2. Click the blue **Save** button.
3. Wait for the app card status to read **Running**.
4. Open the app **Logs** panel to confirm the bridge establishes cleanly.

---

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
