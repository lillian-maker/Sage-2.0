"""Artifacts, reviews, workspace rules and playbook sample data (Figma screens 16-19, 22, 23, 24, 41, 54-56)."""
import secrets
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ._clock import timezone

from backend.commerce.models import Reservation, SharedTask, TaskCostEvent
from backend.workbench.models import (Artifact, ArtifactShare, ArtifactUserState, ArtifactVersion, Automation,
                                      AutomationPermission, AutomationRun, AuthorizationRequest, Citation,
                                      ContentReview, ContentReviewIssue, Expert, KnowledgeBase, KnowledgeDocument,
                                      Playbook, PlaybookStep, Task, TaskDelivery, TaskMessage, TaskStep, TaskThread, Workspace,
                                      WorkspaceMember, WorkspaceRuleVersion)
from backend.workbench.services import artifacts as store

ORDER = 60

SPECIALTIES = {'望野': '市场竞争与机会研究', '安界': '合规与认证', '觅位': '关键词与类目', '译境': '本地化与翻译',
               '通途': '跨境物流', '天时': '节点与排期', '立言': '文案与表达', '绘影': '视觉设计', '守衡': '内控审计与独立复核',
               '衡价': '定价与利润测算'}

# Screen 17: v1 created four rules, v2 added the outreach rule, v3 raised the price floor.
RULE_TEXT = (
    '对外内容（Listing、广告、客服回复）一律用德语；内部报告用中文。',
    '价格建议不得低于 €39；低于成本价 +15% 的方案需标出风险并经我确认。',
    '合规相关结论必须引用《德国 EPR 法规摘要》或官方来源原文。',
    '竞品数据只使用近 90 天的数据，并注明数据日期。',
    '不主动联系达人或供应商；对外沟通只出草稿。',
    '交付报告时先给三行结论，再给依据。',
)
OLD_PRICE_RULE = '价格建议不得低于 €35；低于成本价 +15% 的方案需标出风险并经我确认。'
# author, rules, change note, minutes before the seed clock (screen 17 on 11月19日 10:53: 11月1日 / 11月3日 / 今天 10:02)
RULE_VERSIONS = (
    ('lillian', [RULE_TEXT[0], OLD_PRICE_RULE, RULE_TEXT[2], RULE_TEXT[3]], '创建 4 条规则', 18 * 24 * 60 + 30),
    ('chen', [RULE_TEXT[0], OLD_PRICE_RULE, RULE_TEXT[2], RULE_TEXT[3], RULE_TEXT[4], RULE_TEXT[5]],
     '新增：不主动联系达人或供应商', 16 * 24 * 60 + 30),
    ('lillian', list(RULE_TEXT), '价格下限 €35 → €39', 51),
)

REPORT_V2 = {'format': 'blocks', 'blocks': [
    {'type': 'heading', 'level': 1, 'text': '1. 结论摘要'},
    {'type': 'paragraph', 'text': '结论：建议进入德国站，首批备货 800 台，以 6 周销量验证后再补货。'
                                  '价格带：主力价格带为 €39–69，其中 €49–59 区间竞争最弱、评论增速最快。'},
    {'type': 'paragraph', 'text': 'Amazon.de 小家电类目前 20 名中，德国本土品牌占 6 席，价格带集中在 €39–69[1]。'
                                  '头部竞品差评集中在插头规格和德语说明书[2]。'},
    {'type': 'heading', 'level': 1, 'text': '2. 竞争格局'},
    {'type': 'table', 'columns': ['品牌来源', '前 20 名席位', '主要价格带'],
     'rows': [['德国本土品牌', '6 席', '€49–69'], ['中国出海品牌', '9 席', '€35–55'], ['其他国际品牌', '5 席', '€59–89']]},
    {'type': 'heading', 'level': 1, 'text': '3. 合规前置条件'},
    {'type': 'paragraph', 'text': '上架前必须取得 WEEE 注册号并完成 EPR 登记[3]。'},
]}
# Screen 56 counts "望野修改了 2 处" between v1 and v2: only the conclusion paragraph differs.
REPORT_V1 = {'format': 'blocks', 'blocks': [
    REPORT_V2['blocks'][0],
    {'type': 'paragraph', 'text': '结论：建议进入德国站，首批备货 1,500 台。价格带：主力价格带为 €39–69。'},
    *REPORT_V2['blocks'][2:],
]}
LISTING = {'format': 'blocks', 'blocks': [
    {'type': 'heading', 'level': 1, 'text': 'Luftreiniger X1 – tötet 99,9 % aller Viren'},
    {'type': 'list_item', 'text': 'Leiser Betrieb mit nur 25 dB im Schlafmodus.'},
    {'type': 'list_item', 'text': 'Du wirst die frische Luft lieben – leise und effizient.'},
]}


