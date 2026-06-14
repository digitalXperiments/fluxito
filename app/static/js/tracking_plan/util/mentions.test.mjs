// app/static/js/tracking_plan/util/mentions.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseMentions, renderMentions } from './mentions.js';

const members = [
  { id: 'u-ann', display_name: 'Ann Lee' },
  { id: 'u-bob', display_name: 'Bob' },
];

test('parseMentions collects ids for @display tokens', () => {
  const { mentions, text } = parseMentions('hey @Ann Lee and @Bob ok', members);
  assert.deepEqual(mentions.sort(), ['u-ann', 'u-bob']);
  assert.equal(text, 'hey @Ann Lee and @Bob ok'); // text unchanged; ids extracted
});

test('parseMentions ignores unknown @tokens', () => {
  const { mentions } = parseMentions('hi @Nobody', members);
  assert.deepEqual(mentions, []);
});

test('parseMentions dedupes repeated mentions', () => {
  const { mentions } = parseMentions('@Bob @Bob', members);
  assert.deepEqual(mentions, ['u-bob']);
});

test('renderMentions wraps known display names in a highlight span', () => {
  const html = renderMentions('ping @Bob now', members);
  assert.match(html, /<span class="tp-mention">@Bob<\/span>/);
  assert.ok(!html.includes('<script>'));
});

test('renderMentions escapes html in surrounding text', () => {
  const html = renderMentions('<b>x</b> @Bob', members);
  assert.match(html, /&lt;b&gt;x&lt;\/b&gt;/);
});
