#!/usr/bin/env python3
"""Build the standalone WorldPilot HTML using only Python's standard library."""
from pathlib import Path
import base64
import json
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
    "brand/sage-logo-website.svg": "image/svg+xml",
    "brand/sage-logo-product.svg": "image/svg+xml",
}
SITE_URL = 'https://sagegogo.com/'
# Indexed launch pages; pricing, download and account routes stay out of search results.
INDEXED_PAGES = ('index', 'team', 'enterprise', 'docs', 'privacy', 'terms')
LEGAL_ENTITY = '深圳市众妙之门科技有限公司'
ICP_LICENSE = '粤ICP备2026173751号'

def legal_line(prefix='', english=False):
    suffix = '-en' if english else ''
    privacy, terms = ('Privacy Policy', 'Terms of Service') if english else ('隐私政策', '服务条款')
    return (f'<p class="sage-legal"><a href="{prefix}privacy{suffix}.html">{privacy}</a><a href="{prefix}terms{suffix}.html">{terms}</a>'
            f'<span lang="zh-CN">© 2026 {LEGAL_ENTITY}</span>'
            f'<a href="https://beian.miit.gov.cn/" target="_blank" rel="noopener noreferrer" lang="zh-CN">{ICP_LICENSE}</a></p>')
SHARE_IMAGE = SITE_URL + 'assets/og/sage-share.jpg'

def read_text(relative_path):
    return (ROOT / relative_path).read_bytes().decode("utf-8")

def page_metadata(html, name, english=False):
    """Prepare canonical, indexing and share metadata for the launch site."""
    descriptions = {
        'index': ('Sage 是面向出海品牌的 AI 团队：53 位专业数字员工分头研究、交叉复核，交付有出处的经营结论，执行经你批准。', 'Sage is the AI team for global commerce brands: 53 specialist digital employees research, cross-check and deliver sourced business conclusions, and nothing runs without your approval.'),
        'team': ('认识 Sage 的 53 位数字员工：8 个专业域的岗位职责、协作方法与复核机制。', 'Meet the 53 Sage digital employees: roles across eight domains, how they collaborate and how their work is reviewed.'),
        'enterprise': ('了解 Sage 标准产品与专项实施的合作方式、阶段成果及验收安排。', 'Explore Sage product procurement, enterprise implementation, deliverables and acceptance stages.'),
        'docs': ('阅读 Sage 产品规则、场景说明、账户及订阅帮助文档。', 'Read Sage product rules, workflow guides, account documentation and subscription help.'),
        'pricing': ('查看 Sage 个人与企业订阅方案、Sage Credits 与计费规则。', 'Review Sage personal and business plans, Sage Credits and billing rules.'),
        'download': ('Sage App 即将上线。了解发布计划，或先在网页中体验 Sage。', 'The Sage app is coming soon. Follow the release or try Sage on the web first.'),
        'trial': ('免费体验 Sage 的 VOC 机会洞察与 Shopify 店铺诊断。', 'Try Sage VOC opportunity insights and Shopify store diagnostics for free.'),
        'auth': ('使用邮箱或手机号验证码，登录或注册 Sage 账户。', 'Sign in or register for a Sage account with an email or phone verification code.'),
        'notifications': ('查看 Sage 付款、订阅、退款、安全与成员变动通知。仅展示你有权查看的记录。', 'View Sage payment, subscription, refund, security and membership updates. Only authorized records are shown.'),
        'profile': ('管理 Sage 个人设置。账户资料仅在验证身份后展示。', 'Manage Sage personal settings. Account data requires identity verification.'),
        'billing': ('管理 Sage 账户、订阅与账单。账户资料仅在验证身份后展示。', 'Manage Sage accounts, subscriptions and billing. Account data requires identity verification.'),
        'checkout': ('确认购买前，核对 Sage 订阅方案、价格与订单信息。', 'Check your Sage plan, price and order details before you confirm a purchase.'),
        'product': ('Sage 产品方案预览。具体开放能力以产品内说明为准。', 'Sage product design preview. Available capabilities follow product disclosures.'),
        'privacy': ('Sage 隐私政策：我们收集哪些个人信息、如何使用与保护，以及你如何行使个人信息权利。', 'Sage Privacy Policy: what personal information we collect, how we use and protect it, and how to exercise your rights.'),
        'terms': ('Sage 服务条款：账户、使用规范、AI 生成内容、付费服务与责任等使用 Sage 的约定。', 'Sage Terms of Service: accounts, acceptable use, AI-generated content, paid services and liability.'),
    }
    description = descriptions[name][int(english)]
    title = re.search(r'<title>(.*?)</title>', html, re.S)[1]
    chinese_url, english_url = page_urls(name)
    url = english_url if english else chinese_url
    public = name in {'index', 'team', 'enterprise', 'docs', 'pricing', 'download', 'privacy', 'terms'}
    robots = 'index,follow' if name in INDEXED_PAGES else f'noindex,{"follow" if public else "nofollow"}'
    tags = [f'<meta name="description" content="{escape(description, quote=True)}">',
            f'<meta name="robots" content="{robots}">',
            f'<link rel="canonical" href="{url}">']
    if name != 'product':
        tags += [f'<link rel="alternate" hreflang="zh-CN" href="{chinese_url}">',
                 f'<link rel="alternate" hreflang="en" href="{english_url}">',
                 f'<link rel="alternate" hreflang="x-default" href="{chinese_url}">']
    if public:
        image = SHARE_IMAGE
        alt = 'Sage: your AI team for global commerce' if english else 'Sage：你的出海 AI 团队'
        for property_name, value in [('og:type','website'), ('og:site_name','Sage'), ('og:title',title), ('og:description',description), ('og:url',url), ('og:locale','en_US' if english else 'zh_CN'), ('og:image',image), ('og:image:alt',alt)]:
            tags.append(f'<meta property="{property_name}" content="{escape(value, quote=True)}">')
        for property_name, value in [('twitter:card','summary_large_image'), ('twitter:title',title), ('twitter:description',description), ('twitter:image',image), ('twitter:image:alt',alt)]:
            tags.append(f'<meta name="{property_name}" content="{escape(value, quote=True)}">')
    html = re.sub(r'<meta name="(?:description|robots)" content="[^"]*">', '', html)
    return html.replace('</head>', '\n' + '\n'.join(tags) + '\n</head>', 1)

