const { parsePeerJids, esDeOtraInstancia, normalizar } = require('./peerBots')

const P3_TEL = '5214451000181'
const P3_LID = '233299133886498'

describe('normalizar', () => {
    test('ignora dominio, dispositivo y símbolos', () => {
        expect(normalizar('5214451000181:2@s.whatsapp.net')).toBe(P3_TEL)
        expect(normalizar('233299133886498:2@lid')).toBe(P3_LID)
        expect(normalizar('+52 144 510 00181')).toBe(P3_TEL)
    })

    test('lo vacío no se vuelve una cadena que matchee', () => {
        expect(normalizar(undefined)).toBe('')
        expect(normalizar('@lid')).toBe('')
    })
})

describe('esDeOtraInstancia', () => {
    const peers = parsePeerJids(`${P3_TEL},${P3_LID}`)
    const ajeno = (participant) => ({ key: { fromMe: false, participant }, message: {} })

    test('reconoce a bot-p3 por teléfono, con otro dispositivo', () => {
        expect(esDeOtraInstancia(ajeno('5214451000181:7@s.whatsapp.net'), peers)).toBe(true)
    })

    test('reconoce a bot-p3 por LID', () => {
        expect(esDeOtraInstancia(ajeno('233299133886498:2@lid'), peers)).toBe(true)
    })

    test('reconoce al autor venga en el campo que venga', () => {
        for (const campo of ['participant', 'participantAlt', 'participantPn']) {
            const msg = { key: { fromMe: false, [campo]: `${P3_LID}:2@lid` }, message: {} }
            expect(esDeOtraInstancia(msg, peers)).toBe(true)
        }
    })

    test('un cliente cualquiera NO es otra instancia', () => {
        expect(esDeOtraInstancia(ajeno('5214439728793:1@s.whatsapp.net'), peers)).toBe(false)
    })

    test('los mensajes propios nunca se ignoran', () => {
        // Una lista mal cargada con el número propio dejaría a esta instancia
        // sin atender sus comandos: el hueco es peor que el duplicado.
        const propio = { key: { fromMe: true, participant: `${P3_TEL}:2@s.whatsapp.net` }, message: {} }
        expect(esDeOtraInstancia(propio, peers)).toBe(false)
    })

    test('sin lista configurada no se ignora a nadie', () => {
        expect(esDeOtraInstancia(ajeno(`${P3_LID}:2@lid`), parsePeerJids(''))).toBe(false)
        expect(esDeOtraInstancia(ajeno(`${P3_LID}:2@lid`), parsePeerJids(undefined))).toBe(false)
    })

    test('un mensaje sin autor no matchea por vacío', () => {
        expect(esDeOtraInstancia(ajeno(undefined), peers)).toBe(false)
    })
})
