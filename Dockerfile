FROM python:3.11-alpine
WORKDIR /app
RUN apk update && apk add --no-cache ca-certificates
RUN pip install --no-cache-dir websockets paho-mqtt
COPY mqtt_bridge.py anova_interactive.py ./
CMD ["python", "-O", "mqtt_bridge.py"]
