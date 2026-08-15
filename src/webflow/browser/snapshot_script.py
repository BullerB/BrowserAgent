"""The in-page script that produces a page snapshot.

Kept as a single JS string so it can be evaluated in every frame in one round
trip. It returns only what the planner needs - a large DOM dump would blow the
context window and slow every step down.
"""

from __future__ import annotations

INTERACTIVE_SELECTOR = (
    "a[href], button, input:not([type='hidden']), select, textarea, summary, "
    "[role='button'], [role='link'], [role='checkbox'], [role='radio'], "
    "[role='combobox'], [role='switch'], [role='tab'], [role='option'], "
    "[role='menuitem'], [role='slider'], [role='spinbutton'], [role='textbox'], "
    "[contenteditable='true'], [tabindex]:not([tabindex='-1'])"
)

SNAPSHOT_JS = """
(config) => {
  const MAX_ELEMENTS = config.maxElements;
  const trim = (s, n) => (s || '').replace(/\\s+/g, ' ').trim().slice(0, n);

  function visible(el) {
    const st = window.getComputedStyle(el);
    if (st.visibility === 'hidden' || st.display === 'none') return false;
    if (parseFloat(st.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  }

  function role(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit.split(/\\s+/)[0];
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return el.hasAttribute('href') ? 'link' : 'generic';
    if (tag === 'button' || tag === 'summary') return 'button';
    if (tag === 'select') return el.multiple ? 'listbox' : 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (t === 'submit' || t === 'button' || t === 'reset' || t === 'image') return 'button';
      if (t === 'number') return 'spinbutton';
      if (t === 'range') return 'slider';
      if (t === 'search') return 'searchbox';
      return 'textbox';
    }
    return 'generic';
  }

  function labelText(el) {
    if (el.id) {
      const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (lab) return trim(lab.innerText, 120);
    }
    const wrapper = el.closest('label');
    if (wrapper) return trim(wrapper.innerText, 120);
    return null;
  }

  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return trim(aria, 120);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const text = by.split(/\\s+/)
        .map((id) => { const n = document.getElementById(id); return n ? n.innerText : ''; })
        .join(' ');
      if (trim(text, 120)) return trim(text, 120);
    }
    const lab = labelText(el);
    if (lab) return lab;
    const own = trim(el.innerText, 120);
    if (own) return own;
    for (const attr of ['placeholder', 'title', 'alt', 'name']) {
      const v = el.getAttribute(attr);
      if (v) return trim(v, 120);
    }
    if (el.tagName === 'INPUT' && el.value && el.type === 'submit') return trim(el.value, 120);
    return '';
  }

  function groupLabel(el) {
    const fs = el.closest('fieldset');
    if (fs) {
      const lg = fs.querySelector('legend');
      if (lg && trim(lg.innerText, 120)) return trim(lg.innerText, 120);
    }
    const grp = el.closest('[role="group"],[role="radiogroup"]');
    if (grp) {
      const al = grp.getAttribute('aria-label');
      if (al) return trim(al, 120);
    }
    let node = el.parentElement;
    let hops = 0;
    while (node && hops < 5) {
      const h = node.querySelector('h1,h2,h3,h4,legend,label,p');
      if (h && !h.contains(el) && trim(h.innerText, 120)) return trim(h.innerText, 120);
      node = node.parentElement;
      hops += 1;
    }
    return null;
  }

  function cssPath(el) {
    if (el.id) return '[id="' + el.id + '"]';
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && parts.length < 6) {
      if (node.id) { parts.unshift('[id="' + node.id + '"]'); break; }
      const tag = node.tagName.toLowerCase();
      if (tag === 'body' || tag === 'html') break;
      const parent = node.parentElement;
      if (!parent) { parts.unshift(tag); break; }
      const sibs = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
      parts.unshift(sibs.length > 1 ? tag + ':nth-of-type(' + (sibs.indexOf(node) + 1) + ')' : tag);
      node = parent;
    }
    return parts.join(' > ');
  }

  const seen = new Set();
  const elements = [];
  const nodes = Array.from(document.querySelectorAll(config.selector));
  for (const el of nodes) {
    if (elements.length >= MAX_ELEMENTS) break;
    if (!visible(el)) continue;
    if (seen.has(el)) continue;
    seen.add(el);
    const tag = el.tagName.toLowerCase();
    const options = tag === 'select'
      ? Array.from(el.options).map((o) => trim(o.label || o.text, 80)).filter(Boolean)
      : [];
    const checked = (tag === 'input' && (el.type === 'checkbox' || el.type === 'radio'))
      ? !!el.checked
      : (el.getAttribute('aria-checked') === 'true' ? true : null);
    elements.push({
      tag: tag,
      role: role(el),
      name: accessibleName(el),
      input_type: el.getAttribute('type'),
      value: tag === 'select' || tag === 'input' || tag === 'textarea'
        ? trim(el.value, 120) || null : null,
      placeholder: el.getAttribute('placeholder'),
      label: labelText(el),
      options: options.slice(0, 40),
      checked: checked,
      required: el.hasAttribute('required') || el.getAttribute('aria-required') === 'true',
      disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
      element_id: el.id || null,
      name_attr: el.getAttribute('name'),
      test_id: el.getAttribute('data-testid') || el.getAttribute('data-test-id')
        || el.getAttribute('data-test') || null,
      css: cssPath(el),
      group_label: groupLabel(el),
    });
  }

  const validation = [];
  const errorNodes = document.querySelectorAll(
    '[role="alert"], [aria-invalid="true"], .error, .invalid, .field-error, .is-invalid'
  );
  for (const n of Array.from(errorNodes).slice(0, 20)) {
    if (!visible(n)) continue;
    const t = trim(n.innerText || n.getAttribute('aria-label'), 200);
    if (t && !validation.includes(t)) validation.push(t);
  }
  for (const n of Array.from(document.querySelectorAll('input, select, textarea')).slice(0, 100)) {
    if (n.validationMessage && !n.validity.valid) {
      const t = trim(n.validationMessage, 200);
      if (t && !validation.includes(t)) validation.push(t);
    }
  }

  return {
    url: location.href,
    title: document.title,
    text: trim(document.body ? document.body.innerText : '', config.maxText),
    elements: elements,
    validation_messages: validation,
  };
}
"""
