# Instructivo de despliegue — FulfillPro en Proxmox + Debian + Docker + Cloudflared

Guía paso a paso para levantar **FulfillPro** en un servidor local (homelab / oficina) con:

| Capa | Tecnología |
|------|------------|
| Hipervisor | **Proxmox VE** |
| SO invitado | **Debian 10 (Buster)** *o superior recomendado* |
| Contenedores | **Docker + Docker Compose** |
| Exposición a Internet | **Cloudflare Tunnel (`cloudflared`)** + dominio propio |

> **Objetivo:** la app queda accesible en **`https://fulfillpro.app`** (Cloudflare Tunnel) **sin abrir puertos** en el router (no hace falta reenvío 80/443).  
> Al **encender la VM**, Docker y el stack FulfillPro deben levantarse solos (ver §4.8).

---

## 0. Antes de empezar — checklist

- [ ] Proxmox funcionando y con espacio en disco (ideal ≥ **100 GB** libres en el datastore).
- [ ] Dominio pagado y **ya en Cloudflare** (nameservers del dominio apuntando a Cloudflare).
- [ ] Cuenta Cloudflare con el dominio activo (plan Free alcanza para Tunnel).
- [ ] Acceso a la consola de Proxmox y SSH a la VM.
- [ ] Imagen ISO de Debian (ver nota abajo).
- [ ] Código de FulfillPro (repo Git o carpeta copiada al servidor).

### Nota importante sobre Debian 10

**Debian 10 (Buster) está en fin de vida (EOL).** Funciona, pero:

- Ya no recibe parches de seguridad del proyecto Debian de forma normal.
- Algunos repositorios de Docker exigen ajustes extra.

**Recomendación fuerte:** si puedes, crea la VM con **Debian 12 (Bookworm)** o **Debian 11 (Bullseye)**. Los pasos de Docker y cloudflared son casi idénticos y más estables.

Si **debes** usar Debian 10, sigue este documento tal cual; donde haya diferencia se indica *solo Buster*.

### Recursos recomendados de la VM

Perfil alineado con el `docker-compose` de FulfillPro (~12 GB stack):

| Recurso | Mínimo usable | Recomendado producción |
|---------|----------------|------------------------|
| vCPU | 4 | 6–8 |
| RAM | 8 GB | **12–16 GB** |
| Disco | 40 GB | **100–150 GB** (SSD/NVMe) |
| Red | Bridge (`vmbr0`) | Bridge con IP fija LAN |

Distribución aproximada de RAM en contenedores:

- API ~2 GB · Worker Excel ~6 GB · PostgreSQL ~3 GB · Redis ~0.5 GB · SO ~1 GB

---

## 1. Crear la VM en Proxmox

### 1.1 Subir ISO

1. En Proxmox: **local (pve) → ISO Images → Upload**.
2. Sube `debian-10.x.x-amd64-netinst.iso` (o Debian 12 si usas la recomendación).

### 1.2 Crear VM

1. **Create VM**.
2. **General**
   - Name: `fulfillpro`
   - ID: el que prefieras (ej. `120`)
3. **OS**
   - ISO: la de Debian
   - Type: Linux · Kernel 5.x / 2.6
4. **System**
   - SCSI Controller: `VirtIO SCSI single`
   - Qemu Agent: **Enabled** (recomendado)
5. **Disks**
   - Bus/Device: `SCSI`
   - Disk size: **100 GB** (o más)
   - Discard: on (si usas thin + SSD)
6. **CPU**
   - Cores: **6** (o 4 mínimo)
   - Type: `host` (mejor rendimiento)
7. **Memory**
   - **12288 MB** (12 GB) o 16384 MB
8. **Network**
   - Bridge: `vmbr0`
   - Model: `VirtIO`
9. Finish → **Start**.

### 1.3 Instalar Debian (asistente)

Opciones recomendadas en el instalador:

- Language / Location: tu preferencia
- Hostname: `fulfillpro`
- Domain name: vacío o tu dominio interno
- Usuario no-root + contraseña fuerte
- **Software selection:** marca al menos:
  - ☑ SSH server  
  - ☑ standard system utilities  
  - ☐ Desktop environment (no hace falta GUI)
- Disco: usar todo el disco con LVM o partición simple `/`

Al terminar, reinicia y anota la **IP LAN** (ej. `192.168.1.50`).

