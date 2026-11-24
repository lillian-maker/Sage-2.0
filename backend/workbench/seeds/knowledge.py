"""Knowledge bases and documents (37, 39-41 屏) and the knowledge graph (41-42 屏)."""
from datetime import timedelta

from ._clock import timezone

from backend.commerce.models import Department
from backend.workbench.models import (Artifact, ArtifactVersion, Expert, KnowledgeBase, KnowledgeDocument,
                                      KnowledgeEntity, KnowledgeFact, KnowledgeRelation, Workspace,
                                      WorkspaceKnowledgeSource)
from backend.workbench.services import knowledge as knowledge_service

ORDER = 30
MB = 1024 * 1024

# Offsets are relative to "now"; the screens read as if today were 11月6日.
NOW, MINUTES_10, TODAY, YESTERDAY = 'now', 'm10', 'today', 'yesterday'


def _at(offset):
    now = timezone.now()
    if offset == MINUTES_10:
        return now - timedelta(minutes=10)
    if offset == TODAY:
        return now - timedelta(hours=2)
    if offset == YESTERDAY:
        return now - timedelta(days=1)
    return now - timedelta(days=offset)


# name, scope, owner, cite_audience, expert_cite_enabled, editors, department, updated offset
BASES = (
    ('品牌手册 2026', 'personal', 'lillian', 'owner_only', True, (), None),
    ('德国市场进入资料库', 'workspace', 'lillian', 'workspace_all', True, ('lillian', 'chen'), None),
    ('竞品研究资料', 'account', 'chen', 'account_all', True, (), None),
    ('客服话术库', 'account', 'chen', 'account_all', False, (), '客服组'),
    ('德国市场法规库', 'workspace', 'lillian', 'workspace_all', True, ('lillian', 'chen'), None),
)
# 24 屏（727:2）资料来源只列“德国市场法规库”；“德国市场进入资料库”仍属该工作区（scope / workspace 不变），只是不作为资料来源。
WORKSPACE_SOURCES = ('德国市场法规库',)
# title, uploader, size bytes, pages, state, progress, updated offset, extra
DOCUMENTS = {
    '德国市场进入资料库': (
        ('德国 EPR 法规摘要.pdf', 'lillian', int(2.4 * MB), 6, 'indexed', None, 3, {}),
        ('竞品清单.xlsx', None, 86 * 1024, None, 'indexed', None, 2, {'expert': 'AGT-007'}),
        ('品牌手册 2026.pdf', 'chen', 12 * MB, 48, 'indexing', 62, MINUTES_10, {}),
        ('Amazon.de 小家电类目页', 'lillian', None, None, 'indexed', None, YESTERDAY,
         {'web': 'https://www.amazon.de/kleine-haushaltsgeraete', 'refresh': 'weekly'}),
        ('2025 Q3 销售报表.csv', 'mark', int(1.1 * MB), None, 'indexed', None, 109, {'data_age': 96}),
        ('德国站关键词清单.xlsx', 'chen', 240 * 1024, None, 'indexed', None, 10, {}),
        ('Amazon.de 类目费用说明.pdf', 'lillian', int(1.6 * MB), 9, 'indexed', None, 12, {}),
        ('德国消费者调研 2026.pdf', 'lillian', int(3.2 * MB), 24, 'indexed', None, 14, {}),
        ('竞品评论摘录.docx', 'chen', 520 * 1024, 11, 'indexed', None, 16, {}),
        ('德国物流时效对比.xlsx', 'mark', 130 * 1024, None, 'indexed', None, 20, {}),
        ('X1 系列产品规格书.pdf', 'lillian', int(4.5 * MB), 18, 'indexed', None, 25, {}),
        ('德国站 VAT 注册说明.pdf', 'lillian', int(0.9 * MB), 5, 'indexed', None, 30, {}),
    ),
    '德国市场法规库': (
        ('德国 EPR 法规摘要.pdf', 'lillian', int(2.4 * MB), 6, 'indexed', None, 3, {}),
        ('WEEE 注册指南 2026.pdf', 'lillian', int(1.8 * MB), 14, 'indexed', None, 4, {}),
        ('包装法 LUCID 操作手册.pdf', 'chen', int(2.1 * MB), 20, 'indexing', 35, TODAY, {}),
        ('CE 认证清单.xlsx', 'chen', 64 * 1024, None, 'indexed', None, 5, {}),
        ('ElektroG 条文摘录.pdf', 'lillian', int(1.2 * MB), 12, 'indexed', None, 20, {}),
        ('GPSR 产品安全要求.pdf', 'chen', int(0.8 * MB), 8, 'indexed', None, 22, {}),
        ('电池法 BattG 摘要.pdf', 'chen', int(0.6 * MB), 6, 'indexed', None, 26, {}),
        # 40 屏：望野产出的资料，数据日期 8月2日 起已超过 90 天（过期）
        ('RoHS 限制物质清单.xlsx', 'lillian', 12 * 1024 * 1024, None, 'indexed', None, 109,
         {'expert': 'AGT-007', 'data_age': 109}),
    ),
}
FILLER_BASES = {
    # name: (count, prefix, updated offset of the newest, failed count)
    '品牌手册 2026': (12, ('品牌主张', '视觉规范', '语气指南', '标志使用', '色彩规范', '字体规范', '包装规范',
                       '摄影风格', 'Listing 模板', '广告素材规范', '社媒规范', '品牌故事'), YESTERDAY, 0),
    # 23 fillers + the 2025-06 source of the superseded WEEE fact = 24 (39 屏).
    # 39 屏 draws 10月30日 / 10月28日 for these two bases against the 11月19日 render clock.
    '竞品研究资料': (23, None, 20, 0),
    '客服话术库': (16, None, 22, 1),
}
OLD_SOURCE_TITLE = '竞品研究资料'

