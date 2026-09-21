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
process.env.PEER_BOT_JIDS   = '5214451000181,233299133886498'  // bot-p3

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

describe('mensajes de otra instancia', () => {
    beforeEach(() => {
        ordersReales.cancelSession(ORDERS)
        jest.clearAllMocks()
        axios.post.mockResolvedValue({ data: { pedido_id: 1, total: '500.00' } })
    })

    test('un /cerrar escrito por bot-p3 no lo cierra bot-p2', async () => {
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'gorra barbas', 500, 230)
        const sock = { sendMessage: jest.fn() }

        await handleOrdersMessage(sock, {
            key: { remoteJid: ORDERS, fromMe: false, id: 'WA-P3', participant: '5214451000181:2@s.whatsapp.net' },
            message: { conversation: '/cerrar' },
        })

        expect(postDeVenta()).toBeUndefined()
        expect(sock.sendMessage).not.toHaveBeenCalled()
        // Y la sesión NO se toca: el cierre lo hace bot-p3 sobre la suya.
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(1)
    })

    test('un /cerrar de un operador cualquiera SÍ lo cierra bot-p2', async () => {
        // El control inverso: sin esto, el test de arriba pasaría aunque
        // handleOrdersMessage hubiera dejado de atender a todo el mundo.
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'gorra barbas', 500, 230)

        await handleOrdersMessage({ sendMessage: jest.fn() }, {
            key: { remoteJid: ORDERS, fromMe: false, id: 'WA-OTRO', participant: '5214439728793:1@s.whatsapp.net' },
            message: { conversation: '/cerrar' },
        })

        expect(postDeVenta()[1].idem_key).toBe('wa:WA-OTRO')
    })
})

describe('respuesta duplicada de Django', () => {
    beforeEach(() => {
        ordersReales.cancelSession(ORDERS)
        jest.clearAllMocks()
    })

    test('no se anuncia como venta creada', async () => {
        axios.post.mockResolvedValue({
            data: { pedido_id: 105, total: '500.00', duplicado: true } })
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'gorra barbas', 500, 230)
        const sock = { sendMessage: jest.fn() }

        await handleOrdersMessage(sock, mensaje('/cerrar', 'WA-DUP'))

        const texto = sock.sendMessage.mock.calls.map(c => c[1].text).join('\n')
        expect(texto).toContain('ya estaba registrada')
        expect(texto).toContain('#105')
        expect(texto).not.toContain('creado')
    })
})
