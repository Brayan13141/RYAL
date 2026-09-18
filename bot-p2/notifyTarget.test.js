const { resolveNotifyJid } = require('./notifyTarget')

const OPTS = { ordersGid: '123@g.us', alertJid: '456@s.whatsapp.net' }

describe('resolveNotifyJid', () => {
    test('"orders" con grupo configurado → JID del grupo', () => {
        expect(resolveNotifyJid('orders', OPTS)).toEqual({ jid: '123@g.us' })
    })
    test('"orders" sin grupo → error', () => {
        expect(resolveNotifyJid('orders', { ...OPTS, ordersGid: undefined }).error).toBe('orders_group_not_configured')
    })
    test('sin target (watchdog) → JID de alerta', () => {
        expect(resolveNotifyJid(undefined, OPTS)).toEqual({ jid: '456@s.whatsapp.net' })
    })
    test('"customer" con 10 dígitos → customerPhone', () => {
        expect(resolveNotifyJid('customer', { ...OPTS, phone: '5512345678' })).toEqual({ customerPhone: '5512345678' })
    })
    test('"customer" con teléfono inválido → error, nunca cae al JID de alerta', () => {
        for (const phone of [undefined, '', '123', '525512345678', '55-1234-5678', 5512345678]) {
            expect(resolveNotifyJid('customer', { ...OPTS, phone })).toEqual({ error: 'invalid_phone' })
        }
    })
})
