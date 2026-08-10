function resolveNotifyJid(target, { ordersGid, alertJid }) {
    if (target === 'orders') {
        if (!ordersGid) return { error: 'orders_group_not_configured' }
        return { jid: ordersGid }
    }
    return { jid: alertJid }
}

module.exports = { resolveNotifyJid }
