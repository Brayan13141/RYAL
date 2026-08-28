'use strict'

/**
 * Mensaje del grupo cuando la venta se rechaza por un texto sin tipo.
 *
 * Se resuelve UN texto por vez a propósito: al crear el alias el bot
 * reintenta la venta entera, y si queda otro texto sin tipo el servidor
 * vuelve a rechazar con el siguiente. El cliente no lleva la cuenta.
 */

function money(n) {
    const num = Number(n)
    return Number.isInteger(num) ? String(num) : num.toFixed(2)
}

function mensajeSinTipo(detalles, totalItems) {
    const lista = Array.isArray(detalles) ? detalles : []
    const primero = lista[0]
    if (!primero) return { texto: '', opciones: [] }

    const opciones = Array.isArray(primero.sugerencias) ? primero.sugerencias : []
    const restantes = lista.length - 1

    const cabeza = `❌ Venta NO registrada — ${lista.length} `
        + `artículo${lista.length === 1 ? '' : 's'} sin tipo`

    const linea = `«${primero.texto}» ($${money(primero.precio)} × ${primero.qty})`

    const cola = `\n\nLos ${totalItems} artículo${totalItems === 1 ? '' : 's'}`
        + ` siguen cargados.`

    if (opciones.length === 0) {
        return {
            opciones: [],
            texto: `${cabeza}\n\n${linea} no coincide con ningún tipo,`
                + ` y no encontré ninguno parecido.`
                + `\n\nCargá el tipo o el alias en /panel/negocio/tipos/`
                + ` y volvé a mandar /cerrar.${cola}`,
        }
    }

    const numeradas = opciones
        .map((o, i) => `  ${i + 1}. ${o.nombre} — costo $${money(o.costo)}`)
        .join('\n')

    const aviso = restantes > 0
        ? `\n\n(Queda ${restantes} texto${restantes === 1 ? '' : 's'} más por resolver.)`
        : ''

    return {
        opciones,
        texto: `${cabeza}\n\n${linea} no coincide con ningún tipo.\n¿Cuál es?`
            + `\n\n${numeradas}`
            + `\n\nRespondé el número, o «otro» para cargarlo en el panel.`
            + `${aviso}${cola}`,
    }
}

module.exports = { mensajeSinTipo }
