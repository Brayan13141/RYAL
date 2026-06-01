# Deploy — App `negocio` + Bot WhatsApp (Task 12)

Runbook listo para pegar. Servidor Hetzner: `root@5.161.249.245`, app en `/root/app/`.

> **Orden:** Fase 0 (subir código) → Fase 1 (Django) → Fase 2–5 (bot).
> El bot depende de que la API Django (`/api/negocio/`) esté viva, así que Django va primero.

---

## Fase 0 — Subir el código a `master` (en tu PC, PowerShell)

La rama `feature/negocio-whatsapp-bot` está 7 commits adelante de `master` (fast-forward limpio).

```powershell
cd C:\Users\Lenovo\Documents\WEB_RYAL
git checkout master
git merge --ff-only feature/negocio-whatsapp-bot
git push origin master
git checkout feature/negocio-whatsapp-bot   # volver a la rama de trabajo
```

---

## Fase 1 — Deploy de la app Django `negocio` (en el servidor)

```bash
ssh root@5.161.249.245

# 1. Traer el código nuevo
cd /root/app && git pull origin master

# 2. Generar la API key e inyectarla en el .env de producción (UNA sola vez)
#    Guárdala: la necesitarás idéntica en el bot (Fase 3).
NEGOCIO_KEY=$(openssl rand -hex 32)
echo "NEGOCIO_API_KEY=$NEGOCIO_KEY" >> /root/app/.env
echo "API KEY GENERADA (copiar): $NEGOCIO_KEY"

# 3. Migrar (aplica negocio 0001/0002 + cualquier pendiente: size groups 0017, cart_script 0008)
cd /root/app/config && source ../venv/bin/activate
PYTHONUTF8=1 python manage.py migrate
PYTHONUTF8=1 python manage.py collectstatic --noinput

# 4. Reiniciar gunicorn para tomar la nueva NEGOCIO_API_KEY y el código
systemctl restart gunicorn

# 5. Verificar la API interna (debe responder 401 sin key, 200 con key)
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/api/negocio/cliente/5550000000/
#   esperado: 401
curl -s -H "Authorization: Bearer $NEGOCIO_KEY" \
  http://localhost:8000/api/negocio/cliente/5550000000/
#   esperado: {"descuento": 0.0}
```

> Verifica también el panel: `https://ryalsneackers.com/panel/negocio/` → debe cargar el Resumen.

---

## Fase 2 — Subir e instalar el bot (en el servidor)

```bash
# Desde tu PC (PowerShell) — subir solo la carpeta bot (sin node_modules)
# (.gitignore del bot ya excluye node_modules/.env*/.baileys_auth)
scp -r C:\Users\Lenovo\Documents\WEB_RYAL\bot root@5.161.249.245:/root/app/
```

```bash
# En el servidor — instalar Node.js 20 LTS si no está
node --version 2>/dev/null || (curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt install -y nodejs)

# Instalar dependencias del bot (solo producción)
cd /root/app/bot && npm install --omit=dev
```

---

## Fase 3 — Configurar `.env.persona1` y `.env.persona2` (en el servidor)

```bash
cd /root/app/bot
cp .env.example .env.persona1
cp .env.example .env.persona2
nano .env.persona1
```

`.env.persona1` (Persona que REENVÍA del proveedor → Grupo Ryal):
```env
SUPPLIER_GROUP_ID=<JID del grupo del proveedor — obtener en Fase 4>
RYAL_GROUP_ID=<JID del Grupo Ryal — obtener en Fase 4>
FORWARD_TO_RYAL=true
DJANGO_API_URL=http://localhost:8000
DJANGO_API_KEY=<el NEGOCIO_KEY de la Fase 1, paso 2>
MARKUP=100
```

`.env.persona2` (Persona que SOLO responde precios en privado — NO reenvía):
```env
SUPPLIER_GROUP_ID=<mismo JID del proveedor (no se usa si FORWARD=false)>
RYAL_GROUP_ID=<mismo JID del Grupo Ryal>
FORWARD_TO_RYAL=false
DJANGO_API_URL=http://localhost:8000
DJANGO_API_KEY=<el mismo NEGOCIO_KEY>
MARKUP=100
```

---