def doc(*paragraphs):
    return {'format': 'blocks', 'blocks': [{'type': 'paragraph', 'text': text} for text in paragraphs]}


def sheet(*rows):
    return {'format': 'table', 'columns': ['项目', '数值'], 'rows': [list(row) for row in rows]}


def slides(*titles):
    return {'format': 'slides', 'slides': [{'title': title} for title in titles]}


def page(title):
    return {'format': 'blocks', 'blocks': [{'type': 'heading', 'level': 1, 'text': title}]}


def image(caption):
    return {'format': 'image', 'caption': caption}


# title, kind, workspace, task, expert, versions [(body, state, label, minutes ago)], share, trashed days ago
# On the 10:53 seed clock the report outline lands at 09:55 (screen 55) and the current draft "10 分钟前", so the
# report leads the library ahead of the EPR checklist at "32 分钟前" (screen 54). Screen 55 dates the same draft
# 10:12; the library order wins.
ARTIFACTS = (
    ('德国站竞品格局报告', 'document', '德国市场进入', '德国站竞品格局调研', '望野',
     [(REPORT_V1, 'draft', '大纲', 58), (REPORT_V2, 'draft', '', 10)], 'workspace_members', None),
    ('竞品价格带.xlsx', 'spreadsheet', '德国市场进入', '德国站竞品格局调研', '衡价',
     [(sheet(('德国本土品牌', '€49–69'), ('中国出海品牌', '€35–55')), 'draft', '', 12)], 'workspace_members', None),
    ('EPR 合规清单', 'document', '德国市场进入', '德国站竞品格局调研', '安界',
     [(doc('上架前必须取得 WEEE 注册号并完成 EPR 登记。'), 'draft', '', 23)], None, None),
    ('德国站新品落地页', 'webpage', '德国市场进入', '德国站新品落地页', '绘影',
     [(page('Luftreiniger X1'), 'published', '', 60 * 26), (page('Luftreiniger X1'), 'published', '', 60 * 5),
      (page('Luftreiniger X1 – leise und effizient'), 'published', '', 120)], 'workspace_members', None),
    ('德国 EPR 与 WEEE 合规清单', 'document', '德国市场进入', '德国 EPR 与 WEEE 合规清单', '安界',
     [(doc('WEEE 注册：需在 stiftung ear 完成品类登记。'), 'draft', '', 90),
      (doc('WEEE 注册：需在 stiftung ear 完成品类登记。', 'EPR 包装：需在 LUCID 完成登记。'), 'accepted', '', 32)],
     'workspace_members', None),
    ('Amazon.de 关键词与类目机会.xlsx', 'spreadsheet', '德国市场进入', 'Amazon.de 关键词与类目机会', '觅位',
     [(sheet(('Luftreiniger', '74,000'), ('Luftreiniger Allergiker', '12,100')), 'draft', '', 60 * 3)], None, None),
    # Screen 56 counts only 竞品价格带 and EPR 合规清单 as new in the main task, so these two have no source task.
    ('竞品月销量估算.xlsx', 'spreadsheet', '德国市场进入', None, '望野',
     [(sheet(('Top 10 月销量中位数', '1,850 台')), 'draft', '', 60 * 30)], None, None),
    ('EPR 注册材料清单.xlsx', 'spreadsheet', '德国市场进入', '德国 EPR 与 WEEE 合规清单', '安界',
     [(sheet(('WEEE 品类申报表', '待补充')), 'draft', '', 60 * 28)], None, None),
    ('德国站差评主题分析', 'document', '德国市场进入', None, '望野',
     [(doc('头部竞品差评集中在插头规格和德语说明书。'), 'draft', '', 60 * 32)], None, None),
    # 设计与审核流基于 v2（应用改写生成 v3），所以保留两个历史版本
    ('X1 Listing DE', 'document', '德国市场进入', 'X1 德语 Listing 上架前审核', '译境',
     [(LISTING, 'draft', '', 60 * 26), (LISTING, 'draft', '', 60 * 4)], None, None),
    ('X1 主图.png', 'image', '德国市场进入', 'X1 德语 Listing 上架前审核', '绘影',
     [(image('X1 主图'), 'draft', '', 60 * 4)], None, None),
    ('首页文案 A/B 方案', 'document', '品牌官网改版', '首页文案 A/B 方案', '立言',
     [(doc('方案 A：安静，是更好的空气。', '方案 B：每一口呼吸都更轻松。'), 'draft', '', 60 * 6)], None, None),
    ('品牌语气指南', 'document', '品牌官网改版', '首页文案 A/B 方案', '立言',
     [(doc('对外称呼统一使用“您”。'), 'accepted', '', 60 * 50)], None, None),
    ('官网 SEO 关键词表.xlsx', 'spreadsheet', '品牌官网改版', '官网 SEO 关键词梳理', '觅位',
     [(sheet(('air purifier quiet', '8,100')), 'draft', '', 60 * 8)], None, None),
    ('品牌故事提案', 'presentation', '品牌官网改版', '首页文案 A/B 方案', '立言',
     [(slides('品牌起点', '我们相信的事'), 'draft', '', 60 * 54)], None, None),
    ('品牌官网首页', 'webpage', '品牌官网改版', '首页文案 A/B 方案', '绘影',
     [(page('Sage Air'), 'draft', '', 60 * 9)], None, None),
    ('品牌官网关于我们页', 'webpage', '品牌官网改版', '首页文案 A/B 方案', '绘影',
     [(page('关于我们'), 'draft', '', 60 * 56)], None, None),
    ('团队页头像 · 新版', 'image', '品牌官网改版', '团队页头像替换', '绘影',
     [(image('团队头像 12 张'), 'draft', '', 60 * 58)], None, None),
    ('Q4 达人合作提案', 'presentation', '黑五大促', 'Q4 欧洲达人合作清单', '立言',
     [(slides('合作目标'), 'draft', '', 60 * 40), (slides('合作目标', '达人清单'), 'draft', '', 60 * 24)],
     'encrypted_link', None),
    ('黑五主视觉 3 版', 'image', '黑五大促', '黑五主视觉设计', '绘影',
     [(image('黑五主视觉 3 版'), 'draft', '', 60 * 25)], 'only_me', None),
    ('黑五促销组合测算.xlsx', 'spreadsheet', '黑五大促', '促销组合与定价测算', '衡价',
     [(sheet(('满 €79 减 €10', '毛利 31%')), 'draft', '', 60 * 12)], None, None),
    ('黑五库存预警规则说明', 'document', '黑五大促', '黑五库存预警规则', '天时',
     [(doc('可售天数低于 21 天时预警。'), 'draft', '', 60 * 70)], None, None),
    # Screen 54 keeps the sample outside any workspace; screen 48 shows its delivery waiting for acceptance.
    ('德语 Listing 本地化样稿', 'document', None, '德语 Listing 本地化', '译境',
     [(doc('Leiser Luftreiniger für Allergiker.'), 'draft', '', 60 * 72)], None, None),
    ('店铺冬季头图.png', 'image', None, None, '绘影',
     [(image('店铺冬季头图'), 'draft', '', 60 * 75)], None, None),
    # Trash (screen 54 "回收站 3")
    ('旧版竞品报告（草稿）', 'document', '德国市场进入', '德国站竞品格局调研', '望野',
     [(doc('旧版草稿。'), 'draft', '', 60 * 24 * 6)], None, 2),
    ('黑五海报初稿', 'image', '黑五大促', '黑五主视觉设计', '绘影',
     [(image('黑五海报初稿'), 'draft', '', 60 * 24 * 8)], None, 5),
    ('官网首页草图', 'webpage', '品牌官网改版', '首页文案 A/B 方案', '绘影',
     [(page('草图'), 'draft', '', 60 * 24 * 9)], None, 7),
)
WORKSPACES = {'德国市场进入': (), '品牌官网改版': (('chen', 'editor'),), '黑五大促': (('chen', 'editor'),)}
# Matches seeds/tasks.py; any other source task is finished so the active list stays at screen 715's six.
TASK_STATUS = {'德国站竞品格局调研': 'awaiting_user', 'X1 德语 Listing 上架前审核': 'stuck',
               '德国 EPR 与 WEEE 合规清单': 'delivered', '德语 Listing 本地化': 'delivered',
               'Amazon.de 关键词与类目机会': 'running', 'Q4 欧洲达人合作清单': 'running'}
