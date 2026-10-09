// Run with: node website/test_home_search_scroll.js
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function element(classes = []) {
  const values = new Set(classes);
  return {
    listeners: {}, attributes: {}, style: {}, value: '',
    classList: {
      contains: name => values.has(name),
      add: name => values.add(name),
      remove: name => values.delete(name),
      toggle(name, force) {
        const enabled = force === undefined ? !values.has(name) : force;
        if (enabled) values.add(name); else values.delete(name);
        return enabled;
      },
    },
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attributes[name] = value; },
    contains(target) { return target === this || target === input; },
    focus() { document.activeElement = this; },
    getBoundingClientRect() { assert.fail('Scroll handling must not force layout reads'); },
    get scrollHeight() { assert.fail('Scroll handling must not force layout reads'); },
    get offsetHeight() { assert.fail('Scroll handling must not force layout reads'); },
  };
}

const navbar = element();
const input = element();
const toggle = element();
const bar = element(['search-bar--persistent']);
bar.closest = () => navbar;
const nodes = {
  'search-toggle': toggle, 'search-bar': bar, 'search-input': input,
  'clear-search': element(), 'search-results': element(),
};
const document = {
  activeElement: null,
  getElementById: id => nodes[id],
  addEventListener() {},
};
const frames = [];
const window = {
  scrollY: 0, location: { search: '' }, listeners: {},
  addEventListener(name, fn) { this.listeners[name] = fn; },
};
const script = fs.readFileSync('website/static/js/main.js', 'utf8').split('// ── مشاركة الملزمة')[0];
vm.runInNewContext(script, {
  document, window, URLSearchParams,
  requestAnimationFrame: fn => frames.push(fn), setTimeout, clearTimeout,
});
function scroll(y) {
  window.scrollY = y;
  window.listeners.scroll();
  while (frames.length) frames.shift()();
}
function collapsed() {
  return navbar.classList.contains('home-search-collapsed');
}
assert.equal(collapsed(), false, 'Starts expanded');
scroll(90);
assert.equal(collapsed(), false, 'Does not collapse near the top');
scroll(200);
assert.equal(collapsed(), true, 'Downward scrolling collapses search');
assert.equal(toggle.attributes['aria-expanded'], 'false');
scroll(195);
assert.equal(collapsed(), true, 'Small movements do not flicker');
scroll(165);
assert.equal(collapsed(), false, 'Upward scrolling restores search');
assert.deepEqual(bar.style, {}, 'Revealing search never changes inline layout styles');
scroll(185);
assert.equal(collapsed(), true, 'Direction reversal works without waiting for animation end');
scroll(155);
assert.equal(collapsed(), false, 'Rapid upward reversal restores search immediately');
scroll(200);
scroll(195);
scroll(190);
assert.equal(collapsed(), true, 'Tiny upward steps do not flicker');
scroll(187);
assert.equal(collapsed(), false, 'Small upward movements accumulate to reveal search');
window.scrollY = 250;
window.listeners.scroll();
window.scrollY = 260;
window.listeners.scroll();
assert.equal(frames.length, 1, 'Multiple scroll events use only one animation frame');
while (frames.length) frames.shift()();
assert.equal(collapsed(), true);
scroll(250);
toggle.listeners.click();
while (frames.length) frames.shift()();
assert.equal(collapsed(), false, 'The icon opens search');
assert.equal(document.activeElement, input, 'The icon focuses the field');
scroll(400);
assert.equal(collapsed(), false, 'Typing is not interrupted by scrolling');
document.activeElement = null;
scroll(500);
assert.equal(collapsed(), true);
scroll(0);
assert.equal(collapsed(), false, 'Returning to the top restores search');
assert.deepEqual(bar.style, {}, 'At the top, no positional or height reset is needed');
console.log('Homepage search scroll checks passed');
