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
process.env.VENTA_REINTENTO_MS = '0'   // sin esperas reales entre reintentos

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

describe('reintento cuando no sabemos si la venta entró', () => {
    // El hueco que quedaba abierto: si Django CREA el pedido pero la respuesta
    // no llega antes del timeout, el bot decía «Intenta de nuevo» y dejaba la
    // sesión viva. Al reteclear `/cerrar` el mensaje tiene OTRO id, o sea otra
    // idem_key, y Django creaba un SEGUNDO pedido. Reintentar lo tiene que
    // hacer el bot, con la MISMA clave — no la persona con un mensaje nuevo.

    const sinRespuesta = () => Object.assign(new Error('timeout of 10000ms exceeded'), {
        code: 'ECONNABORTED', response: undefined })
    const conStatus = (status, data = {}) => Object.assign(new Error(`status ${status}`), {
        response: { status, data } })

    const postsDeVenta = () => axios.post.mock.calls.filter(
        ([url]) => url.endsWith('/api/negocio/tienda/') || url.endsWith('/api/negocio/pedido/'))

    const textoEnviado = (sock) => sock.sendMessage.mock.calls.map(c => c[1].text).join('\n')

    // Sin valor por defecto a propósito: `id = 'X'` haría que pasar
    // `undefined` cayera en el default y el caso «sin clave» nunca se probara.
    const conSesionCerrada = async (sock, id) => {
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'gorra barbas', 500, 230)
        await handleOrdersMessage(sock, mensaje('/cerrar', id))
    }

    beforeEach(() => {
        ordersReales.cancelSession(ORDERS)
        jest.clearAllMocks()
    })

    test('un timeout se reintenta con la MISMA idem_key y la venta se confirma', async () => {
        axios.post
            .mockRejectedValueOnce(sinRespuesta())
            .mockResolvedValueOnce({ data: { pedido_id: 105, total: '500.00', duplicado: true } })
        const sock = { sendMessage: jest.fn() }

        await conSesionCerrada(sock, 'WA-TIMEOUT')

        const posts = postsDeVenta()
        expect(posts).toHaveLength(2)
        expect(posts[0][1].idem_key).toBe('wa:WA-TIMEOUT')
        expect(posts[1][1].idem_key).toBe('wa:WA-TIMEOUT')
        // Django reconoció el POST perdido: un solo pedido, y el bot lo dice.
        expect(textoEnviado(sock)).toContain('#105')
        expect(ordersReales.getSession(ORDERS)).toBeNull()
    })

    test('SIN idem_key no se reintenta — reintentar a ciegas duplicaría', async () => {
        // Sin clave Django no tiene con qué reconocer el POST anterior, así
        // que un reintento automático crearía la segunda venta él solito.
        axios.post.mockRejectedValue(sinRespuesta())
        const sock = { sendMessage: jest.fn() }

        await conSesionCerrada(sock, undefined)

        expect(postsDeVenta()).toHaveLength(1)
    })

    test('un 400 no se reintenta: es una respuesta definitiva', async () => {
        axios.post.mockRejectedValue(conStatus(400, { error: 'items vacíos' }))
        await conSesionCerrada({ sendMessage: jest.fn() }, 'WA-400')
        expect(postsDeVenta()).toHaveLength(1)
    })

    test('un 409 no se reintenta: la venta está retenida, no perdida', async () => {
        axios.post.mockRejectedValue(conStatus(409, { sin_tipo: [] }))
        await conSesionCerrada({ sendMessage: jest.fn() }, 'WA-409')
        expect(postsDeVenta()).toHaveLength(1)
        // Y la sesión sobrevive, como siempre.
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(1)
    })

    test('un 500 sí se reintenta: tampoco sabemos si alcanzó a grabar', async () => {
        axios.post
            .mockRejectedValueOnce(conStatus(500, { error: 'boom' }))
            .mockResolvedValueOnce({ data: { pedido_id: 106, total: '500.00' } })
        await conSesionCerrada({ sendMessage: jest.fn() }, 'WA-500')
        expect(postsDeVenta()).toHaveLength(2)
    })

    test('agotados los reintentos, el bot NO dice «intenta de nuevo»', async () => {
        // Ese texto es exactamente la instrucción que duplica la venta.
        axios.post.mockRejectedValue(sinRespuesta())
        const sock = { sendMessage: jest.fn() }

        await conSesionCerrada(sock, 'WA-MUERTO')

        expect(postsDeVenta()).toHaveLength(3)
        const texto = textoEnviado(sock)
        expect(texto).not.toMatch(/intenta de nuevo/i)
        expect(texto).toMatch(/no pude confirmar/i)
        expect(texto).toMatch(/panel/i)
        // La sesión NO se toca: los ítems tienen que sobrevivir.
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(1)
    })
})

describe('/pedido moda — el mismo hueco, el mismo remedio', () => {
    // `crearPedidoModa` tiene su propio axios.post con el mismo timeout de
    // 10 s. Sin reintento, un cierre perdido terminaba en «Intenta de nuevo»
    // y al reteclear el comando se creaba el segundo pedido igual que en
    // /cerrar. Pasa por el mismo camino de reintento.

    const sinRespuesta = () => Object.assign(new Error('timeout of 10000ms exceeded'), {
        code: 'ECONNABORTED', response: undefined })
    const postsDePedido = () => axios.post.mock.calls.filter(
        ([url]) => url.endsWith('/api/negocio/pedido/'))
    const textoEnviado = (sock) => sock.sendMessage.mock.calls.map(c => c[1].text).join('\n')

    const pedidoModa = async (sock, id) => {
        axios.get.mockResolvedValue({
            data: { clientes: [{ nombre: 'Victor', telefono: '5551110000', descuento: 0 }] } })
        await handleOrdersMessage(sock, mensaje('/pedido Victor moda 12 100', id))
    }

    beforeEach(() => {
        ordersReales.cancelSession(ORDERS)
        jest.clearAllMocks()
    })

    test('un timeout se reintenta con la MISMA idem_key', async () => {
        axios.post
            .mockRejectedValueOnce(sinRespuesta())
            .mockResolvedValueOnce({ data: { pedido_id: 210, total: '1200.00' } })

        await pedidoModa({ sendMessage: jest.fn() }, 'WA-MODA')

        const posts = postsDePedido()
        expect(posts).toHaveLength(2)
        expect(posts[0][1].idem_key).toBe('wa:WA-MODA')
        expect(posts[1][1].idem_key).toBe('wa:WA-MODA')
    })

    test('agotados los reintentos no manda a reteclear el comando', async () => {
        axios.post.mockRejectedValue(sinRespuesta())
        const sock = { sendMessage: jest.fn() }

        await pedidoModa(sock, 'WA-MODA-MUERTO')

        expect(postsDePedido()).toHaveLength(3)
        const texto = textoEnviado(sock)
        expect(texto).toMatch(/no pude confirmar/i)
        expect(texto).not.toMatch(/intenta de nuevo/i)
    })

    test('si Django dice duplicado, no se anuncia como creado', async () => {
        axios.post.mockResolvedValue({
            data: { pedido_id: 210, total: '1200.00', duplicado: true } })
        const sock = { sendMessage: jest.fn() }

        await pedidoModa(sock, 'WA-MODA-DUP')

        const texto = textoEnviado(sock)
        expect(texto).toContain('#210')
        expect(texto).toMatch(/ya estaba registrado/i)
        expect(texto).not.toMatch(/creado/i)
    })
})

