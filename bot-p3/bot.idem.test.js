// El cierre de una venta tiene que llevar la clave de idempotencia que Django
// usa para colapsar los POST repetidos.
//
// El caso real (2026-09-20): bot-p2 y bot-p3 escuchaban el mismo Grupo Pedidos
// y atendieron el mismo `/cerrar`. Dos POST con 18 ms de diferencia dejaron los
// pedidos 105 y 106 idénticos y la caja del día con $500 de más.
//
// La clave es el id del mensaje de WhatsApp: WhatsApp se lo asigna al mensaje,
// no al receptor, así que TODA instancia que reciba ese `/cerrar` manda la
// MISMA clave. Por eso protege también contra la re-entrega tras una
// reconexión y contra dos eventos `upsert` procesados en paralelo — ninguno de
// esos casos cambia el id del mensaje.

process.env.ORDERS_GROUP_ID = 'orders@g.us'
process.env.DJANGO_API_URL  = 'http://localhost'
process.env.DJANGO_API_KEY  = 'test-key'

jest.mock('axios')
const axios = require('axios')
const { handleOrdersMessage, orders: ordersReales } = require('./bot')

const ORDERS = 'orders@g.us'

const mensaje = (text, id) => ({
    key: { remoteJid: ORDERS, fromMe: true, id },
    message: { conversation: text },
})

// El POST del cierre, distinguido de las consultas de tipo que lo preceden.
const postDeVenta = () => axios.post.mock.calls.find(
    ([url]) => url.endsWith('/api/negocio/tienda/') || url.endsWith('/api/negocio/pedido/'))

describe('clave de idempotencia en el cierre de venta', () => {
    beforeEach(() => {
        ordersReales.cancelSession(ORDERS)
        jest.clearAllMocks()
        axios.post.mockResolvedValue({ data: { pedido_id: 1, total: '500.00' } })
    })

    test('/cerrar de tienda manda idem_key con el id del mensaje', async () => {
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'gorra barbas', 500, 230)
        const sock = { sendMessage: jest.fn() }

        await handleOrdersMessage(sock, mensaje('/cerrar', 'WA-MSG-105'))

        const [url, payload] = postDeVenta()
        expect(url).toBe('http://localhost/api/negocio/tienda/')
        expect(payload.idem_key).toBe('wa:WA-MSG-105')
    })

    test('/cerrar de pedido manda idem_key con el id del mensaje', async () => {
        ordersReales.startSession(ORDERS, 'Ana', '5551110000', 'pedido')
        ordersReales.addItem(ORDERS, 'tenis', 900, 400)
        const sock = { sendMessage: jest.fn() }

        await handleOrdersMessage(sock, mensaje('/cerrar', 'WA-MSG-200'))

        const [url, payload] = postDeVenta()
        expect(url).toBe('http://localhost/api/negocio/pedido/')
        expect(payload.idem_key).toBe('wa:WA-MSG-200')
    })

    test('dos instancias que reciben el MISMO /cerrar mandan la MISMA clave', async () => {
        // Lo que de verdad falló en producción. El mensaje es el mismo objeto
        // que llega a las dos instancias; lo único que cambia entre ellas es
        // el estado en memoria, que acá se reinicia entre cierres.
        const cierre = mensaje('/cerrar', 'WA-MSG-IGUAL')
        const claves = []
        for (const _ of [1, 2]) {
            jest.clearAllMocks()
            axios.post.mockResolvedValue({ data: { pedido_id: 1, total: '500.00' } })
            ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
            ordersReales.addItem(ORDERS, 'gorra barbas', 500, 230)
            await handleOrdersMessage({ sendMessage: jest.fn() }, cierre)
            claves.push(postDeVenta()[1].idem_key)
        }
        expect(claves[0]).toBe(claves[1])
        expect(claves[0]).toBe('wa:WA-MSG-IGUAL')
    })

    test('un mensaje sin id no manda clave en vez de mandar una inventada', async () => {
        // Una clave inventada (timestamp, random) sería distinta en cada
        // instancia: daría la ilusión de protección y duplicaría igual.
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'gorra barbas', 500, 230)

        await handleOrdersMessage({ sendMessage: jest.fn() }, mensaje('/cerrar', undefined))

        expect(postDeVenta()[1].idem_key).toBeNull()
    })
})

