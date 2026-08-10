const { resolveNotifyJid } = require('./notifyTarget')

describe('resolveNotifyJid', () => {
    test('target "orders" con ORDERS_GID configurado devuelve el JID del grupo', () => {
        const result = resolveNotifyJid('orders', { ordersGid: '123@g.us', alertJid: '456@s.whatsapp.net' })
        expect(result).toEqual({ jid: '123@g.us' })
    })

    test('target "orders" sin ORDERS_GID configurado devuelve error', () => {
        const result = resolveNotifyJid('orders', { ordersGid: undefined, alertJid: '456@s.whatsapp.net' })
        expect(result.error).toBe('orders_group_not_configured')
        expect(result.jid).toBeUndefined()
    })

    test('target "alert" devuelve el JID de alerta', () => {
        const result = resolveNotifyJid('alert', { ordersGid: '123@g.us', alertJid: '456@s.whatsapp.net' })
        expect(result).toEqual({ jid: '456@s.whatsapp.net' })
    })

    test('target ausente (undefined) se comporta como "alert" — no rompe al watchdog existente', () => {
        const result = resolveNotifyJid(undefined, { ordersGid: '123@g.us', alertJid: '456@s.whatsapp.net' })
        expect(result).toEqual({ jid: '456@s.whatsapp.net' })
    })
})
