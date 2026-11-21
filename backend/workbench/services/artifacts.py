"""Artifact access, version content storage (Q-D1) and version diffs (Q-B-21)."""
import difflib
import json
import re
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage, storages
from django.db.models import Max, Q

from ..ids import new_public_id
from ..models import ArtifactVersion
from . import workspaces as ws

STORE_SCHEME = 'store:'
TEXT_KINDS = ('document', 'webpage')
TEXT_BLOCKS = ('heading', 'paragraph', 'list_item', 'quote')
_CLOSERS = '。！？!?；;，'
# Full-width commas also split so a changed clause does not mark the whole sentence as replaced.
_SENTENCE = re.compile(r'[^。！？!?；;，\n]+[。！？!?；;，]?|[。！？!?；;，]')


# Content storage --------------------------------------------------------------
# Q-D1 A: binaries live in object storage. Deployments point the 'artifacts'
# alias in settings.STORAGES at their object-storage backend; without it the
# content goes to a local directory under tmp/ (never MEDIA_ROOT, which serves avatars).

def content_storage():
    if 'artifacts' in settings.STORAGES:
        return storages['artifacts']
    return FileSystemStorage(location=Path(settings.BASE_DIR) / 'tmp' / 'artifact-content')


def save_content(artifact, number, body):
    data = json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')
    name = content_storage().save('artifacts/%s/v%d-%s.json' % (artifact.public_id, number, new_public_id()),
                                  ContentFile(data))
    return STORE_SCHEME + name, len(data)


def read_content(version):
    ref = version.content_ref or ''
    if not ref.startswith(STORE_SCHEME):
        raise ValueError('SOURCE_UNAVAILABLE')
    try:
        with content_storage().open(ref[len(STORE_SCHEME):], 'rb') as handle:
            return json.loads(handle.read().decode('utf-8'))
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        raise ValueError('SOURCE_UNAVAILABLE') from exc


def create_version(artifact, body, *, state='draft', label='', author_user=None, author_expert=None,
                   change_summary=None, restored_from=None, content_ref=None, size_bytes=None,
                   client_request_id=None):
    """Append a version (numbers start at 1, Q-O3 A) and make it current."""
    number = (artifact.versions.aggregate(top=Max('number'))['top'] or 0) + 1
    if content_ref is None:
        content_ref, size_bytes = save_content(artifact, number, body)
    version = ArtifactVersion.objects.create(
        account=artifact.account, artifact=artifact, number=number, state=state, label=label,
        author_user=author_user, author_expert=author_expert, change_summary=change_summary,
        restored_from=restored_from, content_ref=content_ref, size_bytes=size_bytes,
        client_request_id=client_request_id)
    artifact.current_version = version
    artifact.save(update_fields=['current_version', 'updated_at'])
    return version


# Access -----------------------------------------------------------------------

def visible_artifacts_q(ctx):
    workspace_ids = ws.member_rows(ctx).exclude(role='guest').values('workspace_id')
    return (Q(workspace_id__in=workspace_ids) | Q(created_by_user=ctx.user)
            | Q(source_task__in=ws.visible_tasks(ctx).values('pk')))


def artifact_access(ctx, artifact):
    if artifact is None or artifact.account_id != ctx.account.pk:
        return None
    level = None
    if artifact.workspace_id:
        member = ws.my_member(ctx, artifact.workspace)
        if member and member.role in ws.EDIT_ROLES:
            level = 'edit'
        elif member and member.role == 'viewer':
            level = 'view'
    if artifact.created_by_user_id == ctx.user.pk:
        level = 'edit'
    task = artifact.source_task
    if task is not None:
        if ws.is_owner_or_creator(ctx, task):
            level = 'edit'
        elif ws.task_access(ctx, task):
            # Task participants only read; changing needs workspace editor or task owner (Q-P8 A).
            level = ws.best(level, 'view')
    return level


def is_mine(ctx, artifact):
    return artifact.created_by_user_id == ctx.user.pk or (
        artifact.source_task is not None and artifact.source_task.creator.user_id == ctx.user.pk)


# Text and diff ------------------------------------------------------------------

def is_text(artifact, body):
    return artifact.kind in TEXT_KINDS and isinstance(body, dict) and body.get('format') == 'blocks'


def sentences(text):
    return [part.strip() for part in _SENTENCE.findall(text or '') if part.strip()]


def text_units(body):
    units = []
    for block in body.get('blocks', []):
        kind = block.get('type')
        if kind in TEXT_BLOCKS:
            units.extend(sentences(block.get('text', '')))
        elif kind == 'table':
            units.extend(' | '.join(str(cell) for cell in row) for row in block.get('rows', []))
    return units


def _join(units):
    text = ''
    for unit in units:
        # Headings and table rows carry no closing punctuation; keep them on separate lines.
        text += unit if not text or text[-1] in _CLOSERS else '\n' + unit
    return text


def diff_hunks(base_body, target_body):
    """Sentence-level hunks; equal runs become context (Q-B-21 A).

    Units are compared without their closing punctuation, so a sentence that only gains a following clause
    ("…€39–69。" -> "…€39–69，其中…") shows as context plus an insertion instead of a full replacement.
    """
    old, new = text_units(base_body), text_units(target_body)
    old_keys, new_keys = [unit.rstrip(_CLOSERS) for unit in old], [unit.rstrip(_CLOSERS) for unit in new]
    hunks = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old_keys, b=new_keys, autojunk=False).get_opcodes():
        if tag == 'equal':
            hunks.extend({'context': unit} for unit in new[j1:j2])
            continue
        hunk = {}
        if i2 > i1:
            hunk['deleted'] = _join(old[i1:i2])
        if j2 > j1:
            hunk['inserted'] = _join(new[j1:j2])
        hunks.append(hunk)
    return hunks


def replace_text(body, original, suggested):
    """Return (new_body, replaced?) with every occurrence of original rewritten."""
    if not original:
        return body, False
    body = json.loads(json.dumps(body))
    replaced = False
    for block in body.get('blocks', []):
        if block.get('type') in TEXT_BLOCKS and original in block.get('text', ''):
            block['text'] = block['text'].replace(original, suggested)
            replaced = True
        elif block.get('type') == 'table':
            for row in block.get('rows', []):
                for index, cell in enumerate(row):
                    if isinstance(cell, str) and original in cell:
                        row[index] = cell.replace(original, suggested)
                        replaced = True
    return body, replaced


def render_markdown(title, body):
    lines = ['# ' + title, '']
    for block in body.get('blocks', []) if isinstance(body, dict) else []:
        kind = block.get('type')
        if kind == 'heading':
            lines += ['#' * (int(block.get('level', 2)) + 1) + ' ' + block.get('text', ''), '']
        elif kind == 'list_item':
            lines.append('- ' + block.get('text', ''))
        elif kind == 'table':
            columns = block.get('columns', [])
            lines += ['| ' + ' | '.join(columns) + ' |', '|' + '---|' * len(columns)]
            lines += ['| ' + ' | '.join(str(cell) for cell in row) + ' |' for row in block.get('rows', [])]
            lines.append('')
        elif kind in TEXT_BLOCKS:
            lines += [block.get('text', ''), '']
    return '\n'.join(lines).rstrip() + '\n'