# The last four map each finished task to the expert who authored its artifacts; with 望野 as their lead his
# history would exceed the 24 tasks shown on screen 28.
TASK_LEAD = {'X1 德语 Listing 上架前审核': '安界', '德国 EPR 与 WEEE 合规清单': '安界', '德语 Listing 本地化': '译境',
             'Amazon.de 关键词与类目机会': '觅位', 'Q4 欧洲达人合作清单': '立言',
             '德国站新品落地页': '绘影', '团队页头像替换': '绘影', '黑五主视觉设计': '绘影', '促销组合与定价测算': '衡价'}
# Minutes since last activity on the seed clock (631 / 763: the EPR checklist at "32 分钟前"); others two hours.
TASK_ACTIVITY_MINUTES = {'德国 EPR 与 WEEE 合规清单': 32}
PENDING_DELIVERIES = (('德国站竞品格局报告', '德国站竞品格局报告草稿已完成，请验收。'),
                      ('德语 Listing 本地化样稿', '德语 Listing 本地化样稿 v1 已完成，请验收。'))

PLAYBOOK_STEPS = (
    ('understand', '确认{市场}进入目标与评估范围', '望野'),
    ('research', '{平台}关键词与类目机会', '觅位'),
    ('research', '{平台}{品类}竞品格局调研', '望野'),
    ('analyze', '{市场} EPR 与 WEEE 合规清单', '安界'),
    ('analyze', '竞品价格带与定价测算', '衡价'),
    ('deliver', '{市场}站进入建议报告', '立言'),
)


