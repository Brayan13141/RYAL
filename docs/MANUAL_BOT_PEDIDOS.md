# Manual — Bot de Pedidos y Ventas Ryal (Grupo Pedidos)

> **Grupo:** Privado — Bryan + socio + bot
> **Bot activo en:** `bot-persona1` (servidor Hetzner)
> **Variable requerida:** `ORDERS_GROUP_ID` en `/root/app/bot/.env`

---

## ¿Qué hace?

El bot escucha el Grupo Pedidos. Sirve para dos flujos:

| Flujo | Comando | Cuándo usarlo |
|---|---|---|
| **Pedido WhatsApp** | `/pedido` | Cliente confirmó por chat; reenvías fotos con precio |
| **Venta tienda física** | `/venta` | Cliente en mostrador; capturas ítems a mano |

---

## Flujo A — Pedido WhatsApp

### 1. Abrir sesión con el cliente

Acepta **teléfono** (preferido) o **nombre** — no ambos.

**Por teléfono (recomendado):**

```
Bryan:  /pedido 4451234567
Bot:    📋 Sesión iniciada — Juan García (4451234567)
        Reenvía fotos con precio para agregar ítems.
```

Si el número no está registrado, el bot crea el cliente automáticamente:

```
Bryan:  /pedido 4451234567
Bot:    📋 Sesión iniciada — Tel. 4451234567 (4451234567)
        Reenvía fotos con precio para agregar ítems.
```

**Por nombre:**

```
Bryan:  /pedido Juan García
Bot:    📋 Sesión iniciada — Juan García (4451234567)
        Reenvía fotos con precio para agregar ítems.
```

Si hay varios clientes con ese nombre:

```
Bryan:  /pedido María
Bot:    🔍 Varios resultados:
        1. María López — 4451234567
        2. María García — 5512345678
        Responde con el número de la opción.
```

Si no se encuentra ningún cliente:

```
Bot:    ⚠️ No encontré ningún cliente con ese nombre.
        Busca por teléfono (/pedido <número>) o regístralo primero desde el panel.
```

### 2. Reenviar fotos del Grupo Ryal al Grupo Pedidos

Las fotos del Grupo Ryal ya tienen el precio en el caption (`$450 MXN`). El bot lo detecta y agrega el artículo automáticamente.

```
Bryan:  [reenvía foto tenis rojo — caption "$450 MXN"]
Bot:    ✅ Ítem 1: $450 MXN agregado — Total acumulado: $450 MXN

Bryan:  [reenvía foto tenis azul — caption "$380 MXN"]
Bot:    ✅ Ítem 2: $380 MXN agregado — Total acumulado: $830 MXN
```

> **Importante:** Solo funcionan fotos que **tengan precio en el caption** (`$X MXN`). Si la foto no tiene precio, se ignora silenciosamente.

---

## Flujo B — Venta tienda física

### 1. Abrir sesión de tienda

```
Bryan:  /venta
Bot:    🏪 Venta tienda iniciada — Mostrador
        Ingresa ítems: <cantidad> <precio>  o  <descripción> <precio>  o  <cantidad> <descripción> <precio>
```

### 2. Capturar ítems por texto

No necesitas reenviar fotos. Solo escribe el ítem en el chat:

**Formatos aceptados:**

```
Bryan:  450              → 1 ítem a $450 (sin descripción)
Bot:    ✅ Ítem 1: 1× $450 — Total: $450 MXN

Bryan:  2 450            → 2 ítems a $450 (sin descripción)
Bot:    ✅ Ítem 2: 2× $450 — Total: $1,350 MXN

Bryan:  tenis rojos 450  → 1 ítem con descripción
Bot:    ✅ Ítem 3: 1× $450 — tenis rojos — Total: $1,800 MXN

Bryan:  3 gorras azules 380  → cantidad + descripción + precio
Bot:    ✅ Ítem 4: 3× $380 — gorras azules — Total: $2,940 MXN
```

### 3. Cerrar y guardar la venta

```
Bryan:  /cerrar
Bot:    ✅ Pedido #44 creado — Total: $2,940 MXN

# Con envío:
Bryan:  /cerrar envio=80
Bot:    ✅ Pedido #44 creado — Total: $3,020 MXN
```

La venta queda registrada con `origen = Tienda física` en: **`https://ryalsneackers.com/panel/negocio/pedidos/`**

---

## Sesión activa — opciones al usar `/pedido` o `/venta` con otra sesión abierta

Si ya hay una sesión activa, el bot avisa en lugar de sobreescribir:

