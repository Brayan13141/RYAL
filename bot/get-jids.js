// Helper local: descubre los JIDs de los grupos de WhatsApp.
// Reusa la misma sesión `.baileys_auth` que bot.js → escaneas el QR una sola vez.
//
// Uso:
//   1) node get-jids.js  → escanea el QR (tras emparejar puede reconectar solo, es normal)
//   2) Espera "✓ Conectado".
//   3) Manda un mensaje en el GRUPO DEL PROVEEDOR y otro en el GRUPO RYAL.
//   4) Copia los "GROUP JID" a tu .env (SUPPLIER_GROUP_ID / RYAL_GROUP_ID).
//   5) Ctrl+C. Luego `node bot.js` ya NO pedirá QR (reusa la sesión).
const qrcode = require('qrcode-terminal')
const pino = require('pino')

let makeWASocket, useMultiFileAuthState, DisconnectReason, version

async function connect() {
    const { state, saveCreds } = await useMultiFileAuthState('.baileys_auth')
    const sock = makeWASocket({ auth: state, logger: pino({ level: 'warn' }), version })

    sock.ev.on('creds.update', saveCreds)

    sock.ev.on('connection.update', async (u) => {
        const { qr, connection, lastDisconnect } = u
        if (qr) {
            console.log('\n=== ESCANEA ESTE QR (WhatsApp → Dispositivos vinculados) ===')
            qrcode.generate(qr, { small: true })
        }
        if (connection === 'open') {
            console.log('\n✓ Conectado. Tus grupos (nombre → JID):\n')
            try {
                const groups = await sock.groupFetchAllParticipating()
                const list = Object.values(groups).sort((a, b) => (a.subject || '').localeCompare(b.subject || ''))
                for (const g of list) {
                    console.log(`  ${g.subject}\n      ${g.id}`)
                }
                console.log('\nCopia a tu .env el JID del grupo del PROVEEDOR y del GRUPO RYAL. Luego Ctrl+C.\n')
            } catch (e) {
                console.log('No pude listar grupos:', e.message, '\n→ alterna: espera a que el proveedor publique; su JID saldrá abajo.')
            }
        }
        if (connection === 'close') {
            const code = lastDisconnect?.error?.output?.statusCode
            if (code === DisconnectReason.loggedOut) {
                console.log('Sesión cerrada. Borra .baileys_auth/ y reintenta.')
                process.exit(0)
            } else {
                // 515 (restart required) tras emparejar, u otros cierres → reconectar
                console.log('[reconectando...]', lastDisconnect?.error?.message || code || '')
                setTimeout(connect, 2000)
            }
        }
    })

    sock.ev.on('messages.upsert', ({ messages }) => {
        for (const m of messages) {
            const jid = m.key.remoteJid
            if (jid && jid.endsWith('@g.us')) {
                console.log('GROUP JID:', jid, m.pushName ? `(envió: ${m.pushName})` : '')
            }
        }
    })
}

async function main() {
    const baileys = await import('@whiskeysockets/baileys')   // Baileys es ESM-only
    makeWASocket = baileys.default
    useMultiFileAuthState = baileys.useMultiFileAuthState
    DisconnectReason = baileys.DisconnectReason
    try {
        version = (await baileys.fetchLatestBaileysVersion()).version
        console.log('WA Web version:', version.join('.'))
    } catch (e) {
        console.log('No se pudo obtener versión de WA (sigo igual):', e.message)
    }
    console.log('Conectando a WhatsApp...')
    await connect()
}

main().catch((e) => console.error('FATAL en get-jids:', e))