### 1.4 Primer acceso y agente QEMU (opcional)

Desde tu PC:

```bash
ssh usuario@IP_DE_LA_VM
```

```bash
sudo apt update
sudo apt install -y qemu-guest-agent
sudo systemctl enable --now qemu-guest-agent
```

En Proxmox: Options → QEMU Guest Agent → Enabled (si no lo dejaste al crear).

---

## 2. Preparar el sistema base (Debian)

### 2.1 Actualizar paquetes

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install -y \
  ca-certificates curl gnupg lsb-release \
  git ufw fail2ban htop unzip
```

### 2.2 Firewall local (UFW)

Solo SSH desde la LAN (ajusta la red). **No abras 8000 a Internet**: Cloudflared sale hacia Cloudflare.

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from 192.168.0.0/16 to any port 22 proto tcp
# Si tu LAN es 10.x:
# sudo ufw allow from 10.0.0.0/8 to any port 22 proto tcp
sudo ufw enable
sudo ufw status
```

> Cloudflared **no necesita** puertos entrantes. Solo necesita salida HTTPS (443) a Cloudflare.

### 2.3 Zona horaria y locale (opcional)

```bash
sudo timedatectl set-timezone America/Bogota   # o tu zona
timedatectl
```

### 2.4 Usuario para Docker (recomendado)

```bash
sudo usermod -aG docker $USER
# Cierra sesión SSH y vuelve a entrar para que aplique el grupo
```

*(El grupo `docker` se crea al instalar Docker en el paso 3.)*

---

## 3. Instalar Docker Engine + Compose

### 3.1 Debian 11 / 12 (recomendado)

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

### 3.2 Solo Debian 10 (Buster)

Buster ya no está en el canal “stable” actual de Docker. Opciones:

**Opción A — script oficial (rápida):**

```bash
curl -fsSL https://get.docker.com | sudo sh
```

**Opción B — paquete del repo Docker archivado / versión soportada en Buster:**

Si `get.docker.com` falla, usa el repositorio con codename `buster`:

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg \
  | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/debian buster stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
```

Si `docker-compose-plugin` no existe en tu mirror, instala Compose v2 standalone:

```bash
DOCKER_COMPOSE_VERSION=v2.29.7
sudo curl -L \
  "https://github.com/docker/compose/releases/download/${DOCKER_COMPOSE_VERSION}/docker-compose-linux-x86_64" \
  -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose
# Usarás: docker-compose up -d   (con guion)
```

### 3.3 Verificar

```bash
sudo systemctl enable --now docker
docker --version
docker compose version   # o: docker-compose --version
sudo docker run --rm hello-world
```

Añade tu usuario al grupo docker y reabre SSH:

```bash
sudo usermod -aG docker $USER
```

---

## 4. Desplegar FulfillPro con Docker

### 4.1 Directorio de la aplicación

```bash
sudo mkdir -p /opt/fulfillpro
sudo chown $USER:$USER /opt/fulfillpro
cd /opt/fulfillpro
```

**Con Git:**

```bash
git clone <URL_DE_TU_REPO> .
# o copia el proyecto con scp/rsync desde tu PC
```

**Desde tu PC Windows (ejemplo rsync/scp):**

```bash
# en PowerShell / WSL, desde la carpeta del proyecto:
scp -r .\* usuario@IP_VM:/opt/fulfillpro/
```

Estructura mínima esperada en `/opt/fulfillpro`:

```
Dockerfile
docker-compose.yml
.env.example
requirements.txt
backend/
frontend/
samples/
```

### 4.2 Archivo `.env` de producción

Si el repositorio trae un `.env` temporal (solo para el primer despliegue), **no hace falta crearlo**:

```bash
cd /opt/fulfillpro
ls -la .env    # debe existir tras git pull
```

Si no existe:

```bash
cp .env.example .env
nano .env   # o vim
```

> **Seguridad:** el `.env` no debe quedarse en Git. Tras el primer arranque, elimínalo del repo en un commit posterior y rota `JWT_SECRET` / `ADMIN_PASSWORD`.

Genera secretos fuertes:

```bash
# JWT_SECRET (≥ 32 chars aleatorios)
openssl rand -base64 48

