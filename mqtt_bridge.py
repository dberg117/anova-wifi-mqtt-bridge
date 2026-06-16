import os
import sys
import json
import ast
import time
import subprocess
import threading
import warnings
import socket
import paho.mqtt.client as mqtt

warnings.filterwarnings("ignore", category=DeprecationWarning)

try:
    BROKER = os.environ["MQTT_BROKER"]
    PORT = int(os.environ.get("MQTT_PORT", 1883))
    USER = os.environ.get("MQTT_USER", "")
    PASS = os.environ.get("MQTT_PASS", "")
    TOKEN = os.environ["ANOVA_TOKEN"]
except KeyError as e:
    print(f"!!! Configuration Error: Missing Environment Variable {e} !!!", flush=True)
    sys.exit(1)

proc = None
lock = threading.Lock()
state_lock = threading.Lock()

is_cooking = False
current_target_temp = 55.0

essential_keys = {
    "currentTemperature", "targetTemperature", "timerInSeconds", 
    "firmwareVersion", "unit", "isCooking", "isConnected", 
    "isAlarmActive", "isTimerRunning", "isSpeakerOn", 
    "isKeepingWarm", "isMonitoringIcebath"
}

def read_output(process):
    global is_cooking, current_target_temp
    last_send_time = 0.0  
    last_published_state = {} 
    
    for line in iter(process.stdout.readline, ""):
        if "{" in line and "}" in line:
            try:
                start = line.find("{")
                end = line.rfind("}") + 1
                payload = ast.literal_eval(line[start:end])
                
                with state_lock:
                    is_cooking = payload.get("isCooking", False)
                    if "targetTemperature" in payload and is_cooking:
                        current_target_temp = payload["targetTemperature"]
                
                current_time = time.time()
                state_changed = False
                
                if not last_published_state:
                    state_changed = True
                else:
                    for k in ["targetTemperature", "isCooking", "isConnected"]:
                        if payload.get(k) != last_published_state.get(k):
                            state_changed = True
                            break
                    
                    if not state_changed:
                        try:
                            old_temp = float(last_published_state.get("currentTemperature", 0.0))
                            new_temp = float(payload.get("currentTemperature", 0.0))
                            if abs(new_temp - old_temp) >= 0.2:
                                state_changed = True
                        except (ValueError, TypeError):
                            state_changed = True
                
                if state_changed or (current_time - last_send_time >= 300.0):
                    if "currentTemperature" in payload and "targetTemperature" in payload:
                        pruned_payload = {k: payload[k] for k in essential_keys if k in payload}
                        if "currentJob" in payload and isinstance(payload["currentJob"], dict):
                            pruned_payload["currentJob"] = {
                                k: payload["currentJob"][k] 
                                for k in ["jobType", "jobStage"] if k in payload["currentJob"]
                            }
                        
                        client.publish("anova/cooker/state", json.dumps(pruned_payload), qos=0)
                        last_published_state = payload
                        last_send_time = current_time
            except Exception: pass

def run_anova_stream():
    global proc
    with lock:
        if proc:
            try: proc.terminate(); proc.wait()
            except: pass
            
        proc = subprocess.Popen(
            ["python", "-O", "/app/anova_interactive.py"], 
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, close_fds=True 
        )
    
    threading.Thread(target=read_output, args=(proc,), daemon=True).start()
    
    try:
        time.sleep(0.5)
        proc.stdin.write(f"{TOKEN}\n")
        proc.stdin.flush()
        time.sleep(2.5) 
        proc.stdin.write("1\n") 
        proc.stdin.flush()
        time.sleep(1.0) 
        proc.stdin.write("1\n") 
        proc.stdin.flush()
        print("=== Permanent Telemetry Stream Established Cleanly ===", flush=True)
    except Exception as err:
        print(f"!!! Telemetry Stream Error: {err} !!!", flush=True)

def watchdog_loop():
    while True:
        time.sleep(10)
        with lock:
            if proc and proc.poll() is not None:
                print("!!! Detected script crash. Auto-recovering... !!!", flush=True)
                run_anova_stream()

def send_inline_command(inputs):
    global proc
    with lock:
        if not proc or proc.poll() is not None:
            run_anova_stream()
            return
            
        try:
            proc.stdin.write("\n")
            proc.stdin.flush()
            time.sleep(0.5) 
            
            for inp in inputs:
                proc.stdin.write(f"{inp}\n")
                proc.stdin.flush()
                time.sleep(0.5) 
                
            time.sleep(2.5) 
            proc.stdin.write("\n")
            proc.stdin.flush()
            time.sleep(0.5)
            
            proc.stdin.write("1\n")
            proc.stdin.flush()
        except Exception: pass

def on_connect(client, userdata, flags, reason_code, properties=None):
    try:
        client._sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    except Exception: pass
    client.subscribe("anova/cooker/set", qos=0)

def on_message(client, userdata, msg):
    global is_cooking, current_target_temp
    payload = msg.payload.decode("utf-8").strip()
    
    with state_lock:
        local_cooking = is_cooking
        local_target = current_target_temp

    if payload.lower() == "stop":
        send_inline_command(["3"]) 
    elif payload.lower() == "start":
        send_inline_command(["2", str(local_target), "0"]) 
    else:
        try:
            temp = float(payload)
            if local_cooking:
                send_inline_command(["2", str(temp), "0"])
            else:
                with state_lock:
                    current_target_temp = temp
        except ValueError: pass

client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message

if USER and PASS: client.username_pw_set(USER, PASS)

while True:
    try:
        client.connect(BROKER, PORT, 60)
        client.loop_start() 
        break
    except Exception:
        time.sleep(5)

run_anova_stream()
threading.Thread(target=watchdog_loop, daemon=True).start()

threading.Event().wait()
