const { normalizarNumero, yaVinculado, debePedirCodigo } = require('./pairing')

// Numeros de RELLENO con la forma exacta que WhatsApp acepta (521 + 10
// digitos). La forma se saco de leer el creds.me.id de las cuentas que si
// estan vinculadas y funcionando; los numeros reales viven en el vault y no
// en el repo, que es publico. Lo que los tests fijan es la REGLA de formato,
// no estos digitos.
const PERSONA1 = '5215550000001'
const PERSONA2 = '5215550000002'

describe('normalizarNumero', () => {
    test('a los 10 digitos nacionales les antepone 521', () => {
        expect(normalizarNumero('5550000003')).toBe('5215550000003')
    })

    test('conserva el 1 de Mexico: 52 + 10 digitos NO es el formato de WhatsApp', () => {
        // El caso que rompe en silencio. Mexico dejo de marcar el 1 en 2019,
        // pero TODAS las cuentas vinculadas de este proyecto tienen 521 en su
        // creds.me.id. Quitarlo daria un numero que no existe y la vinculacion
        // fallaria sin decir por que.
        expect(normalizarNumero('525550000003')).toBe('5215550000003')
    })

    test('acepta un numero ya en el formato correcto sin tocarlo', () => {
        expect(normalizarNumero(PERSONA2)).toBe(PERSONA2)
        expect(normalizarNumero(PERSONA1)).toBe(PERSONA1)
    })

    test('acepta lo que una persona escribe: +, espacios y guiones', () => {
        expect(normalizarNumero('+52 1 555 000 0003')).toBe('5215550000003')
        expect(normalizarNumero('555-000-0003')).toBe('5215550000003')
    })

    test('devuelve null en vez de adivinar cuando el largo no cuadra', () => {
        expect(normalizarNumero('12345')).toBeNull()
        expect(normalizarNumero('55500000030000')).toBeNull()
    })

    test('devuelve null con basura o vacio', () => {
        expect(normalizarNumero('')).toBeNull()
        expect(normalizarNumero(null)).toBeNull()
        expect(normalizarNumero(undefined)).toBeNull()
        expect(normalizarNumero('hola')).toBeNull()
    })
})

describe('yaVinculado', () => {
    test('`registered` NO sirve como senal: las cuentas vivas lo tienen en false', () => {
        // Baileys 7.0.0-rc14 inicializa registered:false en auth-utils.js y
        // NADA en la libreria lo pone en true. Verificado contra los creds.json
        // de persona1 y persona2, que estan vinculados y funcionando.
        const credsDeUnaCuentaViva = {
            registered: false,
            account: { details: 'x', accountSignature: 'y' },
            me: { id: `${PERSONA2}:4@s.whatsapp.net`, lid: '100000000000002:4@lid' },
            platform: 'smbi',
        }
        expect(yaVinculado(credsDeUnaCuentaViva)).toBe(true)
    })

    test('un me.id sin account NO cuenta como vinculado', () => {
        // requestPairingCode escribe creds.me ANTES de que la vinculacion
        // ocurra (socket.js:602). Si esto contara como vinculado, un intento
        // fallido dejaria al bot creyendose vinculado para siempre y no
        // volveria a pedir codigo nunca.
        const credsTrasPedirCodigoSinCompletar = {
            registered: false,
            me: { id: '5215550000003@s.whatsapp.net', name: '~' },
        }
        expect(yaVinculado(credsTrasPedirCodigoSinCompletar)).toBe(false)
    })

    test('unas credenciales recien inicializadas no estan vinculadas', () => {
        expect(yaVinculado({ registered: false })).toBe(false)
        expect(yaVinculado({})).toBe(false)
    })
})

describe('debePedirCodigo', () => {
    const frescas = { registered: false }

    test('pide codigo cuando hay numero configurado y no hay vinculo', () => {
        expect(debePedirCodigo({
            creds: frescas, numero: '5215550000003', yaPedido: false,
        })).toBe(true)
    })

    test('sin numero configurado la feature esta apagada: sigue el QR de siempre', () => {
        expect(debePedirCodigo({
            creds: frescas, numero: null, yaPedido: false,
        })).toBe(false)
    })

    test('NO pide un codigo nuevo en cada reconexion de una cuenta ya vinculada', () => {
        // El bug que evita este guard: como registered es SIEMPRE false, apoyarse
        // en el haria que un bot vinculado y trabajando pidiera codigo cada vez
        // que se reconecta.
        const vinculada = { registered: false, account: { details: 'x' }, platform: 'smbi' }
        expect(debePedirCodigo({
            creds: vinculada, numero: '5215550000003', yaPedido: false,
        })).toBe(false)
    })

    test('solo pide un codigo por proceso: los QR siguen llegando cada 60s', () => {
        // connection.update emite un qr nuevo cada 60s (qrTimeout). Sin este
        // guard se pediria un codigo por cada uno, y el que se esta tecleando
        // quedaria invalidado a mitad.
        expect(debePedirCodigo({
            creds: frescas, numero: '5215550000003', yaPedido: true,
        })).toBe(false)
    })
})
