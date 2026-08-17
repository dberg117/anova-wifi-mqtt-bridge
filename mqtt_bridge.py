#!/usr/bin/env python3
"""
Anova Wi-Fi → MQTT bridge for Home Assistant.

Wraps the official Anova interactive CLI, exposes MQTT discovery entities,
and provides start/stop + target temperature control.
"""

from __future__ import annotations

import ast
import json
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import warnings
from typing import Any

import paho.mqtt.client as mqtt

warnings.filterwarnings("ignore", category=DeprecationWarning)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    stream=sys.stdout,
)
log = logging.getLogger("anova-bridge")

try:
    BROKER = os.environ["MQTT_BROKER"]
    TOKEN = os.environ["ANOVA_TOKEN"]
except KeyError as exc:
    log.error("Missing required environment variable: %s", exc)
    sys.exit(1)

PORT = int(os.environ.get("MQTT_PORT", "1883"))
USER = os.environ.get("MQTT_USER", "")
PASS = os.environ.get("MQTT_PASS", "")
DEVICE_INDEX = int(os.environ.get("ANOVA_DEVICE_INDEX", "1"))  # 1-based
TEMP_DEADBAND = float(os.environ.get("TEMP_DEADBAND", "0.2"))
HEARTBEAT_INTERVAL = float(os.environ.get("HEARTBEAT_INTERVAL", "300"))
STATE_TOPIC = "anova/cooker/state"
AVAIL_TOPIC = "anova/cooker/status"
DIAG_TOPIC = "anova/cooker/diagnostic"
SET_TOPIC = "anova/cooker/set"
CLIENT_ID = os.environ.get("MQTT_CLIENT_ID", "anova-wifi-mqtt-bridge")

ESSENTIAL_KEYS = {
    "currentTemperature",
    "targetTemperature",
    "timerInSeconds",
    "firmwareVersion",
    "unit",
    "isCooking",
    "isConnected",
    "isAlarmActive",
    "isTimerRunning",
    "isSpeakerOn",
    "isKeepingWarm",
    "isMonitoringIcebath",
}

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

proc: subprocess.Popen[str] | None = None
proc_lock = threading.RLock()
state_lock = threading.Lock()

is_cooking = False
current_target_temp = 55.0
current_timer = 0
last_published: dict[str, Any] = {}
last_send_time = 0.0
running = True

# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------

def terminate_process() -> None:
    global proc
    with proc_lock:
        if proc is None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=3)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        proc = None