ENTITIES = {
    'market': ('德国市场', '法国市场', '英国市场', '北欧市场', '美国市场', '西班牙市场'),
    'competitor': tuple(['竞品 A · Brand K', '竞品 B · Rotfeld'] + ['竞品 %s · %s' % pair for pair in zip(
        'CDEFGHIJKLMNOPQRSTUVWX', ('Kochwerk', 'Lumora', 'Heimfux', 'Brisa', 'Nordhaus', 'Vellaro', 'Klarion',
                                   'Ostwind', 'Pureline', 'Talvik', 'Grünberg', 'Mavella', 'Zentro', 'Aurix',
                                   'Felsen', 'Quellwerk', 'Sonnhof', 'Brightway', 'Elvora', 'Kaltberg', 'Novalux',
                                   'Rhinetal'))]),
    'regulation': ('WEEE 注册', 'EPR / LUCID 登记', 'VerpackG', 'Amazon.de 合规核验', 'CE 认证', 'RoHS 限制物质',
                   'GPSR 产品安全', 'ErP 能效要求', '电池法 BattG'),
    'product': ('我们的 X1 系列', 'X2 系列', 'X1 Pro', '空气炸锅 A3', '手持吸尘器 V5', '电热水壶 K2', '咖啡机 C1',
                '破壁机 B7', '电动牙刷 T3', '加湿器 H2', '电饭煲 R4', '便携榨汁杯 J1'),
    'channel': ('Amazon.de', 'Amazon.de 小家电类目', '独立站', 'Otto.de'),
    'task': ('德国站竞品格局调研', '德国站定价策略', '德语 Listing 本地化', '欧盟包装法规对照表', '物流头程方案比价',
             '德国站产品质检标准', '北欧市场进入评估', 'Q4 欧洲达人合作清单', '德国站广告投放计划',
             '德国 EPR 与 WEEE 合规清单', '德国站利润测算', '黑五备货节奏', '品牌主张文案', 'X1 德语 Listing 上架前审核',
             '首页文案 A/B 方案', '售后话术库整理', '德国站关键词研究', '德国站 VAT 注册'),
}
SUBTITLES = {'WEEE 注册': '法规 · 德国市场', 'EPR / LUCID 登记': '法规 · 德国市场', 'VerpackG': '法规 · 德国市场',
             'Amazon.de 合规核验': '法规 · Amazon.de', '德国市场': '市场', '我们的 X1 系列': '产品 · 小家电'}
