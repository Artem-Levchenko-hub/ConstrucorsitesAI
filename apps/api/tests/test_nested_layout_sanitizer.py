"""Deterministic guard: a NESTED Next.js layout must never render <html>/<body>
(the root layout owns them). A duplicate breaks React hydration, which kills the
realtime client — messages stop arriving. A weak model adds <html><body> to the
restyled (app)/layout.tsx even when the prompt forbids it (observed live twice),
so the engine strips it on write. A correct layout passes through byte-identical.
"""

from __future__ import annotations

from omnia_api.services import agent_builder as ab


def test_strips_html_body_from_nested_layout():
    src = (
        'export default function AppLayout({ children }: { children: React.ReactNode }) {\n'
        '  return (\n'
        '    <html lang="ru">\n'
        '      <body className="min-h-screen bg-zinc-950 text-zinc-50">\n'
        '        <header>Capital Messenger</header>\n'
        '        <main>{children}</main>\n'
        '      </body>\n'
        '    </html>\n'
        '  );\n'
        '}\n'
    )
    out = ab._sanitize_nested_layout("src/app/(app)/layout.tsx", src)
    assert "<html" not in out.lower()
    assert "</html>" not in out.lower()
    assert "<body" not in out.lower()
    assert "</body>" not in out.lower()
    # the body's styling is preserved on a <div>, and the shell survives
    assert '<div className="min-h-screen bg-zinc-950 text-zinc-50">' in out
    assert "<header>Capital Messenger</header>" in out
    assert "{children}" in out


def test_strips_head_block():
    src = (
        'export default function L({ children }) {\n'
        '  return (<html><head><title>x</title></head><body>{children}</body></html>);\n'
        '}\n'
    )
    out = ab._sanitize_nested_layout("src/app/(app)/layout.tsx", src)
    assert "<head" not in out.lower()
    assert "<title>" not in out.lower()
    assert "{children}" in out


def test_root_layout_is_untouched():
    # The ROOT layout legitimately owns <html>/<body> — never strip it.
    src = '<html lang="ru"><body>{children}</body></html>'
    assert ab._sanitize_nested_layout("src/app/layout.tsx", src) == src


def test_clean_nested_layout_is_byte_identical():
    src = (
        'export default function AppLayout({ children }) {\n'
        '  return (<div className="shell"><nav/>{children}</div>);\n'
        '}\n'
    )
    assert ab._sanitize_nested_layout("src/app/(app)/layout.tsx", src) == src


def test_non_layout_file_is_untouched():
    # A page that renders an <html> string in a code sample must not be mangled.
    src = 'const sample = "<html><body>hi</body></html>";'
    assert ab._sanitize_nested_layout("src/app/(app)/chat/page.tsx", src) == src


def test_is_nested_layout_matrix():
    assert ab._is_nested_layout("src/app/(app)/layout.tsx") is True
    assert ab._is_nested_layout("src/app/(marketing)/dash/layout.tsx") is True
    assert ab._is_nested_layout("src/app/layout.tsx") is False
    assert ab._is_nested_layout("src/app/(app)/page.tsx") is False
