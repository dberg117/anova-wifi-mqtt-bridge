#!/bin/sh
cd /app

if [ ! -d "/app/deps" ]; then
    echo "=== Initial Setup: Downloading Alpine Packages ==="
    apk update -qq && apk add -q --no-cache ca-certificates > /dev/null 2>&1
    python -u -m pip install -q --target=/app/deps websockets paho-mqtt > /dev/null 2>&1
else
    echo "=== Cache Found: Skipping Alpine Network Downloads ==="
fi

export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
export PYTHONPATH=/app/deps

exec python -O mqtt_bridge.py