RELATIONS = (
    ('Amazon.de 小家电类目', 'Amazon.de', 'belongs_to'), ('Amazon.de', '德国市场', 'channel'),
    ('竞品 A · Brand K', 'Amazon.de 小家电类目', 'top_competitor'),
    ('竞品 B · Rotfeld', 'Amazon.de 小家电类目', 'top_competitor'),
    ('德国市场', 'WEEE 注册', 'requires'), ('德国市场', 'EPR / LUCID 登记', 'requires'),
    ('德国市场', 'VerpackG', 'requires'), ('我们的 X1 系列', '德国市场', 'plans_to_enter'),
    ('德国站竞品格局调研', 'WEEE 注册', 'cites'), ('德国站竞品格局调研', '德国市场', 'cites'),
)
EPR_ENTITIES = ('WEEE 注册', 'VerpackG', 'Amazon.de 合规核验')

EPR_PAGES = [
    {'blocks': [
        {'kind': 'heading', 'text': '德国 EPR 法规摘要（2026 版）', 'spans': []},
        {'kind': 'paragraph', 'text': '1. 适用范围：在德国销售电子电气设备的生产者、进口商与跨境卖家，均需在 stiftung ear 完成 '
                                      'WEEE 注册，注册号须展示在商品页。', 'spans': ['WEEE 注册']},
        {'kind': 'paragraph', 'text': '2. 包装法（VerpackG）：向德国终端消费者销售带包装商品前，须在 LUCID 系统注册并加入双元回收体系。',
         'spans': []},
        {'kind': 'paragraph', 'text': '3. 平台责任：自 2023 年起，Amazon.de 有义务核验卖家的 EPR 注册号，缺少注册号的商品会被下架。',
         'spans': ['缺少注册号的商品会被下架']},
        {'kind': 'paragraph', 'text': '4. 办理周期：WEEE 注册一般需要 4–6 周，需准备授权代表协议、产品类别与预计年销量。', 'spans': []},
    ]},
    {'blocks': [{'kind': 'heading', 'text': '一、WEEE 注册流程', 'spans': []},
                {'kind': 'paragraph', 'text': '委托授权代表、提交产品类别与品牌信息、缴纳担保，取得 WEEE-Reg.-Nr.。', 'spans': []}]},
    {'blocks': [{'kind': 'heading', 'text': '二、LUCID 包装注册', 'spans': []},
                {'kind': 'paragraph', 'text': '在 LUCID 登记生产者信息与包装数量，并与双元回收体系签约。', 'spans': []}]},
    {'blocks': [{'kind': 'heading', 'text': '三、平台核验', 'spans': []},
                {'kind': 'paragraph', 'text': 'Amazon.de 在卖家后台核验 EPR 注册号，未通过核验的商品不可售。', 'spans': []}]},
    {'blocks': [{'kind': 'heading', 'text': '四、年度申报', 'spans': []},
                {'kind': 'paragraph', 'text': '每年按实际投放量申报电子设备与包装数量，逾期可能被罚款。', 'spans': []}]},
    {'blocks': [{'kind': 'heading', 'text': '五、所需材料清单', 'spans': []},
                {'kind': 'list_item', 'text': '授权代表协议、营业执照、产品类别与预计年销量。', 'spans': []}]},
]


