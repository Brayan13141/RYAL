function resolveNotifyJid(target, { ordersGid, alertJid, phone }) {
    if (target === 'orders') {
        if (!ordersGid) return { error: 'orders_group_not_configured' }
        return { jid: ordersGid }
    }
    if (target === 'customer') {
        // El JID real lo resuelve onWhatsApp (necesita el socket); aquí solo se valida.
        if (typeof phone !== 'string' || !/^\d{10}$/.test(phone)) return { error: 'invalid_phone' }
        return { customerPhone: phone }
    }
    return { jid: alertJid }
}

module.exports = { resolveNotifyJid }