def expert(name):
    row = Expert.objects.filter(account__isnull=True, name=name).first()
    if row is None:
        row = Expert.objects.create(
            source='system', group='role', name=name, title=SPECIALTIES.get(name, name),
            specialty=SPECIALTIES.get(name), responsibilities=SPECIALTIES.get(name, name),
            avatar_ref='experts/%s.png' % name, version_label='v1', status='published', share_scope='account',
            communication_style='concise', uncertainty_policy='ask_first')
    return row


def workspace(ctx, name):
    business, users = ctx['business'], ctx['users']
    row = Workspace.objects.filter(account=business, name=name, deleted_at__isnull=True).first()
    if row is None:
        row = Workspace.objects.create(account=business, name=name, created_from='blank', created_by=users['lillian'])
    members = (('lillian', 'owner'),) + WORKSPACES.get(name, ())
    for key, role in members:
        if not WorkspaceMember.objects.filter(workspace=row, user=users[key]).exists():
            WorkspaceMember.objects.create(account=business, workspace=row, user=users[key], role=role,
                                           state='active', invited_by=users['lillian'], accepted_at=timezone.now())
    return row


def task(ctx, title, ws_row):
    business, member = ctx['business'], ctx['memberships']['lillian']
    row = Task.objects.filter(account=business, title=title).order_by('id').first()
    if row is None:
        status = TASK_STATUS.get(title, 'accepted')
        row = Task.objects.create(account=business, workspace=ws_row, title=title, creator=member, owner=member,
                                  lead_expert=expert(TASK_LEAD.get(title, '望野')), model_mode='auto',
                                  status=status, accepted_at=timezone.now() if status == 'accepted' else None,
                                  last_activity_at=timezone.now() - timedelta(minutes=TASK_ACTIVITY_MINUTES.get(title, 120)))
        TaskThread.objects.create(account=business, task=row, kind='main')
    return row


