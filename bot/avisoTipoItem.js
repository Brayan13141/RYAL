'use strict'

/**
 * Aviso en el momento de CARGAR el ítem, no al cerrar la venta.
 *
 * El bloqueo de venta sin tipo ya avisa en `/cerrar` con las sugerencias
 * numeradas, pero para entonces quien capturó puede llevar diez ítems
 * cargados y el problema aparece de golpe al final. Acá se dice en el
 * segundo en que se teclea el producto, que es cuando la persona todavía
 * tiene el artículo en la mano y sabe qué es.
 *
 * No inventa un costo ni bloquea la carga: el ítem entra igual. Solo avisa
 * que ese texto todavía no tiene tipo y que así la venta no va a cerrar.
 */
function avisoTipoItem(descripcion) {
    const texto = String(descripcion || '').trim()
    if (!texto) return ''
    return `\n\n⚠️ «${texto}» no coincide con ningún tipo de artículo:` +
        ` así no vas a poder cerrar la venta.` +
        `\nAgregá la palabra clave en el panel → Tipos de artículo.`
}

module.exports = { avisoTipoItem }
