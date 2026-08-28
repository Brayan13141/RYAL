// Regresión del bug reportado el 2026-08-11: "no se mandan todas las fotos".
// El proveedor manda fotos → video → precio, todo del mismo producto. El bot
// tiraba el lote pendiente al ver el video, y el precio llegaba después a un
// buffer vacío ("Precio recibido sin lote pendiente — ignorado"). En 14 días
// eso descartó 1281 fotos en 193 lotes.
process.env.FORWARD_TO_RYAL   = 'true'
process.env.SUPPLIER_GROUP_ID = 'supplier@g.us'
process.env.RYAL_GROUP_ID     = 'ryal@g.us'

const { handleSupplierMessage, batch } = require('./bot')

const SUPPLIER = 'supplier@g.us'

const imageMsg = (caption = '') => ({
    key: { remoteJid: SUPPLIER },
    message: { imageMessage: { caption, mimetype: 'image/jpeg' } },
})
const videoMsg = () => ({
    key: { remoteJid: SUPPLIER },
    message: { videoMessage: { mimetype: 'video/mp4' } },
})

beforeEach(() => batch.flush(SUPPLIER))

test('un video del proveedor no descarta las fotos que esperan precio', async () => {
    const sock = { sendMessage: jest.fn() }

    await handleSupplierMessage(sock, imageMsg('Nike Rojo'))
    await handleSupplierMessage(sock, imageMsg('Nike Azul'))
    expect(batch.size(SUPPLIER)).toBe(2)

    await handleSupplierMessage(sock, videoMsg())

    expect(batch.size(SUPPLIER)).toBe(2)
    expect(sock.sendMessage).not.toHaveBeenCalled()
})
