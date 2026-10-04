// --- Mínimos de compra leídos de Django --------------------------------------
// El menú del 112 los tenía escritos a mano: si se editaban en el panel, el bot
// seguía anunciando los viejos. Se consultan a /api/negocio/minimos/ y se
// guardan un rato para no pegarle a Django en cada "1" que escribe un cliente.

const TTL_MS = 10 * 60 * 1000

/**
 * `get()` devuelve la lista de categorías de Django, o null si nunca respondió
 * (y entonces el menú usa su texto fijo). Un fallo no borra lo último bueno, y
 * tampoco se reintenta en cada mensaje: espera el mismo TTL.
 */
function createMinimosSource({ fetchFn, ttlMs = TTL_MS, now = Date.now } = {}) {
    let value = null
    let checkedAt = null

    return {
        async get() {
            if (checkedAt !== null && now() - checkedAt < ttlMs) return value
            checkedAt = now()
            try {
                const categorias = await fetchFn()
                if (Array.isArray(categorias)) value = categorias
            } catch (_) {
                // se queda con lo último bueno (o null → texto fijo)
            }
            return value
        },
    }
}

module.exports = { createMinimosSource, TTL_MS }
