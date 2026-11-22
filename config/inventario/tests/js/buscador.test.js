// El buscador de Stock: interpreta texto libre. Correr: node --test inventario/tests/js/buscador.test.js
const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const f = require(path.join(__dirname, '..', '..', '..', 'static', 'js', 'inventario_stock.js'));

const CAT = {
  modelos: [
    { clave: '1', nombre: 'JORDAN 4' },
    { clave: '2', nombre: 'Nike 270' },
    { clave: '3', nombre: 'Nike 270 Plus' },
    { clave: '4', nombre: 'Nike Air force' },
  ],
  colores: [
    { clave: 'negro', nombre: 'Negro' },
    { clave: 'negro total', nombre: 'Negro total' },
    { clave: 'cafe', nombre: 'Cafe' },
    { clave: 'blanco', nombre: 'Blanco' },
  ],
  tallas: ['25.5', '26', '27', '28'],
};
const claves = (o) => Object.keys(o).sort();

test('normalizar quita acentos, mayúsculas y espacios repetidos', () => {
  assert.equal(f.normalizar('  Café   MARÍTIMO '), 'cafe maritimo');
});

test('jordan 4 negro 27', () => {
  const r = f.interpretar('jordan 4 negro 27', CAT);
  assert.deepEqual(claves(r.modelo), ['1']);
  assert.deepEqual(claves(r.color), ['negro']);
  assert.deepEqual(claves(r.talla), ['27']);
  assert.deepEqual(r.sobra, []);
});

test('nike 270 27: el 27 es talla, no parte del modelo', () => {
  const r = f.interpretar('nike 270 27', CAT);
  assert.deepEqual(claves(r.modelo), ['2']);
  assert.deepEqual(claves(r.talla), ['27']);
});

test('nike 270 plus gana el modelo más largo', () => {
  const r = f.interpretar('nike 270 plus', CAT);
  assert.deepEqual(claves(r.modelo), ['3']);
});

test('negro total 26 no marca negro', () => {
  const r = f.interpretar('negro total 26', CAT);
  assert.deepEqual(claves(r.color), ['negro total']);
  assert.deepEqual(claves(r.talla), ['26']);
});

test('mayúsculas, acentos y vía', () => {
  const r = f.interpretar('NIKE AIR FORCE Café MARÍTIMO', CAT);
  assert.deepEqual(claves(r.modelo), ['4']);
  assert.deepEqual(claves(r.color), ['cafe']);
  assert.deepEqual(claves(r.via), ['maritimo']);
});

test('tallas con t y decimales sobrantes', () => {
  for (const txt of ['t25.5 aereo', '25.50 avion']) {
    const r = f.interpretar(txt, CAT);
    assert.deepEqual(claves(r.talla), ['25.5']);
    assert.deepEqual(claves(r.via), ['aereo']);
  }
});

test('una palabra a medias no marca nada', () => {
  const r = f.interpretar('jord', CAT);
  assert.deepEqual(claves(r.modelo), []);
  assert.deepEqual(r.sobra, ['jord']);
});

test('varios modelos y talla inexistente', () => {
  const r = f.interpretar('jordan 4 nike 270 31', CAT);
  assert.deepEqual(claves(r.modelo), ['1', '2']);
  assert.deepEqual(r.sobra, ['31']);
});

test('entendido guarda el orden de aparición con nombres del catálogo', () => {
  const r = f.interpretar('27 barco jordan 4', CAT);
  assert.deepEqual(r.entendido.map((e) => e.dim + ':' + e.nombre),
    ['talla:27', 'via:Marítimo', 'modelo:JORDAN 4']);
});

test('texto vacío', () => {
  for (const txt of ['', '   ']) {
    const r = f.interpretar(txt, CAT);
    assert.deepEqual([r.modelo, r.color, r.talla, r.via], [{}, {}, {}, {}]);
    assert.deepEqual(r.entendido, []);
    assert.deepEqual(r.sobra, []);
  }
});