def run_anova_stream() -> None:
    """Launch (or re-launch) the official interactive CLI and enter the telemetry stream."""
    global proc

    terminate_process()

    with proc_lock:
        try:
            proc = subprocess.Popen(
                ["python", "-O", "anova_interactive.py"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                close_fds=True,
            )
        except Exception as exc:
            log.error("Failed to start anova_interactive.py: %s", exc)
            return

    threading.Thread(target=read_output, args=(proc,), daemon=True).start()

    try:
        # Auth + device selection + enter message stream
        # Menu flow (as of current official CLI):
        #   1. Token prompt
        #   2. Device list → choose DEVICE_INDEX
        #   3. Main menu → 1 = Show message stream
        time.sleep(0.6)
        _write_stdin(f"{TOKEN}\n")
        time.sleep(2.5)
        _write_stdin(f"{DEVICE_INDEX}\n")
        time.sleep(1.2)
        _write_stdin("1\n")
        log.info("Telemetry stream started (device index %d)", DEVICE_INDEX)
    except Exception as exc:
        log.error("Failed to initialise telemetry stream: %s", exc)


def _write_stdin(data: str) -> None:
    with proc_lock:
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise RuntimeError("Process not running")
        proc.stdin.write(data)
        proc.stdin.flush()


def send_inline_command(inputs: list[str]) -> bool:
    """
    Inject a sequence of menu choices into the live CLI.
    Returns True on success, False on failure.
    """
    global proc

    with proc_lock:
        if proc is None or proc.poll() is not None:
            log.warning("Process not alive – restarting before command")
            run_anova_stream()
            time.sleep(1.5)
            if proc is None or proc.poll() is not None:
                log.error("Unable to restart process for command")
                return False

        try:
            # Exit current stream / return to menu
            _write_stdin("\n")
            time.sleep(0.6)

            for value in inputs:
                _write_stdin(f"{value}\n")
                time.sleep(0.55)

            # Return to message stream
            time.sleep(2.0)
            _write_stdin("\n")
            time.sleep(0.4)
            _write_stdin("1\n")
            return True
        except Exception as exc:
            log.error("Command injection failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# Output parser / publisher
# ---------------------------------------------------------------------------

def read_output(process: subprocess.Popen[str]) -> None:
    global is_cooking, current_target_temp, current_timer
    global last_published, last_send_time

    assert process.stdout is not None

    for line in iter(process.stdout.readline, ""):
        if not running:
            break
        if "{" not in line or "}" not in line:
            continue

        try:
            start = line.find("{")
            end = line.rfind("}") + 1
            raw = line[start:end]

            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                payload = ast.literal_eval(raw)

            if not isinstance(payload, dict):
                continue

            with state_lock:
                is_cooking = bool(payload.get("isCooking", False))
                if "targetTemperature" in payload:
                    current_target_temp = float(payload["targetTemperature"])
                if "timerInSeconds" in payload:
                    current_timer = int(payload["timerInSeconds"] or 0)

            now = time.time()
            changed = _state_changed(payload, last_published)

            if changed or (now - last_send_time >= HEARTBEAT_INTERVAL):
                pruned = {k: payload[k] for k in ESSENTIAL_KEYS if k in payload}

                if "currentJob" in payload and isinstance(payload["currentJob"], dict):
                    job = payload["currentJob"]
                    pruned["currentJob"] = {
                        k: job[k] for k in ("jobType", "jobStage") if k in job
                    }

                client.publish(STATE_TOPIC, json.dumps(pruned), qos=0)
                # Fuller diagnostic payload (not retained)
                client.publish(DIAG_TOPIC, json.dumps(payload), qos=0)

                last_published = payload
                last_send_time = now

        except Exception as exc:
            log.debug("Payload parse error: %s", exc)


def _state_changed(new: dict[str, Any], old: dict[str, Any]) -> bool:
    if not old:
        return True

    for key in ("targetTemperature", "isCooking", "isConnected", "isTimerRunning",
                "isAlarmActive", "isKeepingWarm"):
        if new.get(key) != old.get(key):
            return True

    try:
        old_t = float(old.get("currentTemperature", 0.0))
        new_t = float(new.get("currentTemperature", 0.0))
        if abs(new_t - old_t) >= TEMP_DEADBAND:
            return True
    except (TypeError, ValueError):
        return True

    # Publish on significant timer changes (≥ 30 s) to keep the sensor useful
    # without flooding the broker
    try:
        new_timer = int(new.get("timerInSeconds", 0) or 0)
        old_timer = int(old.get("timerInSeconds", 0) or 0)
        if abs(new_timer - old_timer) >= 30:
            return True
    except (TypeError, ValueError):
        pass

    return False


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

def watchdog_loop() -> None:
    while running:
        time.sleep(12)
        with proc_lock:
            if proc is not None and proc.poll() is not None:
                log.warning("anova_interactive.py exited – restarting")
                run_anova_stream()


# ---------------------------------------------------------------------------
# MQTT callbacks
# ---------------------------------------------------------------------------

def on_connect(client: mqtt.Client, userdata: Any, flags: dict, reason_code: int, properties=None) -> None:
    if reason_code != 0:
        log.error("MQTT connect failed (rc=%s)", reason_code)
        return

    log.info("Connected to MQTT broker %s:%s", BROKER, PORT)

    try:
        client._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass

    # Availability
    client.publish(AVAIL_TOPIC, "online", qos=1, retain=True)

    device_info = {
        "identifiers": ["anova_sous_vide_cooker"],
        "name": "Anova",
        "manufacturer": "Anova",
        "model": "Precision Cooker",
        "sw_version": "mqtt-bridge",
    }

    # Classic availability keys (widely compatible with HA)
    avail = {
        "availability_topic": AVAIL_TOPIC,
        "payload_available": "online",
        "payload_not_available": "offline",
    }

    configs: list[tuple[str, str, dict]] = [
        ("sensor", "current_temp", {
            "name": "Current Temperature",
            "unique_id": "anova_current_temperature",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.currentTemperature }}",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            **avail,
            "device": device_info,
        }),
        ("sensor", "target_temp", {
            "name": "Target Temperature",
            "unique_id": "anova_target_temperature",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.targetTemperature }}",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            **avail,
            "device": device_info,
        }),
        ("sensor", "timer_remaining", {
            "name": "Timer Remaining",
            "unique_id": "anova_timer_remaining",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.timerInSeconds }}",
            "device_class": "duration",
            "unit_of_measurement": "s",
            **avail,
            "device": device_info,
        }),
        ("sensor", "firmware", {
            "name": "Firmware Version",
            "unique_id": "anova_firmware",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.firmwareVersion }}",
            "entity_category": "diagnostic",
            **avail,
            "device": device_info,
        }),
        ("sensor", "unit", {
            "name": "Temperature Unit",
            "unique_id": "anova_unit",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.unit | upper }}",
            "entity_category": "diagnostic",
            **avail,
            "device": device_info,
        }),
        ("sensor", "job_type", {
            "name": "Job Type",
            "unique_id": "anova_job_type",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.currentJob.jobType if value_json.currentJob is defined else 'None' }}",
            "entity_category": "diagnostic",
            **avail,
            "device": device_info,
        }),
        ("sensor", "job_stage", {
            "name": "Job Stage",
            "unique_id": "anova_job_stage",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.currentJob.jobStage if value_json.currentJob is defined else 'None' }}",
            **avail,
            "device": device_info,
        }),
        ("binary_sensor", "is_cooking", {
            "name": "Cooking Status",
            "unique_id": "anova_is_cooking",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isCooking else 'OFF' }}",
            "device_class": "running",
            **avail,
            "device": device_info,
        }),
        ("binary_sensor", "connection", {
            "name": "Connection Status",
            "unique_id": "anova_connection",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isConnected else 'OFF' }}",
            "device_class": "connectivity",
            "entity_category": "diagnostic",
            **avail,
            "device": device_info,
        }),
        ("binary_sensor", "alarm", {
            "name": "Alarm Active",
            "unique_id": "anova_alarm",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isAlarmActive else 'OFF' }}",
            "device_class": "problem",
            "entity_category": "diagnostic",
            **avail,
            "device": device_info,
        }),
        ("binary_sensor", "timer_running", {
            "name": "Timer Running",
            "unique_id": "anova_timer_running",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isTimerRunning else 'OFF' }}",
            "device_class": "running",
            **avail,
            "device": device_info,
        }),
        ("binary_sensor", "speaker", {
            "name": "Speaker Status",
            "unique_id": "anova_speaker",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isSpeakerOn else 'OFF' }}",
            "entity_category": "diagnostic",
            **avail,
            "device": device_info,
        }),
        ("binary_sensor", "keeping_warm", {
            "name": "Keeping Warm",
            "unique_id": "anova_keeping_warm",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isKeepingWarm else 'OFF' }}",
            "device_class": "running",
            **avail,
            "device": device_info,
        }),
        ("binary_sensor", "ice_bath", {
            "name": "Ice Bath Monitoring",
            "unique_id": "anova_ice_bath",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isMonitoringIcebath else 'OFF' }}",
            **avail,
            "device": device_info,
        }),
        ("switch", "control", {
            "name": "Control",
            "unique_id": "anova_cooker_control",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isCooking else 'OFF' }}",
            "command_topic": SET_TOPIC,
            "payload_on": "start",
            "payload_off": "stop",
            "state_on": "ON",
            "state_off": "OFF",
            **avail,
            "device": device_info,
        }),
        ("number", "target_temp_number", {
            "name": "Target Temperature",
            "unique_id": "anova_temperature_target",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.targetTemperature }}",
            "command_topic": SET_TOPIC,
            "min": 20,
            "max": 95,
            "step": 0.1,
            "unit_of_measurement": "°C",
            **avail,
            "device": device_info,
        }),
    ]

    for component, object_id, payload in configs:
        topic = f"homeassistant/{component}/anova_cooker/{object_id}/config"
        client.publish(topic, json.dumps(payload), qos=1, retain=True)

    client.subscribe(SET_TOPIC, qos=0)
    log.info("Discovery configs published and subscribed to %s", SET_TOPIC)


