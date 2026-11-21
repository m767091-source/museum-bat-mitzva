// Run with: node --test tests/puzzles.test.cjs
// Offline checks for the five student puzzles and their host instructions.
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'app.js'), 'utf8');
function readConstant(name) {
  const match = source.match(new RegExp('const ' + name + '=(\\[[^;]+\\]);'));
  assert.ok(match, 'Missing ' + name);
  return vm.runInNewContext(match[1]);
}
const goldaEvents = readConstant('goldaEvents');
const goldaOrder = readConstant('goldaOrder');
const encrypted = readConstant('encrypted');
const decoded = readConstant('decoded');
const peopleLetters = [...source.matchAll(/letter:'([^']+)'/g)].map(m => m[1]);
test('Golda: six distinct events in historically correct positions', () => {
  assert.equal(goldaEvents.length, 6);
  assert.equal(new Set(goldaOrder).size, 6);
  assert.deepEqual(Array.from(goldaOrder), [4,1,6,2,5,3]);
  const years = [1948,1898,1969,1906,1956,1921];
  const chronological = years.map((year, i) => ({year, pos: goldaOrder[i]}))
    .sort((a,b) => a.pos - b.pos).map(x => x.year);
  assert.deepEqual(chronological, [1898,1906,1921,1948,1956,1969]);
});
test('Hannah: five girls plus a sixth facilitator letter spell אמיצות', () => {
  assert.equal(encrypted.length, 6);
  assert.equal(decoded.length, 6);
  assert.equal(decoded.join(''), 'אמיצות');
  const alphabet = 'אבגדהוזחטיכלמנסעפצקרשת';
  for (let i=0; i<6; i++) {
    const j=alphabet.indexOf(encrypted[i]);
    assert.ok(j >= 0, 'Unknown encrypted letter');
    assert.equal(decoded[i], alphabet[alphabet.length - 1 - j]);
  }
});
test('Five galleries reveal the intended final word', () => {
  assert.equal(peopleLetters.join(''), 'מצווה');
  assert.equal(40+90+6+6+5, 147);
  assert.match(source, /code:'147'/);
});
test('Student answers, feedback and teacher instructions exist for all five challenges', () => {
  for (let i=0; i<5; i++) assert.match(source, new RegExp('if\\(i===' + i + '\\)'));
  for (const marker of ['מסמך היסטורי מסווג','מגדל ההתמדה','מברק מוצפן','מעבדת השאלות','מילים שהופכות לשיר']) {
    assert.ok(source.includes(marker), 'Missing task: ' + marker);
  }
  assert.match(source, /התשובה עדיין לא נכונה/);
  assert.match(source, /האות לא תואמת למפתח/);
});
test('Hannah and Naomi remain letters; other three remain videos', () => {
  assert.match(source, /video:'videos\/golda.mp4'/);
  assert.match(source, /video:'videos\/linoy.mp4'/);
  assert.match(source, /video:'videos\/ada.mp4'/);
  assert.match(source, /'חנה סנש','נעמי שמר'/);
  assert.match(source, /מכתב מומחז ודמיוני/);
});
