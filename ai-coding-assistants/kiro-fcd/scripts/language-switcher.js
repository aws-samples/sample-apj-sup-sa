/* Local copy only: this runtime never calls a translation service. */
(() => {
  const dictionaries = JSON.parse(document.querySelector('script[type="kiro/i18n"]').textContent);
  const supported = new Set(['ko', 'en', 'ja']);
  const fragment = new URLSearchParams(location.hash.slice(1));
  const query = new URLSearchParams(location.search);
  let saved = 'ko';
  try { saved = localStorage.getItem('kiro-first-call-deck:language') || 'ko'; } catch (_) {}
  const requested = fragment.get('lang') || query.get('lang') || saved;
  const lang = supported.has(requested) ? requested : 'ko';
  const rawSlide = fragment.get('slide') || query.get('slide') || '1';
  const page = /^\d+$/.test(rawSlide) ? Number(rawSlide) : 1;
  const slide = Math.min(56, Math.max(0, Number.isSafeInteger(page) ? page - 1 : 0));
  const words = dictionaries[lang]?.text || {};
  const groups = dictionaries[lang]?.html || {};
  window.kiroLocale = { lang, slide };
  window.kiroTranslate = value => {
    const key = value.trim();
    if (!Object.prototype.hasOwnProperty.call(words, key)) return value;
    return value.slice(0, value.indexOf(key)) + words[key] + value.slice(value.indexOf(key) + key.length);
  };
  window.kiroLocalizeDocument = doc => {
    doc.documentElement.lang = lang;
    // Static, reviewed translations preserve the exact original inline elements.
    doc.querySelectorAll('p,li,h1,h2,h3,h4,td,div.dk-b,span.dk-b,div.dk-bul').forEach(el => {
      if (el.closest('[translate="no"],script,style')) return;
      const key = el.textContent.trim();
      if (Object.prototype.hasOwnProperty.call(groups, key)) el.innerHTML = groups[key];
    });
    const walker = doc.createTreeWalker(doc.documentElement, NodeFilter.SHOW_TEXT);
    for (let node; (node = walker.nextNode());) {
      if (!node.parentElement?.closest('script,style,[translate="no"]')) node.nodeValue = window.kiroTranslate(node.nodeValue);
    }
    doc.querySelectorAll('*').forEach(el => {
      if (el.closest('[translate="no"]')) return;
      for (const attr of ['aria-label', 'title', 'placeholder', 'alt', 'data-screen-label']) {
        if (el.hasAttribute(attr)) el.setAttribute(attr, window.kiroTranslate(el.getAttribute(attr)));
      }
    });
    doc.querySelectorAll('style').forEach(style => {
      style.textContent = style.textContent.replace('충돌 우선순위 +', window.kiroTranslate('충돌 우선순위 +'));
    });
    doc.querySelectorAll('iframe.wf-demo-frame').forEach(frame => {
      frame.setAttribute('src', frame.getAttribute('src') + '#lang=' + lang);
    });
    doc.querySelectorAll('a[download][href^="data:text/markdown"]').forEach(link => {
      const href = link.getAttribute('href');
      const [header, encoded] = href.split(',', 2);
      if (!header.includes(';base64')) return;
      try {
        const original = new TextDecoder().decode(Uint8Array.from(atob(encoded), c => c.charCodeAt(0)));
        const translated = window.kiroTranslate(original);
        if (translated !== original) link.setAttribute('href', 'data:text/markdown;charset=utf-8,' + encodeURIComponent(translated));
      } catch (_) { /* Keep the original downloadable example if it cannot be decoded. */ }
    });
    return doc;
  };
  window.kiroLocalizeMarkup = html => {
    const doc = window.kiroLocalizeDocument(new DOMParser().parseFromString(html, 'text/html'));
    return '<!doctype html>' + doc.documentElement.outerHTML;
  };
  window.kiroSwitchLanguage = (nextLang, index) => {
    if (!supported.has(nextLang) || nextLang === lang) return;
    try { localStorage.setItem('kiro-first-call-deck:language', nextLang); } catch (_) {}
    const params = new URLSearchParams({ lang: nextLang, slide: String(index + 1) });
    location.hash = params.toString();
    // Reuse the same self-contained document and its cached assets.
    location.reload();
  };
})();