# Contraseña admin plataforma
openssl rand -base64 18
```

**Plantilla recomendada** (ajusta dominio y secretos):

```env
# ── Base de datos (red Docker interna: host = db) ──
DATABASE_URL=postgresql+psycopg://fulfillpro:CAMBIA_PASSWORD_DB_LARGA@db:5432/fulfillpro

# ── Redis ──
REDIS_URL=redis://redis:6379/0
REDIS_MAX_CONNECTIONS=100

# ── Seguridad ──
JWT_SECRET=PEGA_AQUI_EL_openssl_rand_base64_48
JWT_EXPIRE_MINUTES=60
JWT_REFRESH_EXPIRE_DAYS=14

ADMIN_EMAIL=tu-admin@tu-dominio.com
ADMIN_PASSWORD=PEGA_PASSWORD_ADMIN_FUERTE
ADMIN_NAME=Administrador FulfillPro

# ── Storage ──
STORAGE_ROOT=/app/storage
STORAGE_MAX_GB=100
MAX_UPLOAD_MB=25

# ── Producción ──
APP_ENV=production
# Dominio público HTTPS vía Cloudflare Tunnel
CORS_ORIGINS=https://fulfillpro.app,https://www.fulfillpro.app
ALLOW_PUBLIC_REGISTER=false
SEED_DEMO_USERS=false

MAX_ROWS=60000
MAX_CANT_COLS=60

UVICORN_WORKERS=4
DB_POOL_SIZE=12
DB_MAX_OVERFLOW=8
PROCESS_MAX_QUEUE=500
WORKER_CONCURRENCY=3

RATE_LIMIT_LOGIN=10
RATE_LIMIT_PROCESS=150
RATE_LIMIT_PROCESS_IP=500
```

**Crítico en `production`:**

| Variable | Regla |
|----------|--------|
| `APP_ENV` | `production` (la app **no arranca** con secretos débiles) |
| `JWT_SECRET` | ≥ 32 caracteres, no valores de ejemplo |
| `ADMIN_PASSWORD` | ≥ 12, no usar `AdminFulfillPro2026!` ni demos |
| `CORS_ORIGINS` | URLs HTTPS reales, **nunca** `*` |
| `SEED_DEMO_USERS` | `false` |
| `ALLOW_PUBLIC_REGISTER` | `false` salvo que quieras registro abierto |

### 4.3 Alinear contraseña de Postgres en Compose

El servicio `db` de `docker-compose.yml` usa por defecto:

```yaml
POSTGRES_USER: fulfillpro
POSTGRES_PASSWORD: fulfillpro
POSTGRES_DB: fulfillpro
```

Tienes dos caminos:

**A) Rápido (solo red Docker, puerto no publicado a Internet):**  
Deja la password de compose y usa la misma en `DATABASE_URL`. Aceptable si **nunca** publicas el puerto 5432 al host/LAN.

**B) Mejor práctica:** edita `docker-compose.yml` y `.env` con la misma password fuerte:

```yaml
# en service db → environment:
POSTGRES_PASSWORD: CAMBIA_PASSWORD_DB_LARGA
```

Y en `.env`:

```env
DATABASE_URL=postgresql+psycopg://fulfillpro:CAMBIA_PASSWORD_DB_LARGA@db:5432/fulfillpro
```

> En el `docker-compose` actual la API ya recibe `DATABASE_URL` y `REDIS_URL` por `environment:` del compose (apuntan a `db` y `redis`).  
> Si el compose **sobrescribe** `DATABASE_URL`, asegúrate de que coincida con la password del servicio `db`.  
> Revisa con: `docker compose config | less`

### 4.4 Variables de producción en `docker-compose.yml` (recomendado)

Edita el servicio `api` para forzar producción (o usa un override):

```bash
nano docker-compose.yml
```

En `api.environment` deja / añade:

El `docker-compose.yml` del repo ya toma `APP_ENV`, `CORS_ORIGINS`, secretos, etc. desde el **`.env`**.  
No hace falta pisar a mano `development` en el compose.

CORS de producción (ya en `.env` del repo):

```env
CORS_ORIGINS=https://fulfillpro.app,https://www.fulfillpro.app
```

> El puerto de la API está enlazado a `127.0.0.1:8000` (solo localhost). Cloudflared habla con ese puerto; no abras 8000 a Internet.

### 4.5 Crear carpeta de storage y permisos

```bash
cd /opt/fulfillpro
mkdir -p storage
# Docker suele correr como root dentro del contenedor; el bind mount basta
```

### 4.6 Arrancar el stack

```bash
cd /opt/fulfillpro
docker compose up -d --build
# Si solo tienes el binario antiguo:
# docker-compose up -d --build
```

Ver estado:

```bash
docker compose ps
docker compose logs -f api
# Ctrl+C para salir de logs
```

Health check local:

```bash
curl -s http://127.0.0.1:8000/api/health | jq .
# o sin jq:
curl -s http://127.0.0.1:8000/api/health
```

Debes ver `"ok": true`, `"database": true`, `"redis": true`.

### 4.7 Escalar workers (opcional)

Si la VM tiene ≥ 16 GB RAM:

```bash
docker compose up -d --scale worker=2
# En cada worker conviene WORKER_CONCURRENCY=2 o 3
```

### 4.8 Arranque automático al prender el servidor (obligatorio en producción)

Sin esto, al reiniciar la VM de Proxmox **Docker puede quedar parado** o los contenedores no se recrean.  
Configura **dos capas**:

| Capa | Qué hace |
|------|----------|
| `docker.service` | Motor Docker al boot |
| `fulfillpro.service` | `docker compose up -d` del stack en `/opt/fulfillpro` |

Los servicios del compose ya tienen `restart: unless-stopped` (si Docker arranca, los contenedores se reponen).  
La unidad `fulfillpro` asegura el `compose up` completo tras un reboot limpio.

#### Opción A — script del repo (recomendado)

```bash
cd /opt/fulfillpro
git pull
sudo bash deploy/install-autostart.sh
```

Comprueba:

```bash
systemctl is-enabled docker fulfillpro
systemctl status fulfillpro --no-pager
docker compose ps
```

#### Opción B — a mano con systemd

```bash
# 1) Docker al boot
sudo systemctl enable --now docker

