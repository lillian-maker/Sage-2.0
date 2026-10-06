#!/usr/bin/env python3
"""Build the standalone WorldPilot HTML using only Python's standard library."""
from pathlib import Path
import base64
import re
import shutil
import sys
from hashlib import sha256
sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from localize_en import english_homepage, build_localized_pages, language_dropdown

ROOT = Path(__file__).resolve().parent
BLOCKS = {
    "{{WORLD_PILOT_STYLES}}": "src/styles.css",
    "{{WORLD_PILOT_BUSINESS_DATA}}": "src/data/sage-catalog.js",
    "{{WORLD_PILOT_APP}}": "src/app.js",
}
ASSETS = {
    "favicon.svg": "image/svg+xml",
    "earth-texture.png": "image/png",
}

def read_text(relative_path):
    return (ROOT / relative_path).read_bytes().decode("utf-8")

def unify_page_shells():
    """Keep the retained English content, but rebuild its shared navigation styles."""
    fragment = read_text('src/language-switch.html')
    version = sha256(read_text('src/pages/content-pages.css').encode()).hexdigest()[:12]
    shell_version = sha256(read_text('src/pages/site-shell.js').encode()).hexdigest()[:12]
    for path in (ROOT / 'pages').glob('*.html'):
        html = path.read_text(encoding='utf-8')
        english = path.stem.endswith('-en')
        name = path.stem.removesuffix('-en')
        if 'content-pages.css' not in html:
            html = html.replace('</head>', f'<link rel="stylesheet" href="content-pages.css?v={version}"></head>')
        else:
            html = re.sub(r'href="content-pages\.css(?:\?v=[a-f0-9]+)?"', f'href="content-pages.css?v={version}"', html)
        if not re.search(r'src="site-shell(?:-en)?\.js', html):
            html = html.replace('</head>', f'<script src="site-shell.js?v={shell_version}" defer></script></head>')
        # Replace the old embedded switch, rather than accumulating scripts across builds.
        html = re.sub(r'<style>\s*\.sage-language\{.*?</style>', '', html, flags=re.S)
        html = re.sub(r'<script>\s*\(\(\)=>\{\s*(?:function initialize\(\)\{\s*let|const) root=document\.querySelector\(\'\.sage-language\'\).*?</script>', '', html, flags=re.S)
        if name != 'product':
            html = html.replace('</body>', fragment.replace('${name}', name) + '</body>')
        else:
            html = re.sub(r'<a class="page-language"[^>]*>.*?</a>', '', html)
        # Version every local stylesheet/script, including runtime account pages.
        # Rebuilds must not show new HTML with cached, unversioned old UI code.
        html = re.sub(r'((?:src|href)=")([\w-]+\.(?:css|js))(?:\?v=[a-f0-9]+)?(")',
                      lambda m: m[1] + m[2] + '?v=' + sha256((path.parent / m[2]).read_bytes()).hexdigest()[:12] + m[3]
                      if (path.parent / m[2]).is_file() else m[0], html)
        path.write_text(html, encoding='utf-8')

def build():
    html = read_text("src/index.template.html")
    for token, relative_path in BLOCKS.items():
        if html.count(token) != 1:
            raise ValueError(f"Expected exactly one {token} in the template")
        content = read_text(relative_path)
        if relative_path == 'src/app.js':
            content = read_text('src/pages/site-shell.js') + '\n' + content
        html = html.replace(token, content)
    # Keep portraits with the standalone homepage, including shared single-file copies.
    for portrait in sorted(set(re.findall(r'assets/people/AGT-\d+\.webp', html))):
        data = (ROOT / portrait).read_bytes()
        if data[:4] != b'RIFF' or data[8:12] != b'WEBP':
            raise ValueError(f"Invalid portrait: {portrait}")
        html = html.replace(portrait, 'data:image/webp;base64,' + base64.b64encode(data).decode('ascii'))
    html = html.replace("</style>", read_text("src/sage-content.css") + "</style>")
    for filename, mime_type in ASSETS.items():
        token = "{{WORLD_PILOT_ASSET:" + filename + "}}"
        if html.count(token) != 1:
            raise ValueError(f"Expected exactly one asset reference: {filename}")
        encoded = base64.b64encode((ROOT / "assets" / filename).read_bytes()).decode("ascii")
        html = html.replace(token, f"data:{mime_type};base64,{encoded}")
    if "{{WORLD_PILOT_" in html:
        raise ValueError("An unresolved build placeholder remains")
    shutil.copytree(ROOT / "src/pages", ROOT / "pages", dirs_exist_ok=True)
    build_localized_pages(ROOT)
    unify_page_shells()
    draft = "--visual-draft" in sys.argv
    # Approved visual direction is shared by the website and its review preview.
    html = html.replace("</style>", read_text("src/visual-draft.css") + read_text('src/pages/content-pages.css') + "</style>")
    if draft:
        html = html.replace("<title>Sage · 你的出海 AI 团队</title>", "<title>Sage · 商务科技配色视觉稿</title>")
    destination = ROOT / ("visual-draft.html" if draft else "index.html")
    destination.write_bytes(language_dropdown(html, 'index').encode("utf-8"))
    if not draft:
        (ROOT / 'index-en.html').write_text(language_dropdown(english_homepage(html), 'index', True), encoding='utf-8')
    if '--release' in sys.argv:
        output = ROOT / 'dist'
        output.mkdir(exist_ok=True)
        for entry in ('index.html', 'index-en.html'):
            shutil.copy2(ROOT / entry, output / entry)
        shutil.copytree(ROOT / 'pages', output / 'pages', dirs_exist_ok=True)
        shutil.copytree(ROOT / 'assets/people', output / 'assets/people', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('*.json'))
        shutil.copy2(ROOT / 'assets/favicon.svg', output / 'assets/favicon.svg')
    print(f"Built {destination.name} ({destination.stat().st_size:,} bytes)")

if __name__ == "__main__":
    build()
