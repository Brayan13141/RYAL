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
        // Ya no guarda un snapshot del payload — el reintento se arma desde
        // la sesión actual (ver CRITICAL 1). Solo persiste lo que no sale de
        // la sesión: el endpoint y el envío.
        expect(pending.payload.endpoint).toContain('/api/negocio/tienda/')
        expect(pending.payload.envio).toBe(0)
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

    test('CRITICAL: el reintento manda el payload actual de la sesion, no el snapshot del 409', async () => {
        // Reproduce el bug real: entre el 409 y la respuesta numerica se carga
        // un segundo item (el texto libre no mira el pending). El reintento
        // tiene que mandar los DOS items, no solo el que existia cuando se
        // armo el pending — si no, ese segundo item se pierde sin dejar rastro.
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'Jordan', 750)
        ordersReales.setQty(ORDERS, 1, 9)

        axios.post.mockRejectedValueOnce({
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

        // Se carga un segundo item MIENTRAS el pending sigue vivo
        await handleOrdersMessage(sock, mensajeDeTexto('Nike 500'))
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(2)

        axios.post
            .mockResolvedValueOnce({ data: { ok: true } })                       // POST alias
            .mockResolvedValueOnce({ data: { pedido_id: 99, total: '7250.00' } }) // reintento

        await handleOrdersMessage(sock, mensajeDeTexto('1'))

        const reintento = axios.post.mock.calls[axios.post.mock.calls.length - 1]
        const payloadEnviado = reintento[1]
        expect(payloadEnviado.items).toHaveLength(2)
        expect(payloadEnviado.items.map(i => i.description)).toEqual(
            expect.arrayContaining(['Jordan', 'Nike']))
    })

    test('CRITICAL: si falla el POST del alias, el pending sobrevive y reintentar el numero no agrega un item fantasma', async () => {
        // Si clearPending corriera ANTES del POST, un fallo dejaria el pending
        // borrado: el reintento natural del usuario (teclear "1" otra vez)
        // caeria en el bloque de item de tienda y agregaria un articulo de $1.
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'Jordan', 750)
        ordersReales.setPending(ORDERS, 'sin_tipo', {
            detalles: [{ texto: 'Jordan', qty: 1, precio: 750, sugerencias: [] }],
            opciones: [{ tipo_id: 7, nombre: 'JORDAN 4', costo: 680 }],
            endpoint: ENDPOINT,
            envio: 0,
        })

        // El POST del alias falla las dos veces
        axios.post.mockRejectedValue({ message: 'network down' })
        const sock = { sendMessage: jest.fn() }

        await handleOrdersMessage(sock, mensajeDeTexto('1'))
        expect(ordersReales.getPending(ORDERS)).not.toBeNull()
        expect(ordersReales.getPending(ORDERS).type).toBe('sin_tipo')
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(1)

        // El usuario reintenta el mismo numero — no puede colarse como item de $1
        await handleOrdersMessage(sock, mensajeDeTexto('1'))
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(1)
        expect(ordersReales.getPending(ORDERS)).not.toBeNull()
    })

    test('IMPORTANT 3: sin sugerencias, un item con precio suelto se puede seguir cargando', async () => {
        // Antes: opciones=[] armaba pending igual, bareNum > 0 siempre superaba
        // opciones.length, y el pending se rearmaba para siempre — ningun item
        // de texto libre podia cargarse hasta /cancelar o /cerrar.
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'Jordan', 750)
        axios.post.mockRejectedValueOnce({
            response: {
                status: 409,
                data: { sin_tipo: [{ texto: 'Jordan', qty: 1, precio: 750, sugerencias: [] }] },
            },
        })
        const sock = { sendMessage: jest.fn() }
        await handleOrdersMessage(sock, mensajeDeTexto('/cerrar'))
        expect(ordersReales.getPending(ORDERS)).toBeNull()

        await handleOrdersMessage(sock, mensajeDeTexto('Gorra 200'))
        expect(ordersReales.getSession(ORDERS).items).toHaveLength(2)
    })

    test('CAMINO VERDE: numero -> alias con el body correcto -> reintento OK -> venta grabada', async () => {
        // El unico test que respondia un numero hacia fallar el reintento a
        // proposito, asi que el camino que decide si la venta SE GRABA no
        // estaba cubierto por nada. Y nadie verificaba el BODY del alias: si
        // alguien mandara elegido.nombre en vez de detalles[0].texto, el
        // alias se guardaria con el texto equivocado y todo seguia verde.
        ordersReales.startSession(ORDERS, 'Mostrador', 'TIENDA-MOSTRADOR', 'tienda')
        ordersReales.addItem(ORDERS, 'Jordan', 750)
        ordersReales.setPending(ORDERS, 'sin_tipo', {
            detalles: [{ texto: 'Jordan', qty: 1, precio: 750, sugerencias: [] }],
            opciones: [
                { tipo_id: 7, nombre: 'JORDAN 4', costo: 680 },
                { tipo_id: 9, nombre: 'Jordan 1', costo: 620 },
            ],
            endpoint: ENDPOINT,
            envio: 0,
        })

        axios.post
            .mockResolvedValueOnce({ data: { ok: true, creado: true } })
            .mockResolvedValueOnce({ data: { pedido_id: 42, total: '750.00', sin_tipo: [] } })

        const sock = { sendMessage: jest.fn() }
        await handleOrdersMessage(sock, mensajeDeTexto('2'))

        // El body del alias: el texto del detalle y el tipo de la opcion ELEGIDA
        const [urlAlias, bodyAlias] = axios.post.mock.calls[0]
        expect(String(urlAlias)).toContain('/api/negocio/alias/')
        expect(bodyAlias).toEqual({ texto: 'Jordan', tipo_id: 9 })

        // El reintento salio y la venta quedo grabada
        expect(String(axios.post.mock.calls[1][0])).toContain('/api/negocio/tienda/')
        const dichos = sock.sendMessage.mock.calls.map(c => c[1].text).join(' | ')
        expect(dichos).toContain('Pedido #42 creado')

        // Y la sesion se cerro, que es lo que distingue el camino verde
        expect(ordersReales.getSession(ORDERS)).toBeNull()
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
        expect(pending.payload.envio).toBe(PAYLOAD.envio)
        expect(pending.payload.opciones[0].tipo_id).toBe(1)
        expect(sock.sendMessage.mock.calls[0][1].text).toContain('NO registrada')
    })

    test('IMPORTANT 3: un 409 sin sugerencias NO arma pending — no puede trabar la carga de items', async () => {
        // Con opciones=[] el pending nunca podia resolverse (bareNum siempre
        // > opciones.length), asi que se rearmaba solo por siempre y el bloque
        // de item de tienda quedaba bloqueado por esRespuestaAPending.
        axios.post.mockRejectedValue({
            response: {
                status: 409,
                data: { sin_tipo: [{ texto: 'qwx', qty: 1, precio: 100, sugerencias: [] }] },
            },
        })
        const sock = { sendMessage: jest.fn() }
        const res = await enviarVentaTienda(sock, { endpoint: ENDPOINT, payload: PAYLOAD })

        expect(res.ok).toBe(false)
        expect(ordersReales.getPending(ORDERS)).toBeNull()
        expect(sock.sendMessage.mock.calls[0][1].text).toContain('no encontré ninguno parecido')
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
