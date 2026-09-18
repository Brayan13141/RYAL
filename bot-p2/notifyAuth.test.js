const { MAX_NOTIFY_BODY, isValidToken } = require('./notifyAuth')

describe('isValidToken', () => {
    const TOKEN = 'a'.repeat(64)

    test('token igual → true', () => {
        expect(isValidToken(TOKEN, TOKEN)).toBe(true)
    })
    test('token distinto de la misma longitud → false', () => {
        expect(isValidToken('b'.repeat(64), TOKEN)).toBe(false)
    })
    test('token de otra longitud → false (sin lanzar)', () => {
        expect(isValidToken('abc', TOKEN)).toBe(false)
    })
    test('header ausente → false', () => {
        expect(isValidToken(undefined, TOKEN)).toBe(false)
    })
    test('instancia sin token configurado → false aunque el header venga vacío', () => {
        expect(isValidToken('', '')).toBe(false)
        expect(isValidToken('', undefined)).toBe(false)
    })
})

test('el límite de body es 16 KB', () => {
    expect(MAX_NOTIFY_BODY).toBe(16 * 1024)
})
