// Buffer en memoria de imágenes de un lote que llegan SIN precio.
// El proveedor manda N imágenes sin caption y luego un mensaje con el precio;
// estas imágenes se guardan hasta que llega ese precio (flush) o expiran (TTL).
const TTL_MS = 5 * 60 * 1000   // 5 minutos
const MAX_PER_GROUP = 50

/**
 * Crea una instancia de buffer con estado propio (facilita los tests).
 * @param {{ ttlMs?: number, maxPerGroup?: number }} opts
 */
function createBatchBuffer({ ttlMs = TTL_MS, maxPerGroup = MAX_PER_GROUP } = {}) {
    // { [groupJid]: { items: [msg, ...], lastTs: number } }
    const buffers = {}

    function purgeExpired(now) {
        let dropped = 0
        for (const jid of Object.keys(buffers)) {
            if (now - buffers[jid].lastTs > ttlMs) {
                dropped += buffers[jid].items.length
                delete buffers[jid]
            }
        }
        return dropped
    }

    function addImage(groupJid, msg, now) {
        purgeExpired(now)
        const entry = buffers[groupJid] || { items: [], lastTs: now }
        if (entry.items.length >= maxPerGroup) return entry.items.length
        entry.items.push(msg)
        entry.lastTs = now
        buffers[groupJid] = entry
        return entry.items.length
    }

    function size(groupJid) {
        return buffers[groupJid] ? buffers[groupJid].items.length : 0
    }

    function flush(groupJid) {
        const entry = buffers[groupJid]
        if (!entry) return []
        delete buffers[groupJid]
        return entry.items
    }

    return { addImage, size, flush, purgeExpired }
}

module.exports = { createBatchBuffer, TTL_MS, MAX_PER_GROUP }
