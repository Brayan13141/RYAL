const fs = require('fs')

function writeQrState(filePath, status, qr) {
    try {
        const data = { status, qr: qr || null, updated_at: new Date().toISOString() }
        fs.writeFileSync(filePath, JSON.stringify(data))
    } catch (err) {
        // No tumbar el bot por un fallo de escritura de estado — el QR en
        // terminal (qrcode-terminal) sigue siendo el fallback.
        console.error('writeQrState falló:', err.message)
    }
}

module.exports = { writeQrState }
