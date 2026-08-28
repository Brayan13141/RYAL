'use strict'

/**
 * Aviso para el grupo cuando la venta trae artículos que Django no reconoció.
 *
 * OJO — desde que la venta sin tipo se RECHAZA (rama feat/venta-sin-tipo),
 * `/api/negocio/tienda/` devuelve siempre `sin_tipo: []` y esta función no
 * puede dispararse por esa ruta: ya no existe una venta grabada con costo $0
 * que avisar. Se conserva porque el bot desplegado la sigue llamando (con
 * `[]` o `undefined` devuelve `''`, así que es inofensiva) y porque la ruta
 * de `/pedido` podría querer el mismo aviso. Lo que describe abajo es lo que
 * pasaba ANTES del bloqueo.
 *
 * Sin tipo, el costo se graba en $0 y la venta queda con 100% de margen. Cero
 * es un costo plausible, así que después nada lo delata: ni el dashboard, ni
 * la caja, ni el pedido. La única señal es que la ganancia iguala al ingreso,
 * y nadie la mira de memoria.
 *
 * Por eso el aviso va acá y no solo en el panel: quien capturó la venta está
 * mirando el teléfono en este segundo y sabe qué se vendió. Dentro de una
 * semana, no.
 *
 * No inventa un costo — solo lo dice.
 */
const MAX_LISTADOS = 5

function avisoSinTipo(sinTipo) {
    if (!Array.isArray(sinTipo)) return ''

    const limpios = []
    for (const nombre of sinTipo) {
        const texto = String(nombre || '').trim()
        if (texto && !limpios.includes(texto)) limpios.push(texto)
    }
    if (limpios.length === 0) return ''

    const listados = limpios.slice(0, MAX_LISTADOS).map(t => `«${t}»`).join(', ')
    const resto = limpios.length - MAX_LISTADOS
    const cola = resto > 0 ? ` y ${resto} más` : ''
    const plural = limpios.length === 1

    return `\n\n⚠️ No reconocí ${listados}${cola}` +
        ` (${limpios.length} artículo${plural ? '' : 's'}):` +
        ` ${plural ? 'quedó' : 'quedaron'} con costo $0, así que la ganancia de` +
        ` es${plural ? 'a línea' : 'as líneas'} sale inflada.` +
        `\nAgregá la palabra clave en el panel → Tipos de artículo.`
}

module.exports = { avisoSinTipo, MAX_LISTADOS }