```
Bryan:  /pedido 5512345678
Bot:    ⚠️ Ya hay una sesión abierta — Pedro Ramírez (2 ítems, $830 MXN).
        Responde:
        1️⃣ Continuar con este pedido
        2️⃣ Cerrar este pedido y abrir uno nuevo
        3️⃣ Cancelar y abrir uno nuevo
```

| Opción | Acción |
|---|---|
| `1` | Ignora el comando nuevo, sigue acumulando en la sesión actual |
| `2` | Guarda el pedido actual en Django y abre la sesión nueva |
| `3` | Descarta el pedido actual y abre la sesión nueva |

---

## Todos los comandos

### `/pedido <teléfono>` o `/pedido <Nombre>`

Abre una sesión de pedido WhatsApp. Acepta teléfono o nombre.

```
/pedido 4451234567        ← por teléfono (preferido)
/pedido Juan García       ← por nombre
```

### `/venta`

Abre una sesión de venta en tienda física con cliente Mostrador.

```
/venta
```

### `/items`

Muestra todos los artículos acumulados en la sesión actual.

```
/items
```

### `/cant <N> <cantidad>`

Cambia la cantidad del artículo número N.

```
/cant 1 3        ← artículo #1: 3 piezas
/cant 2 1        ← artículo #2: 1 pieza
```

### `/quitar <N>`

Elimina el artículo número N de la sesión.

```
/quitar 2        ← elimina el artículo #2
```

### `/cancelar`

Descarta la sesión completa sin guardar nada.

```
/cancelar
Bot:    ❌ Sesión cancelada.
```

### `/cerrar [envio=X]`

Guarda el pedido/venta en Django y cierra la sesión.

```
/cerrar              ← sin costo de envío
/cerrar envio=80     ← con $80 de envío
```

---

## Mensajes de error del bot

| Situación | Respuesta |
|---|---|
| Foto sin precio en caption | Ignorada silenciosamente |
| Foto con precio pero sin sesión activa | `⚠️ Sin sesión activa. Usa /pedido o /venta para iniciar.` |
| `/items` sin sesión | `Sin sesión activa.` |
| `/items` con sesión vacía | `Sin ítems. Agrega artículos.` |
| `/cerrar` sin sesión | `Sin sesión activa.` |
| `/cerrar` sin ítems | `Sin ítems en la sesión.` |
| `/quitar N` con N inválido | `Ítem N no encontrado.` |
| `/cant N qty` inválidos | `No se pudo actualizar el ítem N — índice o cantidad inválidos.` |
| Error al crear pedido en Django | `❌ Error al crear el pedido. Intenta de nuevo.` |
| `/pedido` sin argumentos | `Uso: /pedido <teléfono> o /pedido <nombre>` |
| `/pedido Nombre` con múltiples coincidencias | Lista opciones numeradas — responde con el número |
| `/pedido Nombre` sin resultados | `⚠️ No encontré ningún cliente. Usa /pedido <teléfono> o regístralo en el panel.` |
| Sesión activa al usar `/pedido` o `/venta` | Aviso con opciones 1/2/3 |

---

## Casos frecuentes

### Corregir la cantidad de un artículo ya agregado

```
/cant 1 3
```

### Me equivoqué de artículo (quiero eliminarlo)

```
/quitar 2
```

### Me equivoqué de cliente (pedido WhatsApp)

```
/pedido 5512345678
Bot:    ⚠️ Ya hay una sesión abierta — Pedro Ramírez (2 ítems).
        1️⃣ continuar  2️⃣ cerrar y abrir nuevo  3️⃣ cancelar y abrir nuevo

Bryan:  3
Bot:    ❌ Sesión anterior cancelada.
        📋 Sesión iniciada — Nuevo Cliente (5512345678)
```

### El bot se reinició y perdió la sesión

La sesión vive en memoria. Si el bot se reinicia, la sesión se pierde. Vuelve a abrir con `/pedido` o `/venta` y agrega los artículos de nuevo. Los pedidos ya cerrados siguen guardados en el sistema.

---

## Configuración (servidor)

### Variable de entorno requerida

```
# /root/app/bot/.env
ORDERS_GROUP_ID=<JID del Grupo Pedidos>@g.us
```

Sin esta variable, la feature de pedidos/ventas queda deshabilitada.

### Obtener el JID del Grupo Pedidos

```bash
cd /root/app/bot
node get-jids.js
```

### Reiniciar el bot después de cambiar el .env

```bash
systemctl restart bot-persona1
journalctl -u bot-persona1 -n 20 --no-pager
```

---

## Ver pedidos en el panel

Los pedidos y ventas aparecen en:
`https://ryalsneackers.com/panel/negocio/pedidos/`

Los pedidos de tienda muestran `Origen: Tienda física` en el detalle.