def rules(ctx, ws_row):
    if ws_row.rule_versions.exists():
        return ws_row.current_rule_version
    now, version = timezone.now(), None
    for number, (author, items, note, minutes) in enumerate(RULE_VERSIONS, start=1):
        version = WorkspaceRuleVersion.objects.create(account=ctx['business'], workspace=ws_row, number=number,
                                                      rules=items, author=ctx['users'][author], change_note=note)
        WorkspaceRuleVersion.objects.filter(pk=version.pk).update(created_at=now - timedelta(minutes=minutes))
    ws_row.current_rule_version = version
    ws_row.save(update_fields=['current_rule_version', 'updated_at'])
    return version


def artifact(ctx, spec, workspaces):
    title, kind, ws_name, task_title, author, versions, share, trashed = spec
    business = ctx['business']
    row = Artifact.objects.filter(account=business, title=title).first()
    if row is not None:
        return row
    ws_row = workspaces[ws_name] if ws_name else None
    source = task(ctx, task_title, ws_row) if task_title else None
    by_expert = expert(author)
    row = Artifact.objects.create(account=business, workspace=ws_row, source_task=source, title=title, kind=kind,
                                  created_by_expert=by_expert,
                                  created_by_user=ctx['users']['lillian'] if source is None else None)
    now = timezone.now()
    for body, state, label, minutes in versions:
        version = store.create_version(row, body, state=state, label=label, author_expert=by_expert)
        ArtifactVersion.objects.filter(pk=version.pk).update(created_at=now - timedelta(minutes=minutes))
    if share:
        ArtifactShare.objects.create(
            account=business, artifact=row, audience=share, created_by=ctx['users']['lillian'],
            token=secrets.token_urlsafe(32) if share == 'encrypted_link' else None,
            expires_at=now + timedelta(days=7) if share == 'encrypted_link' else None)
    if trashed:
        row.trashed_at = now - timedelta(days=trashed)
        row.save(update_fields=['trashed_at'])
    row.refresh_from_db()
    return row


def document(ctx, ws_row, title):
    business = ctx['business']
    row = KnowledgeDocument.objects.filter(account=business, title=title, deleted_at__isnull=True).first()
    if row is None:
        kb, _ = KnowledgeBase.objects.get_or_create(
            account=business, name='德国市场进入资料库', defaults={
                'scope': 'workspace', 'workspace': ws_row, 'owner': ctx['users']['lillian'],
                'cite_audience': 'workspace_all'})
        row = KnowledgeDocument.objects.create(account=business, knowledge_base=kb, title=title,
                                               source_kind='upload', uploaded_by=ctx['users']['lillian'],
                                               content_ref='seed:%s' % title, index_state='indexed')
    return row


def citations(ctx, report, epr, ws_row):
    version = report.current_version
    if version.citations.exists():
        return
    business, research = ctx['business'], report.source_task
    sheet_doc, law_doc = document(ctx, ws_row, '竞品清单.xlsx'), document(ctx, ws_row, '德国 EPR 法规摘要.pdf')
    Citation.objects.create(account=business, task=research, artifact_version=version, ref_index=1, document=sheet_doc,
                            cited_version=2, locator='Sheet1')
    Citation.objects.create(account=business, task=research, artifact_version=version, ref_index=2,
                            external_label='Amazon.de 类目页 · 11月3日')
    Citation.objects.create(account=business, task=research, artifact_version=version, ref_index=3, document=law_doc,
                            cited_version=1, locator='§3')
    Citation.objects.create(account=business, task=epr.source_task, artifact_version=epr.current_version, ref_index=1,
                            document=law_doc, cited_version=1, locator='§2')
    # The task conversation shows the same sources under 望野's progress message, and the opening request carries
    # the two files Lillian attached (screens 682/712/709/2736).
    progress = research.messages.filter(body__startswith='我把任务拆成 5 步').first()
    if progress is not None:
        Citation.objects.create(account=business, task=research, task_message=progress, ref_index=1, document=sheet_doc)
        Citation.objects.create(account=business, task=research, task_message=progress, ref_index=3, document=law_doc)
    opening = research.messages.filter(author_kind='user').order_by('sent_at', 'id').first()
    brand = KnowledgeDocument.objects.filter(account=business, title='品牌手册 2026.pdf', deleted_at__isnull=True).first()
    if opening is not None:
        opening.attached_documents.set([doc for doc in (sheet_doc, brand) if doc])


