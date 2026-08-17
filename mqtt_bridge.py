#!/usr/bin/env python3
"""
Anova Wi-Fi → MQTT bridge for Home Assistant (WebSocket edition).

Connects directly to Anova's cloud WebSocket API, publishes state via MQTT
discovery, and accepts start / stop / target-temperature commands.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
import uuid
from typing import Any

import paho.mqtt.client as mqtt
import websockets
from websockets.exceptions import ConnectionClosed

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

WS_URI = (
    f"wss://devices.anovaculinary.io"
    f"?token={TOKEN}&supportedAccessories=APC,APO"
)

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

state_lock = threading.Lock()
current_state: dict[str, Any] = {}
last_published: dict[str, Any] = {}
last_send_time = 0.0

# Selected device
selected_cooker_id: str | None = None
selected_device_type: str | None = None
selected_name: str = "Anova"

# Command queue: MQTT thread → asyncio loop
command_queue: asyncio.Queue | None = None
main_loop: asyncio.AbstractEventLoop | None = None

running = True

# ---------------------------------------------------------------------------
# MQTT client (runs in its own thread)
# ---------------------------------------------------------------------------

def on_mqtt_connect(client: mqtt.Client, userdata: Any, flags: dict, reason_code: int, properties=None) -> None:
    if reason_code != 0:
        log.error("MQTT connect failed (rc=%s)", reason_code)
        return

    log.info("Connected to MQTT broker %s:%s", BROKER, PORT)
    try:
        client._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception:
        pass

    client.publish(AVAIL_TOPIC, "online", qos=1, retain=True)
    publish_discovery(client)
    client.subscribe(SET_TOPIC, qos=0)
    log.info("MQTT discovery published, subscribed to %s", SET_TOPIC)


def on_mqtt_disconnect(client: mqtt.Client, userdata: Any, flags: dict, reason_code: int, properties=None) -> None:
    log.warning("Disconnected from MQTT (rc=%s)", reason_code)


def on_mqtt_message(client: mqtt.Client, userdata: Any, msg: mqtt.MQTTMessage) -> None:
    payload = msg.payload.decode("utf-8").strip()
    log.info("MQTT command received: %s", payload)

    if main_loop is None or command_queue is None:
        log.warning("Asyncio loop not ready – dropping command")
        return

    asyncio.run_coroutine_threadsafe(command_queue.put(payload), main_loop)


def publish_discovery(client: mqtt.Client) -> None:
    device_info = {
        "identifiers": ["anova_sous_vide_cooker"],
        "name": selected_name or "Anova",
        "manufacturer": "Anova",
        "model": "Precision Cooker",
        "sw_version": "mqtt-bridge-ws",
    }
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
            **avail, "device": device_info,
        }),
        ("sensor", "target_temp", {
            "name": "Target Temperature",
            "unique_id": "anova_target_temperature",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.targetTemperature }}",
            "device_class": "temperature",
            "unit_of_measurement": "°C",
            **avail, "device": device_info,
        }),
        ("sensor", "timer_remaining", {
            "name": "Timer Remaining",
            "unique_id": "anova_timer_remaining",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.timerInSeconds }}",
            "device_class": "duration",
            "unit_of_measurement": "s",
            **avail, "device": device_info,
        }),
        ("sensor", "firmware", {
            "name": "Firmware Version",
            "unique_id": "anova_firmware",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.firmwareVersion }}",
            "entity_category": "diagnostic",
            **avail, "device": device_info,
        }),
        ("sensor", "unit", {
            "name": "Temperature Unit",
            "unique_id": "anova_unit",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.unit | upper }}",
            "entity_category": "diagnostic",
            **avail, "device": device_info,
        }),
        ("sensor", "job_type", {
            "name": "Job Type",
            "unique_id": "anova_job_type",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.currentJob.jobType if value_json.currentJob is defined else 'None' }}",
            "entity_category": "diagnostic",
            **avail, "device": device_info,
        }),
        ("sensor", "job_stage", {
            "name": "Job Stage",
            "unique_id": "anova_job_stage",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ value_json.currentJob.jobStage if value_json.currentJob is defined else 'None' }}",
            **avail, "device": device_info,
        }),
        ("binary_sensor", "is_cooking", {
            "name": "Cooking Status",
            "unique_id": "anova_is_cooking",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isCooking else 'OFF' }}",
            "device_class": "running",
            **avail, "device": device_info,
        }),
        ("binary_sensor", "connection", {
            "name": "Connection Status",
            "unique_id": "anova_connection",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isConnected else 'OFF' }}",
            "device_class": "connectivity",
            "entity_category": "diagnostic",
            **avail, "device": device_info,
        }),
        ("binary_sensor", "alarm", {
            "name": "Alarm Active",
            "unique_id": "anova_alarm",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isAlarmActive else 'OFF' }}",
            "device_class": "problem",
            "entity_category": "diagnostic",
            **avail, "device": device_info,
        }),
        ("binary_sensor", "timer_running", {
            "name": "Timer Running",
            "unique_id": "anova_timer_running",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isTimerRunning else 'OFF' }}",
            "device_class": "running",
            **avail, "device": device_info,
        }),
        ("binary_sensor", "speaker", {
            "name": "Speaker Status",
            "unique_id": "anova_speaker",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isSpeakerOn else 'OFF' }}",
            "entity_category": "diagnostic",
            **avail, "device": device_info,
        }),
        ("binary_sensor", "keeping_warm", {
            "name": "Keeping Warm",
            "unique_id": "anova_keeping_warm",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isKeepingWarm else 'OFF' }}",
            "device_class": "running",
            **avail, "device": device_info,
        }),
        ("binary_sensor", "ice_bath", {
            "name": "Ice Bath Monitoring",
            "unique_id": "anova_ice_bath",
            "state_topic": STATE_TOPIC,
            "value_template": "{{ 'ON' if value_json.isMonitoringIcebath else 'OFF' }}",
            **avail, "device": device_info,
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
            **avail, "device": device_info,
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
            **avail, "device": device_info,
        }),
    ]

    for component, object_id, payload in configs:
        topic = f"homeassistant/{component}/anova_cooker/{object_id}/config"
        client.publish(topic, json.dumps(payload), qos=1, retain=True)


def publish_state(force: bool = False) -> None:
    """Publish current_state to MQTT if it changed meaningfully."""
    global last_published, last_send_time

    with state_lock:
        if not current_state:
            return
        payload = dict(current_state)

    now = time.time()
    changed = _state_changed(payload, last_published)

    if not (changed or force or (now - last_send_time >= HEARTBEAT_INTERVAL)):
        return

    essential = {
        "currentTemperature", "targetTemperature", "timerInSeconds",
        "firmwareVersion", "unit", "isCooking", "isConnected",
        "isAlarmActive", "isTimerRunning", "isSpeakerOn",
        "isKeepingWarm", "isMonitoringIcebath",
    }
    pruned = {k: payload[k] for k in essential if k in payload}
    if "currentJob" in payload and isinstance(payload["currentJob"], dict):
        job = payload["currentJob"]
        pruned["currentJob"] = {k: job[k] for k in ("jobType", "jobStage") if k in job}

    mqtt_client.publish(STATE_TOPIC, json.dumps(pruned), qos=0)
    mqtt_client.publish(DIAG_TOPIC, json.dumps(payload), qos=0)

    last_published = payload
    last_send_time = now


def _state_changed(new: dict[str, Any], old: dict[str, Any]) -> bool:
    if not old:
        return True
    for key in ("targetTemperature", "isCooking", "isConnected", "isTimerRunning",
                "isAlarmActive", "isKeepingWarm"):
        if new.get(key) != old.get(key):
            return True
    try:
        if abs(float(new.get("currentTemperature", 0)) - float(old.get("currentTemperature", 0))) >= TEMP_DEADBAND:
            return True
    except (TypeError, ValueError):
        return True
    try:
        if abs(int(new.get("timerInSeconds", 0) or 0) - int(old.get("timerInSeconds", 0) or 0)) >= 30:
            return True
    except (TypeError, ValueError):
        pass
    return False


mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=CLIENT_ID,
    clean_session=True,
)
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_disconnect = on_mqtt_disconnect
mqtt_client.on_message = on_mqtt_message
mqtt_client.will_set(AVAIL_TOPIC, "offline", qos=1, retain=True)
if USER and PASS:
    mqtt_client.username_pw_set(USER, PASS)

# ---------------------------------------------------------------------------
# State normalisation (WebSocket → flat dict used by HA)
# ---------------------------------------------------------------------------

def normalize_state(raw: dict[str, Any]) -> dict[str, Any]:
    """
    Turn the various Anova state payload shapes into the flat dict the
    previous CLI bridge published (so existing HA discovery keeps working).
    """
    out: dict[str, Any] = {
        "isConnected": True,  # if we received a state message, we are connected
    }

    state = raw.get("state") or raw
    job = state.get("job") or state.get("currentJob") or {}

    # Temperature
    for key in ("currentTemperature", "current_temp", "waterTemperature"):
        if key in state:
            out["currentTemperature"] = float(state[key])
            break
    if "currentTemperature" not in out and "temperature" in state:
        out["currentTemperature"] = float(state["temperature"])

    for key in ("targetTemperature", "target_temp", "setpoint"):
        if key in state:
            out["targetTemperature"] = float(state[key])
            break
    if "targetTemperature" not in out and isinstance(job, dict):
        for key in ("targetTemperature", "temperature"):
            if key in job:
                out["targetTemperature"] = float(job[key])
                break

    # Timer
    for key in ("timerInSeconds", "timer", "remainingTime"):
        if key in state:
            out["timerInSeconds"] = int(state[key] or 0)
            break
    if "timerInSeconds" not in out and isinstance(job, dict):
        t = job.get("timer") or job.get("remaining")
        if t is not None:
            out["timerInSeconds"] = int(t)

    # Booleans / status
    mode = str(state.get("mode") or state.get("status") or "").lower()
    out["isCooking"] = bool(
        state.get("isCooking")
        or state.get("is_running")
        or mode in ("cook", "cooking", "running", "heat")
    )
    out["isTimerRunning"] = bool(state.get("isTimerRunning") or state.get("timerRunning"))
    out["isAlarmActive"] = bool(state.get("isAlarmActive") or state.get("alarmActive"))
    out["isSpeakerOn"] = bool(state.get("isSpeakerOn", state.get("speakerOn", True)))
    out["isKeepingWarm"] = bool(state.get("isKeepingWarm") or mode == "keep warm")
    out["isMonitoringIcebath"] = bool(state.get("isMonitoringIcebath"))

    # Unit & firmware
    unit = state.get("unit") or state.get("temperatureUnit") or "C"
    out["unit"] = str(unit).upper()[:1]
    out["firmwareVersion"] = state.get("firmwareVersion") or state.get("firmware") or ""

    # Job info
    if isinstance(job, dict) and job:
        out["currentJob"] = {
            "jobType": job.get("jobType") or job.get("type") or "cook",
            "jobStage": job.get("jobStage") or job.get("stage") or job.get("status") or "",
        }

    return out


# ---------------------------------------------------------------------------
# WebSocket helpers
# ---------------------------------------------------------------------------

def make_request_id() -> str:
    return str(uuid.uuid4())


async def send_ws_command(ws: Any, command: str, payload: dict) -> None:
    msg = {
        "command": command,
        "requestId": make_request_id(),
        "payload": payload,
    }
    await ws.send(json.dumps(msg))
    log.info("Sent WS command: %s", command)


async def handle_mqtt_command(ws: Any, raw: str) -> None:
    """Translate an MQTT payload into a WebSocket command."""
    global current_state

    if selected_cooker_id is None or selected_device_type is None:
        log.warning("No device selected yet – cannot send command")
        return

    with state_lock:
        local_target = current_state.get("targetTemperature", 55.0)
        local_timer = current_state.get("timerInSeconds", 0)
        local_cooking = current_state.get("isCooking", False)

    # JSON form: {"command":"start"|"stop", "temp":56.5, "timer":3600}
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            temp = float(data.get("temp", local_target))
            timer = int(data.get("timer", local_timer))
            cmd = str(data.get("command", "")).lower()

            if cmd == "start":
                await send_ws_command(ws, "CMD_APC_START", {
                    "cookerId": selected_cooker_id,
                    "type": selected_device_type,
                    "targetTemperature": temp,
                    "unit": "C",
                    "timer": timer,
                })
            elif cmd == "stop":
                await send_ws_command(ws, "CMD_APC_STOP", {
                    "cookerId": selected_cooker_id,
                    "type": selected_device_type,
                })
            return
        except Exception as exc:
            log.error("JSON command error: %s", exc)
            return

    lower = raw.lower()
    if lower == "stop":
        await send_ws_command(ws, "CMD_APC_STOP", {
            "cookerId": selected_cooker_id,
            "type": selected_device_type,
        })
    elif lower == "start":
        await send_ws_command(ws, "CMD_APC_START", {
            "cookerId": selected_cooker_id,
            "type": selected_device_type,
            "targetTemperature": local_target,
            "unit": "C",
            "timer": local_timer,
        })
    else:
        # Plain number → set target temperature
        try:
            temp = float(raw)
            if local_cooking:
                await send_ws_command(ws, "CMD_APC_START", {
                    "cookerId": selected_cooker_id,
                    "type": selected_device_type,
                    "targetTemperature": temp,
                    "unit": "C",
                    "timer": local_timer,
                })
            else:
                with state_lock:
                    current_state["targetTemperature"] = temp
                publish_state(force=True)
                log.info("Staged target temperature (cooker off) → %.1f", temp)
        except ValueError:
            log.warning("Unrecognised command: %s", raw)


async def process_ws_message(data: dict[str, Any]) -> None:
    """Handle one inbound WebSocket message."""
    global selected_cooker_id, selected_device_type, selected_name, current_state

    command = data.get("command", "")

    # Device list
    if command in ("EVENT_APC_WIFI_LIST", "EVENT_APO_WIFI_LIST"):
        devices = data.get("payload") or []
        if not devices:
            log.warning("Received empty device list")
            return

        idx = max(0, min(DEVICE_INDEX - 1, len(devices) - 1))
        dev = devices[idx]
        selected_cooker_id = dev.get("cookerId") or dev.get("id")
        selected_device_type = dev.get("type") or "unknown"
        selected_name = dev.get("name") or "Anova"
        log.info(
            "Selected device [%d/%d]: %s (%s) id=%s",
            idx + 1, len(devices), selected_name, selected_device_type, selected_cooker_id,
        )
        return

    # State updates
    if command in (
        "EVENT_APC_STATE",
        "EVENT_APC_WIFI_STATE",
        "CMD_APC_STATE",
        "EVENT_STATE",
    ) or "state" in (data.get("payload") or {}):
        payload = data.get("payload") or data
        if "state" in payload and isinstance(payload["state"], dict):
            merged = dict(payload)
            merged.update(payload["state"])
            normalized = normalize_state(merged)
        else:
            normalized = normalize_state(payload)

        with state_lock:
            current_state.update(normalized)
            current_state["isConnected"] = True

        publish_state()
        return

    if command.startswith("RESPONSE") or command.startswith("CMD_"):
        log.debug("WS response: %s", data)
        return

    log.debug("Unhandled WS message: %s", command)


# ---------------------------------------------------------------------------
# Main WebSocket loop
# ---------------------------------------------------------------------------

async def ws_main() -> None:
    global command_queue, main_loop, running

    main_loop = asyncio.get_running_loop()
    command_queue = asyncio.Queue()

    def mqtt_thread() -> None:
        while running:
            try:
                log.info("Connecting to MQTT broker %s:%s …", BROKER, PORT)
                mqtt_client.connect(BROKER, PORT, keepalive=60)
                mqtt_client.loop_forever()
            except Exception as exc:
                log.warning("MQTT error: %s – retrying in 5 s", exc)
                time.sleep(5)

    threading.Thread(target=mqtt_thread, daemon=True).start()

    backoff = 1
    while running:
        try:
            log.info("Connecting to Anova WebSocket …")
            async with websockets.connect(
                WS_URI,
                ping_interval=20,
                ping_timeout=20,
                close_timeout=5,
                max_size=2**20,
            ) as ws:
                log.info("WebSocket connected")
                backoff = 1

                async def reader() -> None:
                    async for raw in ws:
                        if not running:
                            break
                        try:
                            data = json.loads(raw)
                            await process_ws_message(data)
                        except Exception as exc:
                            log.debug("WS message parse error: %s", exc)

                async def command_worker() -> None:
                    while running:
                        try:
                            raw = await asyncio.wait_for(command_queue.get(), timeout=1.0)
                            await handle_mqtt_command(ws, raw)
                        except asyncio.TimeoutError:
                            continue
                        except Exception as exc:
                            log.error("Command worker error: %s", exc)

                await asyncio.gather(reader(), command_worker())

        except ConnectionClosed as exc:
            log.warning("WebSocket closed: %s", exc)
        except Exception as exc:
            log.error("WebSocket error: %s", exc)

        if not running:
            break

        log.info("Reconnecting in %d s …", backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 60)


def shutdown(signum=None, frame=None) -> None:
    global running
    log.info("Shutting down …")
    running = False
    try:
        mqtt_client.publish(AVAIL_TOPIC, "offline", qos=1, retain=True)
        mqtt_client.disconnect()
    except Exception:
        pass
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        asyncio.run(ws_main())
    except KeyboardInterrupt:
        shutdown()
