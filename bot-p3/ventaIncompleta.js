'use strict'

/**
 * Mensajes de la venta de tenis cuando Django la rechaza por datos que
 * faltan (vía, color o talla) y del aviso cuando el par no tenía stock.
 *
 * A diferencia del sin-tipo, acá no hay opciones que numerar: el bot no
 * puede adivinar la talla. Se dice qué falta y se deja la sesión intacta
 * para que la persona corrija el ítem y vuelva a mandar /cerrar.
 */

function mensajeIncompleta(incompletos) {
    const lista = Array.isArray(incompletos) ? incompletos : []
    if (lista.length === 0) return ''

    const lineas = lista.map(d => `«${d.texto}»: ${d.mensaje}`).join('\n')
    return `⏸️ La venta NO se registró:\n${lineas}`
        + `\nCorrige con /quitar N y vuelve a cargar el ítem.`
}

/** La venta SÍ entró; esto solo avisa que faltaban pares en el inventario. */
function avisoSinStock(sinStock) {
    const lista = Array.isArray(sinStock) ? sinStock : []
    if (lista.length === 0) return ''
    return '\n\n' + lista.map(s => s.mensaje).join('\n')
}

module.exports = { mensajeIncompleta, avisoSinStock }
