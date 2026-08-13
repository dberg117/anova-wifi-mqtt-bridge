FROM python:3.11-alpine
WORKDIR /app
RUN apk update && apk add --no-cache ca-certificates wget
RUN pip install --no-cache-dir websockets paho-mqtt
RUN wget -q -O anova_interactive.py https://raw.githubusercontent.com/anova-culinary/developer-project-wifi/main/anova_interactive.py
COPY mqtt_bridge.py ./
CMD ["python", "-O", "mqtt_bridge.py"]
