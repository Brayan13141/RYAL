const fs = require('fs')
const path = require('path')

const SRC = fs.readFileSync(path.join(__dirname, 'bot.js'), 'utf8')

describe('bot-p3 carga sin conectarse', () => {
    test('el modulo se puede requerir sin abrir WhatsApp', () => {
        // require.main !== module → main() no corre. Si quedara una referencia
        // a algo borrado en el camino de carga, esto explota.
        const bot = require('./bot')
        expect(typeof bot.handleOrdersMessage).toBe('function')
        expect(typeof bot.enviarVentaTienda).toBe('function')
    })
})

describe('lo que esta instancia NO debe tener', () => {
    // Estas no son pruebas de comportamiento: son candados. La bienvenida
    // borrada no puede dispararse por una variable de entorno mal puesta,
    // que es exactamente el riesgo de haberla dejado apagada con un flag.
    test('no existe ninguna ruta de bienvenida ni menu de clientes', () => {
        expect(SRC).not.toMatch(/WELCOME_MESSAGE|menuReply|createWelcomeStore|markSeenBulk/)
    })

    test('no atiende chats privados', () => {
        expect(SRC).not.toMatch(/handleClientMessage/)
    })

    test('no reenvia del proveedor ni publica promos', () => {
        expect(SRC).not.toMatch(/handleSupplierMessage|handleRyalMessage|matchPromo|createBatchBuffer/)
    })

    test('no levanta el servidor de avisos de persona1', () => {
        expect(SRC).not.toMatch(/startNotifyServer|require\('http'\)/)
    })
})

describe('la entrada de mensajes pasa por la regla probada', () => {
    test('bot.js delega en shouldHandleOrders y no reimplementa el filtro', () => {
        expect(SRC).toMatch(/shouldHandleOrders\(msg, ORDERS_GID\)/)
        // Nada de rutas alternativas hacia el manejador
        const llamadas = SRC.match(/await handleOrdersMessage\(/g) || []
        expect(llamadas).toHaveLength(1)
    })
})

describe('higiene para un repo publico', () => {
    test('no hay ningun telefono ni JID escrito en el codigo', () => {
        expect(SRC).not.toMatch(/\b52\d{10,}\b/)
        expect(SRC).not.toMatch(/@s\.whatsapp\.net'/)
        expect(SRC).not.toMatch(/\d{18}@g\.us/)
    })
})