def on_disconnect(client: mqtt.Client, userdata: Any, flags: dict, reason_code: int, properties=None) -> None:
    log.warning("Disconnected from MQTT (rc=%s)", reason_code)


def on_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    payload = msg.payload.decode("utf-8").strip()
    log.info("Command received: %s", payload)

    with state_lock:
        local_cooking = is_cooking
        local_target = current_target_temp
        local_timer = current_timer

    def process() -> None:
        global current_target_temp, current_timer

        # JSON command: {"command": "start"|"stop", "temp": 55.0, "timer": 3600}
        if payload.startswith("{"):
            try:
                data = json.loads(payload)
                temp = float(data.get("temp", local_target))
                timer = int(data.get("timer", local_timer))
                cmd = str(data.get("command", "")).lower()

                with state_lock:
                    current_target_temp = temp
                    current_timer = timer

                if cmd == "start":
                    ok = send_inline_command(["2", str(temp), str(timer)])
                    log.info("Start cook → temp=%.1f timer=%ds  success=%s", temp, timer, ok)
                elif cmd == "stop":
                    ok = send_inline_command(["3"])
                    log.info("Stop cook  success=%s", ok)
                return
            except Exception as exc:
                log.error("JSON command error: %s", exc)
                return

        # Plain-text fallbacks
        lower = payload.lower()
        if lower == "stop":
            ok = send_inline_command(["3"])
            log.info("Stop (text) success=%s", ok)
        elif lower == "start":
            ok = send_inline_command(["2", str(local_target), str(local_timer)])
            log.info("Start (text) success=%s", ok)
        else:
            try:
                temp = float(payload)
                if local_cooking:
                    ok = send_inline_command(["2", str(temp), str(local_timer)])
                    log.info("Set temp while cooking → %.1f  success=%s", temp, ok)
                else:
                    with state_lock:
                        current_target_temp = temp
                    log.info("Staged target temperature (off) → %.1f", temp)
            except ValueError:
                log.warning("Unrecognised command payload: %s", payload)

    threading.Thread(target=process, daemon=True).start()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def shutdown(signum=None, frame=None) -> None:
    global running
    log.info("Shutting down…")
    running = False
    try:
        client.publish(AVAIL_TOPIC, "offline", qos=1, retain=True)
    except Exception:
        pass
    terminate_process()
    try:
        client.disconnect()
    except Exception:
        pass
    sys.exit(0)


client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=CLIENT_ID,
    clean_session=True,
)
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.on_message = on_message

# Last Will – HA will mark entities unavailable if the bridge dies
client.will_set(AVAIL_TOPIC, "offline", qos=1, retain=True)

if USER and PASS:
    client.username_pw_set(USER, PASS)

signal.signal(signal.SIGTERM, shutdown)
signal.signal(signal.SIGINT, shutdown)

# Connect with retry
while running:
    try:
        log.info("Connecting to MQTT broker %s:%s …", BROKER, PORT)
        client.connect(BROKER, PORT, keepalive=60)
        break
    except Exception as exc:
        log.warning("MQTT connection failed (%s) – retrying in 5 s", exc)
        time.sleep(5)

run_anova_stream()
threading.Thread(target=watchdog_loop, daemon=True).start()

try:
    client.loop_forever()
except KeyboardInterrupt:
    shutdown()