# 2) Unidad del stack
sudo cp /opt/fulfillpro/deploy/systemd/fulfillpro.service /etc/systemd/system/fulfillpro.service
# Si el proyecto NO está en /opt/fulfillpro, edita WorkingDirectory en el .service
sudo nano /etc/systemd/system/fulfillpro.service

sudo systemctl daemon-reload
sudo systemctl enable --now fulfillpro.service
sudo systemctl status fulfillpro
```

Contenido de referencia (`deploy/systemd/fulfillpro.service`):

```ini
[Unit]
Description=FulfillPro Docker Compose stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/fulfillpro
ExecStart=/usr/bin/docker compose up -d --remove-orphans
ExecStop=/usr/bin/docker compose down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
```

Si solo tienes el binario antiguo `docker-compose` (con guion):

```bash
sudo sed -i 's|/usr/bin/docker compose|/usr/local/bin/docker-compose|g' \
  /etc/systemd/system/fulfillpro.service
sudo systemctl daemon-reload
sudo systemctl restart fulfillpro
```

#### Probar que sobrevive un reinicio

```bash
sudo reboot
# tras volver a entrar por SSH:
systemctl is-active docker fulfillpro
docker compose -f /opt/fulfillpro/docker-compose.yml ps
curl -s http://127.0.0.1:8000/api/health
```

Debes ver contenedores `Up` / `healthy` sin haber ejecutado `compose up` a mano.

#### Comandos útiles del servicio

```bash
sudo systemctl start fulfillpro      # levantar stack
sudo systemctl stop fulfillpro       # docker compose down
sudo systemctl restart fulfillpro    # recrear stack
journalctl -u fulfillpro -n 50 --no-pager
```

> **Orden de arranque recomendado:** `docker` → `fulfillpro` → `cloudflared`  
> El servicio de cloudflared ya declara `After=docker.service`; si quieres esperar al stack:
>
> ```bash
> sudo systemctl edit cloudflared
> # añade:
> # [Unit]
> # After=fulfillpro.service
> # Requires=fulfillpro.service
> ```

### 4.9 Primer login (solo LAN / tunnel)

| Uso | URL | Notas |
|-----|-----|--------|
| App empresas | `https://fulfillpro.app/` | Tras Cloudflared |
| Ops (plataforma) | `https://fulfillpro.app/ops` | Ruta oculta admin |
| Health | `https://fulfillpro.app/api/health` | Monitoreo |

