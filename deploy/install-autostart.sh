#!/usr/bin/env bash
# Instala arranque automático de FulfillPro (Docker + Compose) al encender la VM.
# Uso (en el servidor, como root o con sudo):
#   cd /opt/fulfillpro
#   sudo bash deploy/install-autostart.sh
set -euo pipefail

APP_DIR="${FULFILLPRO_DIR:-/opt/fulfillpro}"
UNIT_SRC="${APP_DIR}/deploy/systemd/fulfillpro.service"
UNIT_DST="/etc/systemd/system/fulfillpro.service"

if [[ ! -d "$APP_DIR" ]]; then
  echo "ERROR: no existe $APP_DIR. Clona el repo ahí o exporta FULFILLPRO_DIR=..."
  exit 1
fi
if [[ ! -f "$UNIT_SRC" ]]; then
  echo "ERROR: no existe $UNIT_SRC"
  exit 1
fi
if [[ ! -f "$APP_DIR/docker-compose.yml" ]]; then
  echo "ERROR: no hay docker-compose.yml en $APP_DIR"
  exit 1
fi

# Docker al boot
systemctl enable docker
systemctl start docker || true

# Unidad systemd del stack
sed "s|/opt/fulfillpro|${APP_DIR}|g" "$UNIT_SRC" > /tmp/fulfillpro.service
# Detectar binario docker compose
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: docker no está instalado"
  exit 1
fi
if docker compose version >/dev/null 2>&1; then
  COMPOSE_CMD="/usr/bin/docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE_BIN="$(command -v docker-compose)"
  COMPOSE_CMD="$COMPOSE_BIN"
  # Ajustar unit a docker-compose clásico
  sed -i "s|/usr/bin/docker compose|${COMPOSE_BIN}|g" /tmp/fulfillpro.service
else
  echo "ERROR: no hay 'docker compose' ni 'docker-compose'"
  exit 1
fi

install -m 644 /tmp/fulfillpro.service "$UNIT_DST"
systemctl daemon-reload
systemctl enable fulfillpro.service
systemctl start fulfillpro.service

echo "OK: Docker y FulfillPro arrancarán al prender el servidor."
echo "    systemctl status fulfillpro"
echo "    docker compose -f ${APP_DIR}/docker-compose.yml ps"