## Fase 4 — Obtener los JIDs de los grupos (en el servidor)

```bash
cd /root/app/bot
node -e "
const { default: makeWASocket, useMultiFileAuthState } = require('@whiskeysockets/baileys')
const qrcode = require('qrcode-terminal')
const pino = require('pino')
async function run() {
  const { state, saveCreds } = await useMultiFileAuthState('.baileys_auth_temp')
  const sock = makeWASocket({ auth: state, logger: pino({level:'silent'}) })
  sock.ev.on('creds.update', saveCreds)
  sock.ev.on('connection.update', ({qr}) => { if (qr) qrcode.generate(qr, {small:true}) })
  sock.ev.on('messages.upsert', ({ messages }) => {
    for (const m of messages)
      if (m.key.remoteJid?.endsWith('@g.us')) console.log('GROUP JID:', m.key.remoteJid)
  })
}
run()
"
```

1. Escanear el QR con un celular.
2. Enviar un mensaje en el **grupo del proveedor** → copiar su `GROUP JID`.
3. Enviar un mensaje en el **Grupo Ryal** → copiar su `GROUP JID`.
4. `Ctrl+C` y limpiar: `rm -rf .baileys_auth_temp`
5. Pegar los JIDs en `.env.persona1` y `.env.persona2` (Fase 3).

---

## Fase 5 — Login QR por persona + systemd

### Persona 1 (usa `/root/app/bot`)

```bash
cd /root/app/bot
node -e "require('dotenv').config({path:'.env.persona1'}); require('./bot.js')"
# → escanear QR con el celular de PERSONA 1 → esperar "Bot conectado ✓" → Ctrl+C
# La sesión queda en /root/app/bot/.baileys_auth/
```

### Persona 2 (dir propio `/root/app/bot-p2`, auth Baileys separado)

```bash
mkdir -p /root/app/bot-p2
cp /root/app/bot/*.js /root/app/bot-p2/
cp /root/app/bot/package*.json /root/app/bot-p2/
cp /root/app/bot/.env.persona2 /root/app/bot-p2/.env
cd /root/app/bot-p2 && npm install --omit=dev
node bot.js
# → escanear QR con el celular de PERSONA 2 → "Bot conectado ✓" → Ctrl+C
```

### Servicios systemd

```bash
cat > /etc/systemd/system/bot-persona1.service <<'EOF'
[Unit]
Description=Bot Ryal — Persona 1
After=network.target gunicorn.service

[Service]
Type=simple
WorkingDirectory=/root/app/bot
ExecStart=/usr/bin/node bot.js
EnvironmentFile=/root/app/bot/.env.persona1
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/bot-persona2.service <<'EOF'
[Unit]
Description=Bot Ryal — Persona 2
After=network.target gunicorn.service

[Service]
Type=simple
WorkingDirectory=/root/app/bot-p2
ExecStart=/usr/bin/node bot.js
EnvironmentFile=/root/app/bot-p2/.env
Restart=on-failure
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now bot-persona1 bot-persona2
systemctl status bot-persona1 bot-persona2 --no-pager
```

---

## Verificación final

```bash
# Logs en vivo de persona 1
journalctl -u bot-persona1 -f
# Enviar imagen con precio (ej. "$350") en el grupo del proveedor
# → esperado en logs: "Reenviado al Grupo Ryal"  (origPrice=350, newPrice=450)

# Logs de persona 2
journalctl -u bot-persona2 -f
# Un cliente reenvía en privado una imagen con precio
# → el bot responde "Total: $<precio-descuento> MXN"
```

### Re-login si una sesión se cae (`loggedOut`)
```bash
systemctl stop bot-persona1
rm -rf /root/app/bot/.baileys_auth
cd /root/app/bot && node -e "require('dotenv').config({path:'.env.persona1'}); require('./bot.js')"
# re-escanear QR → Ctrl+C → systemctl start bot-persona1
```

---

## Extras pendientes de S39–S43 (opcional, mismo deploy)
```bash
cd /root/app/config && source ../venv/bin/activate
PYTHONUTF8=1 python manage.py load_productos --fix-urls   # supplier_url → #/proinfo/{pid}
pip install playwright && playwright install chromium --with-deps   # auto-order modaverse
```