Credenciales: las de `ADMIN_EMAIL` / `ADMIN_PASSWORD` del `.env`.

**Cambia la contraseña admin** y guarda un **backup** desde el panel Ops → pestaña Backup en cuanto el sistema esté en línea.

---

## 5. Publicar a Internet con Cloudflare Tunnel (`cloudflared`)

Arquitectura:

```
Usuario → HTTPS → Cloudflare Edge → Tunnel (cloudflared en la VM)
                                      └→ http://127.0.0.1:8000  (Docker API)
```

No abres puertos en el router. No necesitas IP pública fija.

### 5.1 Prerrequisitos en Cloudflare

1. Dominio añadido a Cloudflare (estado **Active**).
2. SSL/TLS mode: **Full** (no “Flexible” si puedes; Full está bien con HTTP local).
3. (Opcional) Zero Trust free: dash.cloudflare.com → Zero Trust.

### 5.2 Instalar `cloudflared` en la VM

**Debian / método genérico (recomendado):**

```bash
# Detecta arch
ARCH=$(dpkg --print-architecture)   # amd64 o arm64

# Paquete .deb oficial (ajusta versión si hace falta)
curl -L --output cloudflared.deb \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-${ARCH}.deb

sudo dpkg -i cloudflared.deb
cloudflared --version
```

Si el `.deb` falla en Debian 10 por dependencias:

```bash
# Binario estático
curl -L -o cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
sudo mv cloudflared /usr/local/bin/cloudflared
sudo chmod +x /usr/local/bin/cloudflared
cloudflared --version
```

### 5.3 Autenticar y crear el túnel

En la VM:

```bash
cloudflared tunnel login
```

- Se abre una URL o imprime un enlace: ábrelo en el navegador de tu PC.
- Elige el **dominio** de Cloudflare y autoriza.
- Se guarda un certificado en `~/.cloudflared/cert.pem`.

Crear túnel con nombre fijo:

```bash
cloudflared tunnel create fulfillpro
```

Anota el **Tunnel ID** (UUID) que imprime. También crea `~/.cloudflared/<TUNNEL_ID>.json` (credenciales).

### 5.4 Enrutar DNS del dominio al túnel

Dominio de producción: **fulfillpro.app**

```bash
# Apex (recomendado si la app vive en https://fulfillpro.app)
cloudflared tunnel route dns fulfillpro fulfillpro.app

# Opcional: www
cloudflared tunnel route dns fulfillpro www.fulfillpro.app
```

Eso crea registros **CNAME** en Cloudflare apuntando al túnel.  
En el apex Cloudflare suele aplicar *CNAME flattening*.

### 5.5 Configuración del túnel

```bash
mkdir -p ~/.cloudflared
nano ~/.cloudflared/config.yml
```

Contenido (ajusta UUID y ruta de credenciales; hostname = **fulfillpro.app**):

```yaml
tunnel: FULFILLPRO_TUNNEL_UUID
credentials-file: /home/TU_USUARIO/.cloudflared/FULFILLPRO_TUNNEL_UUID.json

ingress:
  - hostname: fulfillpro.app
    service: http://127.0.0.1:8000
    originRequest:
      connectTimeout: 30s
      noTLSVerify: true

  - hostname: www.fulfillpro.app
    service: http://127.0.0.1:8000
    originRequest:
      connectTimeout: 30s
      noTLSVerify: true

  # Catch-all obligatorio
  - service: http_status:404
```

Prueba en primer plano:

```bash
cloudflared tunnel --config ~/.cloudflared/config.yml run
```

Desde el móvil (datos, no WiFi de casa) abre:

`https://fulfillpro.app/api/health`

Si responde JSON `ok`, el túnel funciona. `Ctrl+C` y pasa a servicio systemd.

### 5.6 Servicio systemd (arranque automático)

**Opción oficial con instalador de cloudflared:**

```bash
sudo cloudflared service install
# A veces espera config en /etc/cloudflared/config.yml
```

**Opción manual (recomendada si usas config en home):**

```bash
sudo mkdir -p /etc/cloudflared
sudo cp ~/.cloudflared/config.yml /etc/cloudflared/config.yml
sudo cp ~/.cloudflared/<TUNNEL_ID>.json /etc/cloudflared/<TUNNEL_ID>.json
# Ajusta credentials-file en /etc/cloudflared/config.yml a:
#   credentials-file: /etc/cloudflared/<TUNNEL_ID>.json
sudo nano /etc/cloudflared/config.yml
```