def review(ctx, listing, photo, rule_version):
    if ContentReview.objects.filter(task=listing.source_task).exists():
        return
    business = ctx['business']
    candidate = store.create_version(photo, image('X1 主图 · 去除促销文字'), label='修改版', author_expert=expert('绘影'))
    first = photo.versions.get(number=1)
    photo.current_version = first
    photo.save(update_fields=['current_version', 'updated_at'])
    row = ContentReview.objects.create(account=business, task=listing.source_task,
                                       dimensions=['platform_policy', 'market_law', 'brand', 'facts'],
                                       conclusion='需要修改 3 处后再上架。', state='reviewed',
                                       target_platform='Amazon.de', target_market='德国', verifier_expert=expert('守衡'))
    for name in ('安界', '立言', '觅位', '绘影'):
        row.reviewers.add(expert(name))
    for version in (listing.current_version, first):
        row.artifact_versions.add(version)
    issue = lambda **fields: ContentReviewIssue.objects.create(account=business, review=row, **fields)
    issue(dimension='market_law', severity='high', title='健康功效宣称超出允许范围', location='标题',
          original_text='Luftreiniger X1 – tötet 99,9 % aller Viren',
          suggested_text='Luftreiniger X1 – filtert bis zu 99,9 % der Partikel ab 0,3 µm',
          highlight_spans={'original': [[18, 42]], 'suggested': [[18, 63]]},
          basis='德国《反不正当竞争法》（UWG）§5；Amazon 受限产品政策 · 医疗宣称')
    issue(dimension='brand', severity='medium', title='品牌规范：对外内容应使用正式称谓“Sie”', location='五点描述第 2 条',
          original_text='Du wirst die frische Luft lieben', suggested_text='Sie werden die frische Luft lieben',
          highlight_spans={'original': [[0, 9]], 'suggested': [[0, 10]]},
          basis='工作区“德国市场进入”规则 v3 第 1 条', basis_rule_version=rule_version, basis_rule_index=1)
    issue(dimension='platform_policy', severity='medium', title='主图右上角含促销文字', location='主图',
          basis='Amazon 主图要求：不得含文字、徽标或促销信息', suggested_artifact_version=candidate)
    # Screen 23 shows the review card inline in the task conversation, after the lead's latest message.
    task = listing.source_task
    thread = task.threads.filter(kind='main').first() or TaskThread.objects.create(account=business, task=task, kind='main')
    last = TaskMessage.objects.filter(task=task).order_by('-sent_at').values_list('sent_at', flat=True).first()
    TaskMessage.objects.create(account=business, task=task, thread=thread, author_kind='expert',
                               author_expert=task.lead_expert or expert('安界'), kind='review_card',
                               body='内容审核已完成，审核结果如下。', sent_at=(last or timezone.now()) + timedelta(minutes=1),
                               card_type='review', card_id=row.public_id)


def playbook(ctx):
    business, lillian = ctx['business'], ctx['users']['lillian']
    if Playbook.objects.filter(account=business, name='新市场进入评估').exists():
        return
    row = Playbook.objects.create(account=business, name='新市场进入评估', visibility='only_me',
                                  created_by=lillian,
                                  variables=[{'name': '市场', 'default': '德国'}, {'name': '平台', 'default': 'Amazon.de'},
                                             {'name': '品类', 'default': '家用小家电'}])
    previous = None
    for order, (stage, title, name) in enumerate(PLAYBOOK_STEPS, start=1):
        step = PlaybookStep.objects.create(account=business, playbook=row, order=order, stage=stage, title=title,
                                           expert=expert(name))
        if previous is not None:
            step.depends_on.add(previous)
        previous = step


