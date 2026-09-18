const http = require('http')
const { createNotifyHandler } = require('./notifyServer')

const TOKEN = 'a'.repeat(64)
const logger = { info: jest.fn(), warn: jest.fn(), error: jest.fn() }

function makeSock({ exists = true } = {}) {
    return {
        onWhatsApp: jest.fn(async (n) => (exists ? [{ exists: true, jid: `${n}@s.whatsapp.net` }] : [])),
        sendMessage: jest.fn(async () => ({})),
    }
}

function startServer(opts) {
    const handler = createNotifyHandler({
        ordersGid: '123@g.us', alertJid: '456@s.whatsapp.net', notifyToken: TOKEN, logger, ...opts,
    })
    return new Promise((resolve) => {
        const server = http.createServer(handler)
        server.listen(0, '127.0.0.1', () => resolve(server))
    })
}

function post(server, payload, headers = {}) {
    const data = typeof payload === 'string' ? payload : JSON.stringify(payload)
    return new Promise((resolve, reject) => {
        const req = http.request({
            host: '127.0.0.1', port: server.address().port, path: '/notify', method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data), ...headers },
        }, (res) => {
            let body = ''
            res.on('data', (c) => { body += c })
            res.on('end', () => resolve({ status: res.statusCode, body }))
        })
        req.on('error', reject)
        req.end(data)
    })
}

describe('/notify', () => {
    let server
    afterEach(() => new Promise((r) => server.close(r)))

    const CLIENTE = { message: 'hola Ana', target: 'customer', phone: '5512345678' }

    test('customer con token correcto resuelve el JID con onWhatsApp y envía', async () => {
        const sock = makeSock()
        server = await startServer({ getSock: () => sock })
        const res = await post(server, CLIENTE, { 'X-Notify-Token': TOKEN })
        expect(res.status).toBe(200)
        expect(sock.onWhatsApp).toHaveBeenCalledWith('525512345678')
        expect(sock.sendMessage).toHaveBeenCalledWith('525512345678@s.whatsapp.net', { text: 'hola Ana' })
    })

    test('customer sin token → 401 y no envía', async () => {
        const sock = makeSock()
        server = await startServer({ getSock: () => sock })
        const res = await post(server, CLIENTE)
        expect(res.status).toBe(401)
        expect(sock.sendMessage).not.toHaveBeenCalled()
    })

    test('customer con token incorrecto → 401 y no envía', async () => {
        const sock = makeSock()
        server = await startServer({ getSock: () => sock })
        const res = await post(server, CLIENTE, { 'X-Notify-Token': 'b'.repeat(64) })
        expect(res.status).toBe(401)
        expect(sock.sendMessage).not.toHaveBeenCalled()
    })

    test('instancia sin NOTIFY_TOKEN → customer 503', async () => {
        const sock = makeSock()
        server = await startServer({ getSock: () => sock, notifyToken: undefined })
        const res = await post(server, CLIENTE, { 'X-Notify-Token': TOKEN })
        expect(res.status).toBe(503)
        expect(sock.sendMessage).not.toHaveBeenCalled()
    })

    test('teléfono inválido → 400', async () => {
        const sock = makeSock()
        server = await startServer({ getSock: () => sock })
        const res = await post(server, { ...CLIENTE, phone: '123' }, { 'X-Notify-Token': TOKEN })
        expect(res.status).toBe(400)
        expect(sock.sendMessage).not.toHaveBeenCalled()
    })

    test('número sin WhatsApp → 404 y no envía', async () => {
        const sock = makeSock({ exists: false })
        server = await startServer({ getSock: () => sock })
        const res = await post(server, CLIENTE, { 'X-Notify-Token': TOKEN })
        expect(res.status).toBe(404)
        expect(sock.sendMessage).not.toHaveBeenCalled()
    })

    test('body de más de 16 KB → 413 y no envía', async () => {
        const sock = makeSock()
        server = await startServer({ getSock: () => sock })
        const res = await post(server, { message: 'x'.repeat(17 * 1024), target: 'orders' })
        expect(res.status).toBe(413)
        expect(sock.sendMessage).not.toHaveBeenCalled()
    })

    test('"orders" sin token sigue enviando al grupo', async () => {
        const sock = makeSock()
        server = await startServer({ getSock: () => sock })
        const res = await post(server, { message: 'pedido nuevo', target: 'orders' })
        expect(res.status).toBe(200)
        expect(sock.sendMessage).toHaveBeenCalledWith('123@g.us', { text: 'pedido nuevo' })
    })

    test('sin target (watchdog) sin token sigue enviando a la alerta', async () => {
        const sock = makeSock()
        server = await startServer({ getSock: () => sock })
        const res = await post(server, { message: 'bot caído' })
        expect(res.status).toBe(200)
        expect(sock.sendMessage).toHaveBeenCalledWith('456@s.whatsapp.net', { text: 'bot caído' })
    })

    test('bot sin conectar → 503', async () => {
        server = await startServer({ getSock: () => null })
        const res = await post(server, { message: 'x', target: 'orders' })
        expect(res.status).toBe(503)
    })
})