```bash
sudo tee /etc/systemd/system/cloudflared.service > /dev/null << 'EOF'
[Unit]
Description=Cloudflare Tunnel - FulfillPro
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=root
ExecStart=/usr/bin/cloudflared --no-autoupdate --config /etc/cloudflared/config.yml tunnel run
Restart=on-failure
RestartSec=5
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF
```

Si el binario está en `/usr/local/bin/cloudflared`, cambia `ExecStart`.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now cloudflared
sudo systemctl status cloudflared
journalctl -u cloudflared -f
```

### 5.7 CORS y cookie / HTTPS

En el `.env` del repo ya va:

```env
APP_ENV=production
CORS_ORIGINS=https://fulfillpro.app,https://www.fulfillpro.app
```

Tras `git pull`, reinicia el stack (o el servicio systemd):

```bash
cd /opt/fulfillpro
docker compose up -d api
# o:
sudo systemctl restart fulfillpro
```

En Cloudflare (recomendado):

| Ajuste | Valor sugerido |
|--------|----------------|
| SSL/TLS | Full |
| Always Use HTTPS | On |
| Minimum TLS | 1.2 |
| Bot Fight Mode | On (opcional) |
| WAF / Rate limiting | Reglas suaves en `/api/auth/login` (opcional) |

---

## 6. Hardening post-despliegue

1. **No publiques** puertos `5432`, `6379` ni `8000` en el router.
2. API solo en `127.0.0.1:8000` si usas Cloudflared en la misma VM.
3. `APP_ENV=production`, secretos fuertes, sin usuarios demo.
4. Cambia `ADMIN_PASSWORD` y guárdala en un gestor de contraseñas.
5. Ruta `/ops` es “oculta”, **no es seguridad real**: usa contraseña fuerte + opcional Access de Cloudflare (Zero Trust) solo para `/ops`.
6. Backups periódicos (panel Ops → Backup o cron que copie `storage/` + dump).
7. Actualiza la VM:

```bash
sudo apt update && sudo apt upgrade -y
cd /opt/fulfillpro && docker compose pull && docker compose up -d --build
```

### 6.1 (Opcional) Cloudflare Access solo para Ops

En Zero Trust → Access → Applications:

- Application: `https://fulfillpro.app/ops*`
- Policy: solo emails de owners (`@tu-empresa.com` o el correo de los owners)

Así el panel de plataforma no es alcanzable por cualquiera que adivine `/ops`.

---

## 7. Backups en el servidor

### 7.1 Desde el panel Ops

1. Login en `https://app.tu-dominio.com/ops`
2. Pestaña **Backup**
3. Descargar ZIP (con storage en respaldos semanales)

### 7.2 Snapshot pre-restore automático

Al restaurar, FulfillPro intenta guardar `storage/.backups/pre-restore-*.zip` (BD).

### 7.3 Cron simple (copia del storage)

```bash
sudo mkdir -p /var/backups/fulfillpro
crontab -e
```

```cron
# Diario 03:15 — copia storage (ajusta retención a mano)
15 3 * * * rsync -a --delete /opt/fulfillpro/storage/ /var/backups/fulfillpro/storage/ >> /var/log/fp-backup.log 2>&1
```

Para volumen Docker de Postgres (`pgdata`), además:

```bash
docker compose exec -T db pg_dump -U fulfillpro fulfillpro | gzip > /var/backups/fulfillpro/db-$(date +\%F).sql.gz
```

---

## 8. Actualizar FulfillPro

```bash
cd /opt/fulfillpro
# git pull   # si usas git
docker compose up -d --build
docker compose ps
curl -s http://127.0.0.1:8000/api/health
```

Si cambió el frontend y ves caché rara: hard refresh en el navegador (`Ctrl+F5`).

---

## 9. Comandos útiles de operación

```bash
# Estado
docker compose ps
docker stats --no-stream

# Logs
docker compose logs -f api
docker compose logs -f worker
docker compose logs --tail=100 db

# Reinicio suave
docker compose restart api worker

# Parar todo
docker compose down

# Parar borrando SOLO contenedores (conserva volúmenes pgdata y storage bind)
docker compose down

# ⚠ Borrar base de datos (destructivo)
# docker compose down -v
```

