// Syntax-directed SPARQL editor for the conceptual-query pane (M9).
//
// Zero dependencies: a tokenizing <pre> underlay gives highlighting (concepts
// tinted by their OWNING SOURCE, unknown concepts flagged), and a caret-
// positioned menu gives completion driven by the live catalog vocabulary:
//   - after `a`            -> classes only (`?x a c:▮`)
//   - `c:` on a subject    -> that subject's class properties first (found by
//                             resolving `?subj a c:Class` in the query text)
//   - `?`                  -> variables already present in the query
//   - bare word            -> SPARQL keywords
// The <textarea> keeps its id and .value contract — everything else reads it
// exactly as before.

function initSparqlEditor(textareaId, vocab) {
  'use strict';
  const ta = document.getElementById(textareaId);
  if (!ta) return;

  const KEYWORDS = ['PREFIX', 'SELECT', 'WHERE', 'FILTER', 'OPTIONAL', 'VALUES',
                    'DISTINCT', 'LIMIT', 'ORDER BY', 'a', 'true', 'false'];
  const CLASSES = vocab.classes || {};
  // property -> owning kinds (a shared key like accountId spans sources)
  const PROP_OWNERS = {};
  for (const [cls, info] of Object.entries(CLASSES)) {
    for (const p of info.props) (PROP_OWNERS[p] = PROP_OWNERS[p] || []).push(cls);
  }

  // ---- overlay scaffolding -------------------------------------------------
  const wrap = document.createElement('div');
  wrap.className = 'sqed';
  ta.parentNode.insertBefore(wrap, ta);
  const hl = document.createElement('pre');
  hl.className = 'sqed-hl';
  hl.setAttribute('aria-hidden', 'true');
  const hlCode = document.createElement('code');
  hl.appendChild(hlCode);
  wrap.appendChild(hl);
  wrap.appendChild(ta);
  ta.classList.add('sqed-input');
  ta.setAttribute('autocomplete', 'off');
  ta.setAttribute('spellcheck', 'false');

  const menu = document.createElement('div');
  menu.className = 'sqed-menu';
  menu.setAttribute('role', 'listbox');
  menu.hidden = true;
  wrap.appendChild(menu);

  // Mirror div: measures the caret's pixel position inside the textarea.
  const mirror = document.createElement('div');
  mirror.className = 'sqed-mirror';
  mirror.setAttribute('aria-hidden', 'true');
  wrap.appendChild(mirror);

  const escHtml = (s) => s.replace(/[&<>]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

  // ---- highlighting --------------------------------------------------------
  const TOKEN_RE = new RegExp([
    '(#[^\\n]*)',                       // 1 comment
    '(<[^>\\n]*>)',                     // 2 IRI
    '("(?:[^"\\\\]|\\\\.)*")',          // 3 string
    '(c:[A-Za-z_][A-Za-z0-9_]*)',       // 4 concept
    '(\\?[A-Za-z_][A-Za-z0-9_]*)',      // 5 variable
    '(\\b(?:PREFIX|SELECT|WHERE|FILTER|OPTIONAL|VALUES|DISTINCT|LIMIT|ORDER|BY|true|false)\\b)', // 6 keyword
    '(\\ba\\b)',                        // 7 rdf:type shorthand
  ].join('|'), 'g');

  function conceptClass(token) {
    const name = token.slice(2);
    if (CLASSES[name]) return 'sqed-cls sqed-src-' + CLASSES[name].kind;
    const owners = PROP_OWNERS[name];
    if (owners) {
      const kinds = [...new Set(owners.map((c) => CLASSES[c].kind))];
      return kinds.length === 1 ? 'sqed-prop sqed-src-' + kinds[0] : 'sqed-prop';
    }
    return 'sqed-unknown'; // not in the catalog: this query will refuse
  }

  function paint() {
    const src = ta.value;
    let out = '', last = 0, m;
    TOKEN_RE.lastIndex = 0;
    while ((m = TOKEN_RE.exec(src))) {
      out += escHtml(src.slice(last, m.index));
      const t = m[0];
      const cls = m[1] ? 'sqed-com' : m[2] ? 'sqed-iri' : m[3] ? 'sqed-str'
        : m[4] ? conceptClass(t) : m[5] ? 'sqed-var' : m[6] ? 'sqed-kw' : 'sqed-kw';
      out += '<span class="' + cls + '">' + escHtml(t) + '</span>';
      last = m.index + t.length;
    }
    out += escHtml(src.slice(last));
    hlCode.innerHTML = out + '\n'; // trailing \n keeps the last line's height
    syncScroll();
  }

  function syncScroll() {
    hl.scrollTop = ta.scrollTop;
    hl.scrollLeft = ta.scrollLeft;
  }

  new ResizeObserver(() => { hl.style.height = ta.offsetHeight + 'px'; }).observe(ta);

  // ---- completion context --------------------------------------------------
  function tokenBeforeCaret() {
    const upto = ta.value.slice(0, ta.selectionStart);
    const m = upto.match(/(c:[A-Za-z0-9_]*|\?[A-Za-z0-9_]*|[A-Za-z][A-Za-z0-9_]*)$/);
    return m ? { text: m[0], start: upto.length - m[0].length, before: upto.slice(0, upto.length - m[0].length) } : null;
  }

  // The subject variable of the triple block the caret is in: last `?var`
  // that opens a statement (after `{`, `.`, or the block start).
  function currentSubject(before) {
    const seg = before.split(/[{.]/).pop() || '';
    const m = seg.match(/^\s*(\?[A-Za-z0-9_]+)/);
    return m ? m[1] : null;
  }

  function subjectClass(subjVar) {
    if (!subjVar) return null;
    const re = new RegExp('\\' + subjVar + '\\s+a\\s+c:([A-Za-z0-9_]+)');
    const m = ta.value.match(re);
    return m && CLASSES[m[1]] ? m[1] : null;
  }

  function candidates(tok) {
    const afterA = /(^|[\s;,({])a\s+$/.test(tok.before);
    if (tok.text.startsWith('?')) {
      const seen = new Set(ta.value.match(/\?[A-Za-z_][A-Za-z0-9_]*/g) || []);
      return [...seen].sort().map((v) => ({ label: v, insert: v, tag: 'var', hint: 'variable' }));
    }
    if (afterA || tok.text.startsWith('c:')) {
      const prefix = tok.text.startsWith('c:') ? tok.text.slice(2) : '';
      const out = [];
      if (!afterA) {
        const subj = subjectClass(currentSubject(tok.before));
        const scoped = subj ? [subj] : Object.keys(CLASSES);
        const seen = new Set();
        for (const cls of scoped) {
          for (const p of CLASSES[cls].props) {
            if (seen.has(p) || !p.startsWith(prefix)) continue;
            seen.add(p);
            out.push({ label: 'c:' + p, insert: 'c:' + p, tag: CLASSES[cls].kind,
                       hint: (subj ? subj : PROP_OWNERS[p].join(', ')) });
          }
        }
      }
      for (const [cls, info] of Object.entries(CLASSES)) {
        if (!cls.startsWith(prefix)) continue;
        out.push({ label: 'c:' + cls, insert: 'c:' + cls, tag: info.kind,
                   hint: 'class · ' + info.source, cls: true });
      }
      // after `a` only classes make sense; otherwise properties lead
      return afterA ? out.filter((c) => c.cls) : out;
    }
    return KEYWORDS
      .filter((k) => k.toLowerCase().startsWith(tok.text.toLowerCase()) && k !== tok.text)
      .map((k) => ({ label: k, insert: k, tag: 'kw', hint: 'SPARQL' }));
  }

  // ---- menu ----------------------------------------------------------------
  let active = -1, items = [], tokenAt = null;

  function caretXY() {
    const style = getComputedStyle(ta);
    for (const p of ['fontFamily','fontSize','fontWeight','lineHeight','letterSpacing',
                     'padding','borderWidth','boxSizing','whiteSpace','wordBreak','overflowWrap']) {
      mirror.style[p] = style[p];
    }
    mirror.style.width = ta.clientWidth + 'px';
    mirror.textContent = ta.value.slice(0, ta.selectionStart);
    const mark = document.createElement('span');
    mark.textContent = '​';
    mirror.appendChild(mark);
    return { x: mark.offsetLeft - ta.scrollLeft, y: mark.offsetTop - ta.scrollTop + 20 };
  }

  function openMenu() {
    tokenAt = tokenBeforeCaret();
    items = tokenAt ? candidates(tokenAt).slice(0, 12) : [];
    if (!items.length) { closeMenu(); return; }
    active = 0;
    menu.innerHTML = items.map((it, i) =>
      '<button type="button" role="option" data-i="' + i + '"' + (i === 0 ? ' class="on"' : '') + '>' +
      '<span class="sqed-mi ' + (it.tag === 'kw' || it.tag === 'var' ? '' : 'sqed-src-' + it.tag) + '">' +
      escHtml(it.label) + '</span><span class="sqed-mh">' + escHtml(it.hint) + '</span></button>').join('');
    const at = caretXY();
    menu.style.left = Math.min(at.x, ta.clientWidth - 240) + 'px';
    menu.style.top = at.y + 'px';
    menu.hidden = false;
    menu.querySelectorAll('button').forEach((b) => {
      b.onmousedown = (e) => { e.preventDefault(); accept(+b.dataset.i); };
    });
  }

  function closeMenu() { menu.hidden = true; items = []; active = -1; }

  function moveActive(d) {
    if (!items.length) return;
    active = (active + d + items.length) % items.length;
    menu.querySelectorAll('button').forEach((b, i) => b.classList.toggle('on', i === active));
    menu.querySelectorAll('button')[active].scrollIntoView({ block: 'nearest' });
  }

  function accept(i) {
    const it = items[i];
    if (!it || !tokenAt) return;
    const end = ta.selectionStart;
    ta.value = ta.value.slice(0, tokenAt.start) + it.insert + ta.value.slice(end);
    const pos = tokenAt.start + it.insert.length;
    ta.setSelectionRange(pos, pos);
    closeMenu();
    paint();
    ta.focus();
  }

  // ---- wiring --------------------------------------------------------------
  ta.addEventListener('input', () => { paint(); openMenu(); });
  ta.addEventListener('scroll', syncScroll);
  ta.addEventListener('blur', () => setTimeout(closeMenu, 120));
  ta.addEventListener('keydown', (e) => {
    if (menu.hidden) {
      if (e.key === ' ' && e.ctrlKey) { e.preventDefault(); openMenu(); } // manual trigger
      return;
    }
    if (e.key === 'ArrowDown') { e.preventDefault(); moveActive(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1); }
    else if (e.key === 'Tab' || e.key === 'Enter') { e.preventDefault(); accept(active); }
    else if (e.key === 'Escape') { closeMenu(); }
  });

  paint();
}
