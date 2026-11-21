"""只读头像路由（开发态方案）：GET app/avatars/<path>。

白名单前缀限定在仓库内的头像资产目录，防目录穿越；.webp 为主。生产正式方案：
头像托管对象存储/CDN，avatar_ref 存完整 URL（见集成回复）。
"""
import mimetypes
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse

from .. import common

PREFIXES = ('digital-employees/assets/', 'assets/people/', 'assets/avatars/')
mimetypes.add_type('image/webp', '.webp')


def serve_avatar(request, ctx, path):
    common.query(request)
    root = Path(settings.BASE_DIR).resolve()
    file = (root / path).resolve()
    try:
        relative = file.relative_to(root).as_posix()
    except ValueError:
        relative = ''
    if not relative.startswith(PREFIXES) or not file.is_file():
        raise ValueError('NOT_FOUND')
    return HttpResponse(file.read_bytes(),
                        content_type=mimetypes.guess_type(file.name)[0] or 'application/octet-stream')


ROUTES = [('GET', '^avatars/(?P<path>.+)$', serve_avatar)]