# Screens 59/60: the monthly scan runs this playbook for the French market; finished runs are fixed history.
SCAN_VARIABLES = {'市场': '法国', '平台': 'Amazon.fr', '品类': '家用小家电'}
SCAN_HISTORY = ((datetime(2026, 10, 1, 9, 0), 'succeeded', None), (datetime(2026, 9, 1, 9, 0), 'succeeded', None),
                (datetime(2026, 8, 1, 9, 0), 'failed', 'connection_invalid'))
# 60 屏：11月1日的运行停在第 4 步等待 Keepa 授权；credits 是已结算的共享额度（62+35+28=125）。
SCAN_LIVE_STEPS = (('法国站竞品格局分析', 'done', 0, 6, '62'), ('Amazon.fr 关键词与类目', 'done', 6, 10, '35'),
                   ('法国 EPR 合规清单', 'done', 10, 15, '28'), ('价格带与定价建议', 'awaiting_user', 15, None, None),
                   ('数字复核', 'queued', None, None, None), ('汇总进入建议', 'queued', None, None, None))


def scan_automation(ctx):
    from .inbox import _extension
    business, member = ctx['business'], ctx['memberships']['lillian']
    scan = Automation.objects.filter(account=business, owner=member, name='每月新市场机会扫描').first()
    row = Playbook.objects.filter(account=business, name='新市场进入评估').first()
    if scan is None or row is None or scan.content_kind == 'playbook':
        return
    scan.content_kind, scan.playbook, scan.prompt, scan.playbook_variables = 'playbook', row, None, SCAN_VARIABLES
    scan.lead_expert = expert('望野')
    scan.estimated_duration_minutes, scan.estimated_credits, scan.expected_output = 25, 180, '1 份进入评估报告'
    scan.save()
    AutomationPermission.objects.get_or_create(account=business, automation=scan, kind='connector_read',
                                               extension=_extension('Keepa'), defaults={'state': 'needs_grant'})
    zone = ZoneInfo('Asia/Shanghai')
    for at, state, failure in SCAN_HISTORY:
        scheduled = at.replace(tzinfo=zone)
        AutomationRun.objects.get_or_create(
            automation=scan, scheduled_for=scheduled, trigger='schedule',
            defaults={'account': business, 'title': '运行 Playbook · 市场：法国', 'variables': SCAN_VARIABLES,
                      'started_at': scheduled + timedelta(seconds=4), 'state': state, 'failure_code': failure,
                      'finished_at': scheduled + timedelta(minutes=2 if failure else 25)})


