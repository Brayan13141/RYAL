const { mensajeSinTipo } = require('./ventaSinTipo')
const { createOrderSessionStore } = require('./orderSession')

describe('pending sin_tipo', () => {
    test('el pending guarda lo necesario para reintentar la venta', () => {
        const orders = createOrderSessionStore()
        const GID = 'g@us'
        const detalles = [{ texto: 'Jordan', qty: 9, precio: 750,
                            sugerencias: [{ tipo_id: 1, nombre: 'JORDAN 4', costo: 680 }] }]
        const { opciones } = mensajeSinTipo(detalles, 1)

        orders.setPending(GID, 'sin_tipo', {
            detalles, opciones,
            endpoint: 'http://x/api/negocio/tienda/',
            ventaPayload: { items: [{ description: 'Jordan', price: 750, qty: 9 }], envio: 0 },
        })

        const p = orders.getPending(GID)
        expect(p.type).toBe('sin_tipo')
        expect(p.payload.opciones[0].tipo_id).toBe(1)
        expect(p.payload.ventaPayload.items).toHaveLength(1)
    })

    test('la sesión sobrevive al rechazo', () => {
        const orders = createOrderSessionStore()
        const GID = 'g@us'
        orders.startSession(GID, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        orders.addItem(GID, 'Jordan', 750)
        orders.setPending(GID, 'sin_tipo', { detalles: [], opciones: [] })

        expect(orders.getSession(GID).items).toHaveLength(1)
    })

    test('un número fuera de rango no borra el pending', () => {
        const orders = createOrderSessionStore()
        const GID = 'g@us'
        const payload = { detalles: [], opciones: [{ tipo_id: 1, nombre: 'A', costo: 1 }] }
        orders.setPending(GID, 'sin_tipo', payload)

        const elegido = 5
        if (elegido < 1 || elegido > payload.opciones.length) {
            orders.setPending(GID, 'sin_tipo', payload)
        }
        expect(orders.getPending(GID)).not.toBeNull()
    })
})

// ── El helper que decide si la venta se graba ────────────────────────────────
// Mismo patrón que bot.supplier.test.js: env vars, require('./bot'), sock falso.

process.env.ORDERS_GROUP_ID = 'orders@g.us'
process.env.DJANGO_API_URL  = 'http://localhost'
process.env.DJANGO_API_KEY  = 'test-key'

jest.mock('axios')
const axios = require('axios')
const { enviarVentaTienda, orders: ordersReales } = require('./bot')

const ORDERS   = 'orders@g.us'
const ENDPOINT = 'http://localhost/api/negocio/tienda/'
const PAYLOAD  = { items: [{ description: 'Jordan', price: 750, qty: 9 }], envio: 0 }

describe('enviarVentaTienda', () => {
    beforeEach(() => {
        ordersReales.clearPending(ORDERS)
        jest.clearAllMocks()
    })

    test('un 409 arma el pending, avisa en el grupo y NO reporta ok', async () => {
        axios.post.mockRejectedValue({
            response: {
                status: 409,
                data: {
                    sin_tipo: [{
                        texto: 'Jordan', qty: 9, precio: 750,
                        sugerencias: [{ tipo_id: 1, nombre: 'JORDAN 4', costo: 680 }],
                    }],
                },
            },
        })
        const sock = { sendMessage: jest.fn() }
        const res = await enviarVentaTienda(sock, { endpoint: ENDPOINT, payload: PAYLOAD })

        expect(res.ok).toBe(false)
        expect(res.sinTipo).toHaveLength(1)
        const pending = ordersReales.getPending(ORDERS)
        expect(pending.type).toBe('sin_tipo')
        expect(pending.payload.ventaPayload).toEqual(PAYLOAD)
        expect(pending.payload.opciones[0].tipo_id).toBe(1)
        expect(sock.sendMessage.mock.calls[0][1].text).toContain('NO registrada')
    })

    test('un 200 devuelve la data y no arma pending', async () => {
        axios.post.mockResolvedValue({
            data: { pedido_id: 7, total: '6750.00', sin_tipo: [] },
        })
        const sock = { sendMessage: jest.fn() }
        const res = await enviarVentaTienda(sock, { endpoint: ENDPOINT, payload: PAYLOAD })

        expect(res.ok).toBe(true)
        expect(res.data.pedido_id).toBe(7)
        expect(ordersReales.getPending(ORDERS)).toBeNull()
    })

    test('un 500 avisa error y NO arma pending', async () => {
        axios.post.mockRejectedValue({ response: { status: 500, data: {} }, message: 'boom' })
        const sock = { sendMessage: jest.fn() }
        const res = await enviarVentaTienda(sock, { endpoint: ENDPOINT, payload: PAYLOAD })

        expect(res.error).toBe(true)
        expect(ordersReales.getPending(ORDERS)).toBeNull()
        expect(sock.sendMessage.mock.calls[0][1].text).toContain('Error al crear el pedido')
    })
})
