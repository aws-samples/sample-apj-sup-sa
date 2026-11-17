"""Guard against XSS in frontend/index.html (static check, no browser).

Rules:
* Every ${...} inside a template literal that contains markup must be
  escapeHtml(...), a nested template (checked recursively), or one of a few
  local values listed in SAFE_EXPRESSIONS (computed in the page, never from
  the API or Connect).
* No insertAdjacentHTML, outerHTML or document.write.
* showStatus writes with textContent.
* No string literal containing markup is concatenated with a non literal
  using '+' (use a template literal so the rule above applies).
"""
import pathlib
import re

HTML = (pathlib.Path(__file__).resolve().parent.parent / 'frontend' / 'index.html').read_text()
SCRIPT = '\n'.join(re.findall(r'<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>', HTML))

# Values computed locally from constants or counters; never API or Connect data.
SAFE_EXPRESSIONS = {
    'i',                # loop index used in element ids
    'pct',              # Math.round of two local counts
    'statusClass',      # one of two CSS class constants
    'statusIcon',       # one of three emoji constants
    'detail',           # HTML built from escapeHtml() calls just above
    'stepRows',         # migration report rows built from escapeHtml() calls just above
    'stepTable',        # constant markup around stepRows
    'failedTable',      # constant markup around escapeHtml() calls
    'secretWarning',    # constant markup around one escapeHtml() call, or ''
    'scope',            # constant markup around one escapeHtml() call, or ''
    'cat.label',        # constant labels in the categories array
}


def parse_template(src, start):
    """Parse a template literal starting at src[start] == '`'. Return (end, text, expressions)."""
    i, text, exprs = start + 1, [], []
    while i < len(src):
        c = src[i]
        if c == '\\':
            text.append(src[i:i + 2])
            i += 2
        elif c == '`':
            return i + 1, ''.join(text), exprs
        elif src.startswith('${', i):
            j, depth = i + 2, 1
            while depth:
                ch = src[j]
                if ch == '`':
                    j, _, _ = parse_template(src, j)
                    continue
                if ch in '\'"':
                    j = skip_string(src, j)
                    continue
                depth += {'{': 1, '}': -1}.get(ch, 0)
                j += 1
            exprs.append(src[i + 2:j - 1].strip())
            i = j
        else:
            text.append(c)
            i += 1
    raise ValueError('unterminated template literal')


def skip_string(src, i):
    quote, i = src[i], i + 1
    while src[i] != quote:
        i += 2 if src[i] == '\\' else 1
    return i + 1


def skip_regex(src, i):
    """Skip a regex literal starting at src[i] == '/'."""
    i, in_class = i + 1, False
    while True:
        ch = src[i]
        if ch == '\\':
            i += 2
            continue
        if ch == '[':
            in_class = True
        elif ch == ']':
            in_class = False
        elif ch == '/' and not in_class:
            i += 1
            while i < len(src) and src[i].isalpha():
                i += 1
            return i
        i += 1


def regex_can_start(src, i):
    j = i - 1
    while j >= 0 and src[j] in ' \t\n':
        j -= 1
    return j < 0 or src[j] in '(,=:[!&|?{};+' or src[j - 5:j + 1] == 'return'


def templates(src):
    """Yield (text, expressions) for every template literal, including nested ones."""
    i = 0
    while i < len(src):
        c = src[i]
        if src.startswith('//', i):
            i = src.index('\n', i)
        elif src.startswith('/*', i):
            i = src.index('*/', i) + 2
        elif c == '/' and regex_can_start(src, i):
            i = skip_regex(src, i)
        elif c in '\'"':
            i = skip_string(src, i)
        elif c == '`':
            end, text, exprs = parse_template(src, i)
            yield text, exprs
            for expr in exprs:
                yield from templates(expr)
            i = end
        else:
            i += 1


def is_escaped(expr):
    if not expr.startswith('escapeHtml(') or not expr.endswith(')'):
        return False
    depth = 0
    for k, ch in enumerate(expr[len('escapeHtml'):], start=len('escapeHtml')):
        depth += {'(': 1, ')': -1}.get(ch, 0)
        if depth == 0:
            return k == len(expr) - 1
    return False


def unescaped_interpolations(src=SCRIPT):
    offenders = []
    for text, exprs in templates(src):
        if '<' not in text:
            continue
        for expr in exprs:
            if is_escaped(expr) or expr in SAFE_EXPRESSIONS or '`' in expr:
                continue
            offenders.append(expr)
    return offenders


def test_parser_finds_the_markup_templates():
    assert sum(1 for text, _ in templates(SCRIPT) if '<' in text) > 20


def test_guard_flags_unescaped_and_accepts_escaped_samples():
    bad = "el.innerHTML = `<b>${r.name}</b>` + x; const re = /[\"']/g; y.innerHTML = `<i>${list.map(v => `<u>${v}</u>`)}</i>`;"
    assert unescaped_interpolations(bad) == ['r.name', 'v']
    good = "el.innerHTML = `<b>${escapeHtml(r.name)}</b>`; url = `https://${host}/x`;"
    assert unescaped_interpolations(good) == []


def test_every_interpolation_in_markup_is_escaped():
    assert unescaped_interpolations() == []


def test_no_other_html_sinks():
    for sink in ('insertAdjacentHTML', 'outerHTML', 'document.write'):
        assert sink not in SCRIPT, sink


def test_show_status_uses_text_content():
    body = re.search(r'function showStatus\(message, type\) \{([\s\S]*?)\n        \}', SCRIPT).group(1)
    assert 'textContent = message' in body and 'innerHTML' not in body


def test_no_markup_string_concatenated_with_data():
    concat = re.findall(r"""(['"])[^'"\n]*<[^'"\n]*\1\s*\+\s*(?!['"`])\w|\w\s*\+\s*(['"])[^'"\n]*<""", SCRIPT)
    assert concat == []