def scan_live_run(ctx):
    """60 屏：11月1日 09:00 的运行带真实步骤、已结算 Credits 和一条待确认的 Keepa 授权。"""
    from .inbox import _extension
    business, chen = ctx['business'], ctx['memberships']['chen']
    scan = Automation.objects.filter(account=business, owner=ctx['memberships']['lillian'],
                                     name='每月新市场机会扫描').first()
    if scan is None:
        return
    scheduled = datetime(2026, 11, 1, 9, 0, tzinfo=ZoneInfo('Asia/Shanghai'))
    started = scheduled + timedelta(seconds=4)
    run, _ = AutomationRun.objects.get_or_create(
        automation=scan, scheduled_for=scheduled, trigger='schedule',
        defaults={'account': business, 'title': '运行 Playbook · 市场：法国', 'variables': SCAN_VARIABLES,
                  'started_at': started, 'state': 'awaiting_user',
                  'finished_at': started + timedelta(minutes=15)})
    updates = {}
    if run.state != 'awaiting_user':
        updates['state'] = 'awaiting_user'
    # run_detail 用 finished_at 结算「已用时」；挂起等待授权时保留完成 3 步后的 15 分钟。
    if run.finished_at != started + timedelta(minutes=15):
        updates['finished_at'] = started + timedelta(minutes=15)
    task = run.task
    if task is None:
        task = Task.objects.filter(account=business, owner=chen, title='法国站竞品格局分析').first()
        if task is None:
            # owner 是 Chen 而非 Lillian：待确认授权不进 Lillian 的收件（徽章 9、需要我决定 4 保持不动）。
            task = Task.objects.create(account=business, title='法国站竞品格局分析', creator=chen, owner=chen,
                                       lead_expert=expert('望野'), model_mode='auto', status='awaiting_user',
                                       source_automation_run=run,
                                       last_activity_at=started + timedelta(minutes=15))
            TaskThread.objects.create(account=business, task=task, kind='main')
        updates['task'] = task
    if updates:
        for key, value in updates.items():
            setattr(run, key, value)
        run.save(update_fields=[*updates, 'updated_at'])
    if not task.steps.exists():
        for order, (title, status, start_min, end_min, _) in enumerate(SCAN_LIVE_STEPS, start=1):
            TaskStep.objects.create(
                account=business, task=task, order=order, stage='research', title=title,
                expert=task.lead_expert, status=status,
                started_at=started + timedelta(minutes=start_min) if start_min is not None else None,
                finished_at=started + timedelta(minutes=end_min) if end_min is not None else None)
    # 每步一笔共享额度结算（task_key 唯一约束要求按步区分），账本不进 CreditEntry，不影响用量页数字。
    shared, _ = SharedTask.objects.get_or_create(account=business, task_key=task.public_id,
                                                 defaults={'membership': chen, 'task_type': 'task'})
    if task.shared_task_id != shared.id:
        task.shared_task = shared
        task.save(update_fields=['shared_task', 'updated_at'])
    steps = {step.title: step for step in task.steps.all()}
    for title, _, _, _, credits in SCAN_LIVE_STEPS:
        if credits is None or title not in steps:
            continue
        step = steps[title]
        reservation, _ = Reservation.objects.get_or_create(
            account=business, task_key='%s:%s' % (task.public_id, step.public_id),
            defaults={'shared_task': shared, 'membership': chen, 'quantity': credits,
                      'settled_quantity': credits, 'state': 'settled'})
        TaskCostEvent.objects.get_or_create(
            account=business, source='seed', event_key='scan60:%s' % step.public_id,
            defaults={'reservation': reservation, 'payload': {}, 'request_hash': 'seed',
                      'step_key': step.public_id})
    keepa = _extension('Keepa')
    if keepa and '价格带与定价建议' in steps and not AuthorizationRequest.objects.filter(
            account=business, task=task).exists():
        AuthorizationRequest.objects.create(
            account=business, task=task, automation_run=run, step=steps['价格带与定价建议'],
            requested_by_expert=task.lead_expert, extension=keepa, action_kind='read_query',
            title='使用 Keepa 读取历史价格（只读）',
            purpose_text='望野想用 Keepa 读取 Amazon.fr 家用小家电类目近 90 天的历史价格。',
            scope_text='Amazon.fr · 历史价格', scope_params={'marketplace': 'amazon.fr'},
            estimate_state='estimated', estimated_credits=28, validity='this_task')


def seed(ctx):
    workspaces = {name: workspace(ctx, name) for name in WORKSPACES}
    rule_version = rules(ctx, workspaces['德国市场进入'])
    rows = {spec[0]: artifact(ctx, spec, workspaces) for spec in ARTIFACTS}
    report = rows['德国站竞品格局报告']
    citations(ctx, report, rows['德国 EPR 与 WEEE 合规清单'], workspaces['德国市场进入'])
    for title, summary in PENDING_DELIVERIES:
        row = rows[title]
        if not TaskDelivery.objects.filter(artifact_version__artifact=row).exists():
            TaskDelivery.objects.create(account=ctx['business'], task=row.source_task,
                                        artifact_version=row.current_version, summary=summary)
    review(ctx, rows['X1 Listing DE'], rows['X1 主图.png'], rule_version)
    playbook(ctx)
    scan_automation(ctx)
    scan_live_run(ctx)
    lillian, now = ctx['users']['lillian'], timezone.now()
    # Sidebar 置顶 / 全部 groups (screens 05, 101): Lillian pins 德国市场进入.
    WorkspaceMember.objects.filter(workspace=workspaces['德国市场进入'], user=lillian, pinned_at__isnull=True).update(
        pinned_at=now - timedelta(days=1))
    for title, minutes in (('德国站竞品格局报告', 5), ('德国站新品落地页', 60)):
        ArtifactUserState.objects.get_or_create(artifact=rows[title], user=lillian, defaults={
            'account': ctx['business'], 'last_opened_at': now - timedelta(minutes=minutes)})
    ctx['artifacts'] = rows