def _spans(text, phrases, entities):
    spans = []
    for phrase in phrases:
        start = text.find(phrase)
        if start < 0:
            continue
        entity = entities.get(phrase)
        spans.append({'start': start, 'end': start + len(phrase), 'kind': 'entity' if entity else 'highlight',
                      'entity_id': entity.public_id if entity else None})
    return spans


def seed_bases(ctx):
    business, users, workspace = ctx['business'], ctx['users'], ctx['workspace']
    department, _ = Department.objects.get_or_create(account=business, name='客服组')
    bases = {}
    for name, scope, owner, cite, enabled, editors, dept in BASES:
        kb = KnowledgeBase.objects.filter(account=business, name=name).first()
        if kb is None:
            kb = KnowledgeBase.objects.create(account=business, name=name, scope=scope, owner=users[owner],
                                              workspace=workspace if scope == 'workspace' else None,
                                              cite_audience=cite, expert_cite_enabled=enabled,
                                              editor_department=department if dept else None)
            kb.editors.set([users[key] for key in editors])
        bases[name] = kb
        if name in WORKSPACE_SOURCES:
            WorkspaceKnowledgeSource.objects.get_or_create(account=business, workspace=workspace, knowledge_base=kb,
                                                           defaults={'added_by': users[owner]})
    return bases


def _document(kb, title, **fields):
    doc = KnowledgeDocument.objects.filter(knowledge_base=kb, title=title, deleted_at__isnull=True).first()
    created = doc is None
    if created:
        doc = KnowledgeDocument.objects.create(account=kb.account, knowledge_base=kb, title=title, content_ref='',
                                               **fields)
    return doc, created


def _expert_version(expert, title):
    artifact = Artifact.objects.filter(account__isnull=False, title=title, created_by_expert=expert,
                                       workspace__isnull=True, source_task__isnull=True).first()
    if artifact is None:
        return None
    return artifact.current_version


def seed_documents(ctx, bases):
    users, business = ctx['users'], ctx['business']
    docs = {}
    for kb_name, rows in DOCUMENTS.items():
        kb = bases[kb_name]
        for title, uploader, size, pages, state, progress, offset, extra in rows:
            fields = {'source_kind': 'upload', 'uploaded_by': users.get(uploader), 'size_bytes': size,
                      'page_count': pages, 'index_state': state, 'index_progress': progress}
            if 'web' in extra:
                fields.update(source_kind='web', source_url=extra['web'], refresh_interval=extra['refresh'],
                              uploaded_by=users[uploader])
            if 'expert' in extra:
                expert = Expert.objects.get(catalog_code=extra['expert'])
                version = _expert_version(expert, title)
                if version is None:
                    artifact = Artifact.objects.create(account=business, title=title, kind='spreadsheet',
                                                       created_by_expert=expert)
                    version = ArtifactVersion.objects.create(account=business, artifact=artifact, number=1,
                                                             state='accepted', author_expert=expert,
                                                             content_ref='seed:%s' % title, size_bytes=size)
                    artifact.current_version = version
                    artifact.save(update_fields=['current_version'])
                fields.update(source_kind='expert_output', uploaded_by=None, source_artifact_version=version)
            if 'data_age' in extra:
                fields['data_date'] = (timezone.now() - timedelta(days=extra['data_age'])).date()
            doc, created = _document(kb, title, **fields)
            if created:
                KnowledgeDocument.objects.filter(pk=doc.pk).update(created_at=_at(offset), updated_at=_at(offset))
            docs.setdefault(kb_name, {})[title] = doc
    for kb_name, (count, titles, newest, failed) in FILLER_BASES.items():
        kb = bases[kb_name]
        for index in range(count):
            title = ('%s.pdf' % titles[index]) if titles else '%s %02d.pdf' % (kb_name, index + 1)
            state = 'failed' if index >= count - failed else 'indexed'
            owner = kb.owner
            doc, created = _document(kb, title, source_kind='upload', uploaded_by=owner,
                                     size_bytes=(index + 2) * 180 * 1024, page_count=index + 3, index_state=state)
            if created:
                at = _at(newest) - timedelta(days=index * 2)
                KnowledgeDocument.objects.filter(pk=doc.pk).update(created_at=at, updated_at=at)
            docs.setdefault(kb_name, {})[title] = doc
    old, created = _document(bases['竞品研究资料'], OLD_SOURCE_TITLE, source_kind='upload', uploaded_by=users['chen'],
                             size_bytes=int(1.3 * MB), page_count=16, index_state='indexed')
    if created:
        at = timezone.now() - timedelta(days=150)
        KnowledgeDocument.objects.filter(pk=old.pk).update(created_at=at, updated_at=at)
    docs['竞品研究资料'][OLD_SOURCE_TITLE] = old
    return docs


