/**
 * Vinculación por CÓDIGO en vez de por QR (bot-p3).
 *
 * El 2026-09-07 esta instancia generó 157 QR en 1h12m y ninguno se escaneó:
 * 26 desconexiones idénticas, todas `code 408 / "QR refs attempts ended"`,
 * separadas por exactamente 2m46s. No es un fallo — es la aritmética de
 * Baileys: da 6 refs por socket, el primero vive 60s y los siguientes 20s
 * (socket.js:709,723), o sea 160s y a empezar de nuevo. Un QR que rota cada
 * 20 segundos, dibujado en un terminal por SSH y que hay que pasar a un
 * teléfono, es un mecanismo que pierde contra el reloj.
 *
 * `requestPairingCode` da un código de 8 caracteres que se teclea en el
 * teléfono (Dispositivos vinculados → Vincular con número de teléfono) y dura
 * minutos, no 20 segundos.
 *
 * Este módulo es puro a propósito: las dos decisiones que pueden romper la
 * vinculación en silencio son el formato del número y cuándo NO pedir código,
 * y las dos se pueden probar sin socket.
 */

/**
 * El formato que WhatsApp acepta de verdad es `521` + los 10 dígitos
 * nacionales.
 *
 * No sale de la documentación sino de leer el `creds.me.id` de las cuentas que
 * ya están vinculadas y funcionando (los valores reales están en el vault, no
 * acá: este repo es público).
 * México dejó de marcar el 1 en 2019, así que "normalizar" quitándolo es la
 * trampa evidente — y da un número que no existe, con una vinculación que
 * falla sin explicar por qué.
 *
 * Devuelve null en vez de adivinar: un número mal formado tiene que apagar la
 * feature y dejar el QR de siempre, no intentar con algo inventado.
 */
function normalizarNumero(raw) {
    if (typeof raw !== 'string') return null

    const d = raw.replace(/\D/g, '')          // se come +, espacios, guiones, paréntesis

    if (d.length === 10) return `521${d}`                       // nacional
    if (d.length === 12 && d.startsWith('52')) return `521${d.slice(2)}`  // le falta el 1
    if (d.length === 13 && d.startsWith('521')) return d        // ya está bien

    return null
}

/**
 * Si esta instancia ya tiene un vínculo real.
 *
 * Ninguna de las dos señales evidentes sirve:
 *
 *  - `creds.registered` está MUERTO en Baileys 7.0.0-rc14. `auth-utils.js:295`
 *    lo inicializa en `false` y nada en la librería lo pone en `true`. Por eso
 *    persona1 y persona2, vinculados y trabajando, lo tienen en `false`.
 *    Apoyarse ahí haría que un bot sano pidiera un código nuevo en cada
 *    reconexión.
 *
 *  - `creds.me` tampoco: `requestPairingCode` lo escribe ANTES de que la
 *    vinculación ocurra (`socket.js:602`). Un intento fallido dejaría al bot
 *    creyéndose vinculado y no volvería a pedir código nunca.
 *
 * `creds.account` es la identidad firmada por WhatsApp: solo la escribe
 * `configureSuccessfulPairing`, o sea que solo existe después de un
 * `pair-success` de verdad.
 */
function yaVinculado(creds) {
    return Boolean(creds?.account)
}

/**
 * `yaPedido` es por proceso, no por sesión: `connection.update` emite un `qr`
 * nuevo cada 20s y sin ese guard se pediría un código por cada uno,
 * invalidando a mitad el que la persona está tecleando.
 */
function debePedirCodigo({ creds, numero, yaPedido }) {
    if (!numero) return false        // feature apagada: sigue el QR de siempre
    if (yaPedido) return false
    return !yaVinculado(creds)
}

module.exports = { normalizarNumero, yaVinculado, debePedirCodigo }
