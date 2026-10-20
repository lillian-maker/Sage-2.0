#!/usr/bin/env python3
"""Build the standalone WorldPilot HTML using only Python's standard library."""
from pathlib import Path
import base64
import re
import shutil
import sys
from hashlib import sha256
from html import escape
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

def page_metadata(html, name, english=False):
    """Prepare canonical/share metadata without opening preview pages to indexing."""
    descriptions = {
        'index': ('Sage 产品预览：了解出海 AI 团队、经营场景与经人授权的协作方式。', 'Sage product preview: explore AI teams, global commerce workflows and human-authorized collaboration.'),
        'team': ('了解 Sage AI 团队的专业岗位、协作方法与复核职责。具体开放能力以产品内说明为准。', 'Explore Sage AI team roles, collaboration methods and review responsibilities. Available capabilities follow product disclosures.'),
        'enterprise': ('了解 Sage 标准产品与专项实施的合作方式、阶段成果及验收安排。', 'Explore Sage product procurement, enterprise implementation, deliverables and acceptance stages.'),
        'docs': ('阅读 Sage 产品规则、场景说明、账户及订阅帮助文档。', 'Read Sage product rules, workflow guides, account documentation and subscription help.'),
        'pricing': ('查看 Sage 订阅方案与计费规则。当前预览未开放收款。', 'Review Sage subscription plans and billing rules. Payments are not available in this preview.'),
        'download': ('查看 Sage 客户端发布状态、系统要求及安装说明。安装包尚未发布。', 'Check Sage client release status, system requirements and installation guidance. The installer has not been released.'),
        'trial': ('Sage 本地交互示例：真实分析服务尚未接入。', 'Sage local interactive examples. Live analysis services are not connected.'),
        'auth': ('登录或注册 Sage 账户。真实认证服务需完成接入验收。', 'Sign in or register for a Sage account. Live authentication requires integration acceptance.'),
        'notifications': ('查看 Sage 付款、订阅、退款、安全与成员变动通知。仅展示你有权查看的记录。', 'View Sage payment, subscription, refund, security and membership updates. Only authorized records are shown.'),
        'profile': ('管理 Sage 个人设置。账户资料仅在验证身份后展示。', 'Manage Sage personal settings. Account data requires identity verification.'),
        'billing': ('管理 Sage 账户、订阅与账单。账户资料仅在验证身份后展示。', 'Manage Sage accounts, subscriptions and billing. Account data requires identity verification.'),
        'checkout': ('核对 Sage 方案与订单。当前预览未开放收款。', 'Review Sage plans and orders. Payments are not available in this preview.'),
        'product': ('Sage 产品方案预览。具体开放能力以产品内说明为准。', 'Sage product design preview. Available capabilities follow product disclosures.'),
    }
    description = descriptions[name][int(english)]
    title = re.search(r'<title>(.*?)</title>', html, re.S)[1]
    prefix = '' if name == 'index' else 'pages/'
    base_url = 'https://sagegogo.com/' + prefix + name
    url = base_url + ('-en' if english else '') + '.html'
    public = name in {'index', 'team', 'enterprise', 'docs', 'pricing', 'download'}
    # --release is also used for the closed candidate; indexing needs separate approval.
    tags = [f'<meta name="description" content="{escape(description, quote=True)}">',
            f'<meta name="robots" content="noindex,{"follow" if public else "nofollow"}">',
            f'<link rel="canonical" href="{url}">']
    if name != 'product':
        tags += [f'<link rel="alternate" hreflang="zh-CN" href="{base_url}.html">',
                 f'<link rel="alternate" hreflang="en" href="{base_url}-en.html">']
    if public:
        image = 'https://sagegogo.com/pages/icons/team-human-ai-workspace.png'
        alt = 'Sage business collaboration illustration' if english else 'Sage 业务协作示意图'
        for property_name, value in [('og:type','website'), ('og:site_name','Sage'), ('og:title',title), ('og:description',description), ('og:url',url), ('og:locale','en_US' if english else 'zh_CN'), ('og:image',image), ('og:image:alt',alt)]:
            tags.append(f'<meta property="{property_name}" content="{escape(value, quote=True)}">')
        for property_name, value in [('twitter:card','summary_large_image'), ('twitter:title',title), ('twitter:description',description), ('twitter:image',image), ('twitter:image:alt',alt)]:
            tags.append(f'<meta name="{property_name}" content="{escape(value, quote=True)}">')
    html = re.sub(r'<meta name="(?:description|robots)" content="[^"]*">', '', html)
    return html.replace('</head>', '\n' + '\n'.join(tags) + '\n</head>', 1)

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
        path.write_text(page_metadata(html, name, english), encoding='utf-8')

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
    destination.write_bytes(page_metadata(language_dropdown(html, 'index'), 'index').encode("utf-8"))
    if not draft:
        (ROOT / 'index-en.html').write_text(page_metadata(language_dropdown(english_homepage(html), 'index', True), 'index', True), encoding='utf-8')
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