def seed_graph(ctx, docs):
    business = ctx['business']
    entities = {}
    for entity_type, names in ENTITIES.items():
        for name in names:
            entity, _ = KnowledgeEntity.objects.get_or_create(
                account=business, name=name, entity_type=entity_type, defaults={'subtitle': SUBTITLES.get(name)})
            entities[name] = entity
    for source, target, kind in RELATIONS:
        KnowledgeRelation.objects.get_or_create(account=business, from_entity=entities[source],
                                                to_entity=entities[target], kind=kind)
    # 同一份文件上传到两个资料库（2468:1129）：实体关联同时挂到两个副本上，
    # 否则法规库副本的详情页会显示“没有关联实体”，与知识图谱的事实来源不一致。
    eprs = [docs['德国市场进入资料库']['德国 EPR 法规摘要.pdf'], docs['德国市场法规库']['德国 EPR 法规摘要.pdf']]
    for name in EPR_ENTITIES:
        for epr_doc in eprs:
            entities[name].documents.add(epr_doc)
    epr = eprs[0]
    weee = entities['WEEE 注册']
    if not weee.facts.exists():
        source_date = (timezone.now() - timedelta(days=30)).date().replace(day=1)
        KnowledgeFact.objects.create(account=business, entity=weee, statement='上架前必须取得 WEEE 注册号',
                                     source_document=epr, source_date=source_date)
        new = KnowledgeFact.objects.create(account=business, entity=weee, statement='注册周期 4–6 周',
                                           source_document=epr, source_date=source_date, conflict_key='weee_cycle')
        KnowledgeFact.objects.create(account=business, entity=weee, statement='注册周期约 8 周',
                                     source_document=docs['竞品研究资料'][OLD_SOURCE_TITLE],
                                     source_date=source_date.replace(year=source_date.year - 1, month=6),
                                     validity='superseded', superseded_by=new, conflict_key='weee_cycle')
    return entities


def seed_content(docs, entities):
    for kb_name in docs:
        for title, doc in docs[kb_name].items():
            if doc.content_ref or doc.index_state != 'indexed':
                continue
            if title == '德国 EPR 法规摘要.pdf':
                pages = [{'blocks': [dict(block, spans=_spans(block['text'], block['spans'], entities))
                                     for block in page['blocks']]} for page in EPR_PAGES]
            else:
                pages = [{'blocks': [{'kind': 'heading', 'text': title.rsplit('.', 1)[0], 'spans': []}]}]
            ref = knowledge_service.save_document_content(doc, {'pages': pages})
            KnowledgeDocument.objects.filter(pk=doc.pk).update(content_ref=ref)


def seed(ctx):
    if 'workspace' not in ctx:
        ctx['workspace'] = Workspace.objects.get(account=ctx['business'], name='德国市场进入')
    bases = seed_bases(ctx)
    docs = seed_documents(ctx, bases)
    entities = seed_graph(ctx, docs)
    seed_content(docs, entities)
    ctx.update(knowledge_bases=bases, knowledge_documents=docs, knowledge_entities=entities)