Cloudflared:

```bash
sudo systemctl status cloudflared
sudo systemctl restart cloudflared
journalctl -u cloudflared -n 50 --no-pager
```

---

## 10. Solución de problemas

| Síntoma | Qué revisar |
|---------|-------------|
| API no arranca en production | Logs: JWT_SECRET / ADMIN_PASSWORD / CORS. Deben ser fuertes y CORS sin `*`. |
| `connection refused` a db | `docker compose ps` — db healthy; `DATABASE_URL` host = `db`. |
| 502 en el dominio | `cloudflared` caído; API no escucha en `127.0.0.1:8000`; hostname mal en `config.yml`. |
| CORS error en navegador | `CORS_ORIGINS` debe incluir `https://fulfillpro.app` (sin slash final). Reinicia api tras cambiar `.env`. |
| Tras reboot no hay app | `systemctl enable docker fulfillpro cloudflared` y `systemctl status fulfillpro`. |
| Excel no procesa | Worker: `docker compose logs worker`. Cola Redis: `/api/health` → `queue`. |
| OOM / VM se congela | Baja `WORKER_CONCURRENCY` a 2; no escales workers sin RAM. |
| Subidas grandes fallan | Cloudflare Free tiene límites de tamaño de request; `MAX_UPLOAD_MB` ≤ 25 recomendado. |
| Tunnel login no abre | Copia la URL que imprime cloudflared y ábrela en el PC. |
| Debian 10 no instala Docker | Usa `get.docker.com` o migra la VM a Debian 12. |

Probar desde la VM:

```bash
curl -I http://127.0.0.1:8000/
curl -s http://127.0.0.1:8000/api/health
sudo systemctl is-active cloudflared docker
```

---

## 11. Orden resumido (cheat sheet)

```text
1. Proxmox → VM Debian (12 preferible / 10 ok)  6 vCPU · 12 GB RAM · 100 GB
2. apt update/upgrade · ufw (solo SSH LAN) · install Docker
3. /opt/fulfillpro → git clone/pull · .env (CORS=https://fulfillpro.app)
4. docker compose up -d --build
5. curl http://127.0.0.1:8000/api/health
6. sudo bash deploy/install-autostart.sh   # Docker + stack al prender la VM
7. cloudflared tunnel login · create · route dns fulfillpro.app · config.yml
8. systemd enable cloudflared
9. Login https://fulfillpro.app/ops · Backup ZIP
10. (Opcional) Cloudflare Access en /ops*
11. sudo reboot  → verificar que todo vuelve solo
```

---

## 12. Diagrama final

```
┌─────────────────────────────────────────────────────────────┐
│ Internet                                                    │
│   https://fulfillpro.app                                    │
└───────────────────────────┬─────────────────────────────────┘
                            │ TLS termina en Cloudflare
                            ▼
                    ┌───────────────┐
                    │ Cloudflare    │
                    │ Edge + WAF    │
                    └───────┬───────┘
                            │ Tunnel (salida de tu red)
                            ▼
┌─────────────────────────────────────────────────────────────┐
│ Proxmox host                                                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ VM Debian · fulfillpro                                │  │
│  │  boot: docker.service → fulfillpro.service            │  │
│  │        → cloudflared.service                          │  │
│  │  cloudflared ──► 127.0.0.1:8000                       │  │
│  │  Docker: api · worker · db · redis                    │  │
│  │  disco: /opt/fulfillpro/storage + volumen pgdata      │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## 13. Referencias internas del proyecto

| Documento / ruta | Uso |
|------------------|-----|
| `README.md` | Arranque rápido y roles |
| `docker-compose.yml` | Recursos y servicios |
| `.env.example` | Variables |
| Panel Ops → Backup | Export / restore de emergencia |
| `Documentacion/Auditoria_Capacidad_Carga_12GB.md` | Límites de RAM / concurrencia |
| `Documentacion/Auditoria_Seguridad_OWASP_Top10.md` | Controles de seguridad |

---

*Documento orientado a owners de FulfillPro · despliegue on-prem con exposición segura vía Cloudflare Tunnel.*  
*Sustituye siempre `tu-dominio.com`, secretos y contraseñas de ejemplo por valores reales antes de producción.*
