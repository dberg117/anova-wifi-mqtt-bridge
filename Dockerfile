FROM python:3.11-alpine

WORKDIR /app

RUN apk add --no-cache ca-certificates wget \
    && pip install --no-cache-dir websockets paho-mqtt \
    && wget -q -O anova_interactive.py \
         https://raw.githubusercontent.com/anova-culinary/developer-project-wifi/main/anova_interactive.py

COPY mqtt_bridge.py .

CMD ["python", "-O", "-u", "mqtt_bridge.py"]