def page_urls(name):
    """Public Chinese and English URLs; the Chinese homepage is the site root."""
    if name == 'index':
        return SITE_URL, SITE_URL + 'index-en.html'
    return SITE_URL + f'pages/{name}.html', SITE_URL + f'pages/{name}-en.html'

def add_legal_line(html, prefix='', english=False):
    """Show legal links, the operator and ICP filing in every static footer, after localization."""
    if 'class="sage-legal"' in html or '</footer>' not in html:
        return html
    return html.replace('</footer>', legal_line(prefix, english) + '</footer>', 1)

def asset_version(relative_path):
    return sha256((ROOT / relative_path).read_bytes()).hexdigest()[:12]

def version_homepage_assets(html):
    """Load portraits, the globe texture and the App demo as cacheable versioned files."""
    def versioned(match):
        path = match[1]
        data = (ROOT / path).read_bytes()
        if path.endswith('.webp') and (data[:4] != b'RIFF' or data[8:12] != b'WEBP'):
            raise ValueError(f"Invalid WebP asset: {path}")
        return f'{path}?v={asset_version(path)}'
    return re.sub(r'(assets/(?:(?:people/[\w-]+|earth-texture)\.webp|video/[\w-]+\.(?:mp4|jpg)))(?!\?v=)', versioned, html)

def team_domains():
    """Render the homepage domain overview from the approved employee catalog."""
    source = read_text('src/data/sage-catalog.js')
    catalog = json.loads(source.split('=', 1)[1].strip().rstrip(';'))
    domains = {}
    for person in catalog['people']:
        domains.setdefault(person['domain'], []).append(person)
    cards = []
    for domain, people in domains.items():
        faces = ''.join(f'<img src="{escape(p["avatar"])}" width="44" height="44" alt="{escape(p["name"])}" loading="lazy" decoding="async">' for p in people[:5])
        more = f'<span class="team-domain-more">+{len(people) - 5}</span>' if len(people) > 5 else ''
        roles = ' · '.join(p['role'] for p in people[:2])
        cards.append(f'<li><a href="pages/team.html#organization"><span class="team-domain-faces">{faces}{more}</span>'
                     f'<strong>{escape(domain)}</strong><span class="team-domain-count">{len(people)} 个专业岗位</span>'
                     f'<span class="team-domain-roles">{escape(roles)}</span></a></li>')
    if sum(len(people) for people in domains.values()) != 50 or len(domains) != 8:
        raise ValueError('Expected 50 specialist roles across 8 domains')
    return (f'<ul class="team-domains">{"".join(cards)}</ul>'
            '<a class="team-domains-link" href="pages/team.html#organization">查看全部岗位与职责 <span aria-hidden="true">→</span></a>')

