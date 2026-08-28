// Mismo patrón que bot.supplier.test.js: env vars, require('./bot'), sock falso.
// Nada de lo que sigue ejercita orderSession.js o ventaSinTipo.js por su cuenta
// (eso ya lo cubren orderSession.test.js y ventaSinTipo.test.js) — todo pasa
// por el dispatcher real de bot.js, porque ahí es donde vive el comportamiento
// que importa: qué hace el bot cuando llega un mensaje real de WhatsApp.

process.env.ORDERS_GROUP_ID = 'orders@g.us'
process.env.DJANGO_API_URL  = 'http://localhost'
process.env.DJANGO_API_KEY  = 'test-key'

jest.mock('axios')
const axios = require('axios')
const { enviarVentaTienda, handleOrdersMessage, orders: ordersReales } = require('./bot')

const ORDERS   = 'orders@g.us'
const ENDPOINT = 'http://localhost/api/negocio/tienda/'
const PAYLOAD  = { items: [{ description: 'Jordan', price: 750, qty: 9 }], envio: 0 }

const mensajeDeTexto = (text) => ({
    key: { remoteJid: ORDERS, fromMe: true },
    message: { conversation: text },
})

describe('pending sin_tipo — dispatcher real de handleOrdersMessage', () => {
    beforeEach(() => {
        ordersReales.cancelSession(ORDERS)
        jest.clearAllMocks()
    })

    test('un 409 real en /cerrar arma el pending con lo necesario para reintentar', async () => {
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'Jordan', 750)
        ordersReales.setQty(ORDERS, 1, 9)
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
        await handleOrdersMessage(sock, mensajeDeTexto('/cerrar'))

        const pending = ordersReales.getPending(ORDERS)
        expect(pending.type).toBe('sin_tipo')
        expect(pending.payload.opciones[0].tipo_id).toBe(1)
        expect(pending.payload.ventaPayload.items).toHaveLength(1)
    })

    test('la sesión sobrevive al rechazo — /cerrar con 409 no cancela ni vacía la sesión', async () => {
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'Jordan', 750)
        axios.post.mockRejectedValue({
            response: {
                status: 409,
                data: { sin_tipo: [{ texto: 'Jordan', qty: 1, precio: 750, sugerencias: [] }] },
            },
        })
        const sock = { sendMessage: jest.fn() }
        await handleOrdersMessage(sock, mensajeDeTexto('/cerrar'))

        expect(ordersReales.getSession(ORDERS).items).toHaveLength(1)
    })

    test('un número fuera de rango no borra el pending ni agrega un ítem fantasma', async () => {
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'Jordan', 750)
        ordersReales.setPending(ORDERS, 'sin_tipo', {
            detalles: [{ texto: 'Jordan', qty: 1, precio: 750, sugerencias: [] }],
            opciones: [{ tipo_id: 1, nombre: 'A', costo: 1 }],
            endpoint: ENDPOINT,
            ventaPayload: PAYLOAD,
        })

        const sock = { sendMessage: jest.fn() }
        await handleOrdersMessage(sock, mensajeDeTexto('9'))

        const pending = ordersReales.getPending(ORDERS)
        expect(pending).not.toBeNull()
        expect(pending.type).toBe('sin_tipo')
        // sin ítem fantasma de $9: la sesión sigue con el único ítem real
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(1)
        expect(sock.sendMessage.mock.calls[0][1].text).toContain('Responde un número del 1 al 1')
        expect(axios.post).not.toHaveBeenCalled()
    })

    test('responder el numero elige la opcion en vez de agregar un item de $1', async () => {
        // Regresion del Critical: parseItemText("1") devuelve un item de $1, y su
        // bloque corre antes del dispatcher del pending. Sin la guarda, elegir la
        // primera opcion agregaba un articulo fantasma y trababa la venta.
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'Jordan', 750)
        ordersReales.setPending(ORDERS, 'sin_tipo', {
            detalles: [{ texto: 'Jordan', qty: 1, precio: 750, sugerencias: [] }],
            opciones: [{ tipo_id: 7, nombre: 'JORDAN 4', costo: 680 }],
            endpoint: ENDPOINT,
            ventaPayload: PAYLOAD,
        })

        // El alias se crea bien; el reintento de la venta falla (no es lo que
        // este test verifica) para poder comprobar que la sesión no se tocó
        // más allá de lo que el propio dispatcher hace explícitamente.
        axios.post
            .mockResolvedValueOnce({ data: { ok: true } })
            .mockRejectedValueOnce({ response: { status: 500, data: {} }, message: 'boom' })
        const sock = { sendMessage: jest.fn() }
        await handleOrdersMessage(sock, mensajeDeTexto('1'))

        // NO se agrego un item fantasma
        expect(ordersReales.getSession(ORDERS)?.items.length ?? 0).toBe(1)
        // SI se llamo al endpoint de alias con el tipo elegido
        const llamadas = axios.post.mock.calls.map(c => c[0])
        expect(llamadas.some(u => String(u).includes('/api/negocio/alias/'))).toBe(true)
    })
})

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
