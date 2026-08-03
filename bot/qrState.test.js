const fs = require('fs')
const os = require('os')
const path = require('path')
const { writeQrState } = require('./qrState')

describe('writeQrState', () => {
    let tmpFile

    beforeEach(() => {
        tmpFile = path.join(os.tmpdir(), `qr_state_test_${Date.now()}_${Math.random()}.json`)
    })

    afterEach(() => {
        if (fs.existsSync(tmpFile)) fs.unlinkSync(tmpFile)
    })

    test('escribe status y qr cuando hay QR pendiente', () => {
        writeQrState(tmpFile, 'qr', '1@abc,def==,ghi==')
        const data = JSON.parse(fs.readFileSync(tmpFile, 'utf8'))
        expect(data.status).toBe('qr')
        expect(data.qr).toBe('1@abc,def==,ghi==')
        expect(typeof data.updated_at).toBe('string')
        expect(new Date(data.updated_at).toString()).not.toBe('Invalid Date')
    })

    test('escribe qr:null cuando status no trae QR (open)', () => {
        writeQrState(tmpFile, 'open', null)
        const data = JSON.parse(fs.readFileSync(tmpFile, 'utf8'))
        expect(data.status).toBe('open')
        expect(data.qr).toBeNull()
    })

    test('trata qr undefined igual que null', () => {
        writeQrState(tmpFile, 'logged_out')
        const data = JSON.parse(fs.readFileSync(tmpFile, 'utf8'))
        expect(data.qr).toBeNull()
    })

    test('no lanza si fs.writeFileSync falla', () => {
        const spy = jest.spyOn(fs, 'writeFileSync').mockImplementation(() => {
            throw new Error('disk full')
        })
        expect(() => writeQrState(tmpFile, 'qr', 'x')).not.toThrow()
        spy.mockRestore()
    })
})