def write_search_files(directory):
    urls = [url for name in INDEXED_PAGES for url in page_urls(name)]
    (directory / 'robots.txt').write_text('User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /sales/\n'
                                          f'Sitemap: {SITE_URL}sitemap.xml\n', encoding='utf-8')
    entries = ''.join(f'<url><loc>{url}</loc></url>' for url in urls)
    (directory / 'sitemap.xml').write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{entries}</urlset>\n', encoding='utf-8')

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
        # Content-hash local images too, so the gateway can cache every versioned file for a year.
        html = re.sub(r'(src=")((?:\.\./)?(?:icons|assets)/[\w/.-]+\.(?:webp|png|jpe?g|svg))(?:\?v=[a-f0-9]+)?(")',
                      lambda m: m[1] + m[2] + '?v=' + sha256((path.parent / m[2]).read_bytes()).hexdigest()[:12] + m[3]
                      if (path.parent / m[2]).is_file() else m[0], html)
        path.write_text(add_legal_line(page_metadata(html, name, english), english=english), encoding='utf-8')

def build():
    html = read_text("src/index.template.html")
    for token, relative_path in BLOCKS.items():
        if html.count(token) != 1:
            raise ValueError(f"Expected exactly one {token} in the template")
        content = read_text(relative_path)
        if relative_path == 'src/app.js':
            content = read_text('src/pages/site-shell.js') + '\n' + content
        html = html.replace(token, content)
    if html.count('{{SAGE_TEAM_DOMAINS}}') != 1:
        raise ValueError('Expected exactly one {{SAGE_TEAM_DOMAINS}} in the template')
    html = version_homepage_assets(html.replace('{{SAGE_TEAM_DOMAINS}}', team_domains()))
    html = html.replace("</style>", read_text("src/sage-content.css") + "</style>")
    for filename, mime_type in ASSETS.items():
        token = "{{WORLD_PILOT_ASSET:" + filename + "}}"
        expected = 2 if filename == "brand/sage-logo-product.svg" else 1
        if html.count(token) != expected:
            raise ValueError(f"Expected {expected} asset references: {filename}")
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
    destination.write_bytes(add_legal_line(page_metadata(language_dropdown(html, 'index'), 'index'), 'pages/').encode("utf-8"))
    if not draft:
        (ROOT / 'index-en.html').write_text(add_legal_line(page_metadata(language_dropdown(english_homepage(html), 'index', True), 'index', True), 'pages/', True), encoding='utf-8')
        write_search_files(ROOT)
    if '--release' in sys.argv:
        output = ROOT / 'dist'
        output.mkdir(exist_ok=True)
        for entry in ('index.html', 'index-en.html'):
            shutil.copy2(ROOT / entry, output / entry)
        shutil.copytree(ROOT / 'pages', output / 'pages', dirs_exist_ok=True)
        shutil.copytree(ROOT / 'assets/people', output / 'assets/people', dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns('*.json'))
        shutil.copy2(ROOT / 'assets/favicon.svg', output / 'assets/favicon.svg')
        shutil.copy2(ROOT / 'assets/earth-texture.webp', output / 'assets/earth-texture.webp')
        shutil.copytree(ROOT / 'assets/og', output / 'assets/og', dirs_exist_ok=True)
        shutil.copytree(ROOT / 'assets/video', output / 'assets/video', dirs_exist_ok=True)
        for entry in ('robots.txt', 'sitemap.xml'):
            shutil.copy2(ROOT / entry, output / entry)
        shutil.copytree(ROOT / 'assets/brand', output / 'assets/brand', dirs_exist_ok=True)
    print(f"Built {destination.name} ({destination.stat().st_size:,} bytes)")

if __name__ == "__main__":
    build()
