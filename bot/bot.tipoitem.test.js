// Integración sobre el dispatcher real de bot.js. El módulo puro ya está
// cubierto por avisoTipoItem.test.js; lo que se prueba acá es el cableado:
// que al cargar un ítem el bot CONSULTE el tipo y pegue el aviso en la misma
// confirmación. Un test que solo ejercitara el módulo pasaría aunque el
// cableado no existiera.

process.env.ORDERS_GROUP_ID = 'orders@g.us'
process.env.DJANGO_API_URL  = 'http://localhost'
process.env.DJANGO_API_KEY  = 'test-key'

jest.mock('axios')
const axios = require('axios')
const { handleOrdersMessage, orders: ordersReales } = require('./bot')

const ORDERS = 'orders@g.us'

const mensajeDeTexto = (text) => ({
    key: { remoteJid: ORDERS, fromMe: true },
    message: { conversation: text },
})

const textoEnviado = (sock) => sock.sendMessage.mock.calls.map(c => c[1].text).join('\n')

describe('aviso de tipo desconocido al cargar un ítem de tienda', () => {
    beforeEach(() => {
        ordersReales.cancelSession(ORDERS)
        jest.clearAllMocks()
    })

    test('un ítem cuyo texto no tiene tipo se confirma CON el aviso', async () => {
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        axios.post.mockResolvedValue({ data: { match: false, nombre: null, costo: 0 } })
        const sock = { sendMessage: jest.fn() }

        await handleOrdersMessage(sock, mensajeDeTexto('metcon 760'))

        const texto = textoEnviado(sock)
        expect(texto).toContain('Ítem 1')
        expect(texto).toContain('«metcon»')
        expect(texto).toContain('Tipos de artículo')
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(1)
    })

    test('la consulta va al endpoint de búsqueda con la descripción del ítem', async () => {
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        axios.post.mockResolvedValue({ data: { match: false } })
        const sock = { sendMessage: jest.fn() }

        await handleOrdersMessage(sock, mensajeDeTexto('metcon 760'))

        expect(axios.post).toHaveBeenCalledWith(
            'http://localhost/api/negocio/articulo/buscar/',
            { descripcion: 'metcon' },
            expect.anything(),
        )
    })

    test('un ítem con tipo conocido se confirma SIN aviso', async () => {
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        axios.post.mockResolvedValue({ data: { match: true, nombre: 'Oncloud', costo: 660 } })
        const sock = { sendMessage: jest.fn() }

        await handleOrdersMessage(sock, mensajeDeTexto('oncloud 760'))

        const texto = textoEnviado(sock)
        expect(texto).toContain('Ítem 1')
        expect(texto).not.toContain('no coincide con ningún tipo')
    })

    test('si la consulta falla, el ítem se carga igual y sin aviso', async () => {
        // La captura de ventas no puede depender de que Django responda: un
        // timeout no debe trabar el mostrador ni inventar un aviso falso.
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        axios.post.mockRejectedValue(new Error('ECONNREFUSED'))
        const sock = { sendMessage: jest.fn() }

        await handleOrdersMessage(sock, mensajeDeTexto('metcon 760'))

        const texto = textoEnviado(sock)
        expect(texto).toContain('Ítem 1')
        expect(texto).not.toContain('no coincide con ningún tipo')
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(1)
    })
})
