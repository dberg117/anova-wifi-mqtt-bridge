FROM python:3.11-alpine
 
WORKDIR /app
 
RUN apk add --no-cache ca-certificates \
    && pip install --no-cache-dir websockets paho-mqtt
 
COPY mqtt_bridge.py .

CMD ["python", "-O", "-u", "mqtt_bridge.py"]
