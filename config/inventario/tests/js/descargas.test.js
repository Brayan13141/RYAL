const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

const f = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'inventario_stock.js'));

test('queryDescarga: parámetros repetidos, por dimensión y en orden alfabético', () => {
  const sel = { modelo: { 2: true, 1: true }, talla: { '27': true }, color: {}, via: { aereo: true } };
  assert.strictEqual(f.queryDescarga(sel), 'modelo=1&modelo=2&talla=27&via=aereo');
});

test('queryDescarga: sel vacío da cadena vacía', () => {
  assert.strictEqual(f.queryDescarga({ modelo: {}, talla: {}, color: {}, via: {} }), '');
  assert.strictEqual(f.queryDescarga({}), '');
});

test('queryDescarga: un color con espacio sale codificado', () => {
  const q = f.queryDescarga({ modelo: {}, talla: {}, color: { 'azul rey': true }, via: {} });
  assert.strictEqual(q, new URLSearchParams({ color: 'azul rey' }).toString());
});
