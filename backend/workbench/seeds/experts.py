"""Expert catalog (27-29 屏), extension catalog and installs (35-36, 43-46, 73 屏) and teams (30-31 屏)."""
import json
from pathlib import Path

from django.conf import settings
from ._clock import timezone

from backend.workbench.models import (ConnectorConnection, Expert, ExpertCapability, ExpertTeam, Extension,
                                      ExtensionInstall)

ORDER = 10

ROLE_AVATAR = 'digital-employees/assets/light/512/%s.webp'
MANAGEMENT_AVATAR = 'assets/people/%s.webp'
ASSISTANT_CODE = 'SAGE-ASSISTANT'

MANAGEMENT = (
    ('MGMT-CEO', '天时', '首席执行官 CEO', None,
     '在已批准的企业约束内，统筹跨业务的目标、优先级与资源分配，明确关键取舍，形成全局决策方案。', ['目标拆解', '资源取舍', '决策方案']),
    ('MGMT-CGO', '地利', '首席增长官 CGO', '首席增长官',
     '统筹流量、内容、达人、商品与履约投入，识别各环节可归因的增量贡献，优化投入组合与增长路径。', ['投入组合', '增量归因', '增长路径']),
    ('MGMT-CGOV', '人和', '首席治理官 CGOV', None,
     '明确经营边界、资源与能力供给规则，保留规则版本和决策依据，让经营活动可追溯、可独立检验。', ['经营边界', '规则版本', '决策留痕']),
)
# Card copy visible on screens 27-29; the other role experts use their workforce.json catalog entry.
ROLE_COPY = {
    'AGT-001': ('将经营目标拆解为业务优先级与资源需求，比较投入方案和约束条件，形成可执行的目标与资源方案。', ['资源测算', '方案比较']),
    'AGT-002': ('根据业务场景选择岗位与技能，明确阶段规则和交接条件，跟踪任务状态、协调异常处理，推动问题闭环。', ['场景编排', '交接规则', '异常闭环']),
    'AGT-003': ('核对经营数据与关键假设，解释结果变化及其原因，提供带来源的分析结论与备选方案，支持经营判断。', ['数据核对', '归因分析', '决策备选']),
    'AGT-004': ('围绕业务需要，提供人才、培训、绩效、编制与行政支持，识别组织能力缺口，提出人员配置与协作建议。', ['能力盘点', '人员配置', '培训计划']),
    'AGT-007': ('发现市场变化与尚未满足的机会。', ['竞品格局', '市场规模', '机会评估']),
}
SPECIALTY = {'AGT-044': '合规与认证', 'AGT-022': '关键词研究', 'AGT-026': '定价策略', 'AGT-029': '品牌叙事',
             'AGT-040': '对账复核'}
# 29 屏协作网络右侧边栏：从“我”的视角描述与望野各位协作专家的常见分工，生成器概括不出这些固定文案。
NETWORK_NOTE = {'AGT-044': '我把合规相关的研究交给他', 'AGT-026': '竞品价格带交给他做定价',
                'AGT-040': '复核我产出的数字', 'AGT-022': '给我提供关键词与类目数据'}
# 28 屏望野资料页的交办示例。
START_EXAMPLE = '帮我评估我们的品类进入德国市场的竞争情况和机会。'
REPORTS_TO = {'AGT-007': 'MGMT-CGO'}
VERSION_LABEL = {'AGT-007': 'v1.2'}

ASSISTANT = ('Sage 助手', '兜底主责', '不确定找谁时交给我：理解需求、拆解任务，再邀请合适的专家。')

# name, kind, tier, version, description, extra
EXTENSIONS = (
    ('PPT 生成', 'plugin', 'official', 'v2.1', '把报告和结论排成可编辑的演示文稿。', {}),
    ('表格处理', 'plugin', 'official', 'v1.4', '读取、清洗和汇总 Excel / CSV 数据。', {}),
    ('竞品格局分析', 'skill', 'official', 'v1.3', '从类目页、评论和价格中整理竞品格局与差异点。', {
        'prerequisite_inputs': ['竞品清单或类目页链接'], 'outputs': ['竞品格局报告（文档）'],
        'acceptance_template': ['覆盖前 20 名', '注明数据日期']}),
    ('定价策略', 'skill', 'official', 'v1.1', '根据价格带与成本边界给出定价区间与理由。', {}),
    ('Jungle Scout', 'connector', 'third_party', 'v1.8', '竞品销量估算与关键词数据，按次计费。',
     {'billing_note': '按次计费', 'permissions': [{'name': '读取竞品销量与关键词数据', 'access': 'read_only'}]}),
    ('销量估算', 'skill', 'official', 'v1.0', '估算竞品月销量，需要 Jungle Scout 连接器。',
     {'required': 'Jungle Scout'}),
    ('Amazon Seller Central', 'connector', 'official', 'v2.3', '读取订单、库存、广告与店铺绩效数据。',
     {'permissions': [{'name': '读取订单与库存', 'access': 'read_only'}]}),
    ('企业邮箱', 'connector', 'official', 'v1.2', '把需要处理的邮件整理进收件。', {}),
    ('飞书', 'connector', 'official', 'v1.0', '同步群消息到收件，可引用飞书文档。', {}),
    ('售后工单智能体', 'agent', 'enterprise', 'v1.0', '企业已有的客服 Agent，可被专家调用查询工单。', {'account': True}),
    # Market (44 屏)
    ('图片背景替换', 'plugin', 'official', 'v1.0', '批量替换商品主图背景，输出新版本。', {'global_usage_count': 9800}),
    ('TikTok Shop 选品', 'skill', 'official', 'v1.2', '按趋势与达人带货数据筛选候选品。', {'global_usage_count': 12000}),
    ('差评归因', 'skill', 'community_reviewed', 'v2.1', '把差评按问题类型和产品部件归类。', {'global_usage_count': 6100}),
    ('Google Drive', 'connector', 'official', 'v1.4', '引用云端文档与表格作为任务资料。', {}),
    ('物流追踪智能体', 'agent', 'third_party', 'v1.0', '查询跨境包裹轨迹与异常，供售后专家调用。', {'global_usage_count': 2300}),
    # Skills and tools referenced by screens 28, 35, 36 and 46
    ('市场规模测算', 'skill', 'official', 'v1.0', '按品类与渠道估算目标市场规模。', {}),
    ('评论洞察摘要', 'skill', 'community_reviewed', 'v2.1', '归纳评论中的需求、痛点与卖点。', {}),
    ('Amazon 广告', 'connector', 'official', 'v1.0', '读取搜索词报告与广告活动表现。', {'permissions': [
        {'name': '读取搜索词报告', 'access': 'read_only'}, {'name': '读取广告活动表现', 'access': 'read_only'},
        {'name': '修改出价、预算', 'access': 'write_unavailable'}]}),
    ('Amazon 关键词研究', 'skill', 'official', 'v2.0', '挖掘搜索词与类目机会，输出关键词清单。', {
        'required': 'Amazon 广告', 'permissions': [
            {'name': '读取搜索词报告', 'access': 'read_only'}, {'name': '读取广告活动表现', 'access': 'read_only'},
            {'name': '修改出价、预算', 'access': 'write_unavailable'}]}),
    ('DeepL', 'connector', 'official', 'v1.0', '调用 DeepL 翻译与术语表。', {}),
    ('企业法务知识库', 'connector', 'enterprise', 'v1.0', '查询企业法务部维护的宣称与合规口径。', {'account': True}),
    ('德语本地化校对', 'skill', 'local_import', 'v1.1', '按德语母语习惯校对译文与术语。',
     {'account': True, 'required': 'DeepL'}),
    ('宣称合规检查', 'skill', 'official', 'v1.0', '检查营销宣称是否符合德国与欧盟规定。', {'required': '企业法务知识库'}),
    ('欧盟 CE 标签检查', 'skill', 'official', 'v1.0', '核对 CE 标识与随附文件要求。', {}),
    ('黑五促销方案', 'skill', 'official', 'v1.0', '按库存与价格带编排黑五促销组合。', {}),
)
# 43 屏: ten installs (plugin 2 · skill 3 · connector 4 · agent 1, 73 屏).
INSTALLS = (('PPT 生成', True), ('表格处理', True), ('竞品格局分析', True), ('定价策略', True), ('销量估算', True),
            ('Amazon Seller Central', True), ('企业邮箱', True), ('飞书', True), ('Jungle Scout', True),
            ('售后工单智能体', False))
# Account-level connections. Amazon 广告 is connected without an install so 28/36 show "已连接" while
# 43/73 keep exactly ten installs.
CONNECTIONS = (('Amazon Seller Central', 'connected'), ('企业邮箱', 'connected'), ('飞书', 'expired'),
               ('Jungle Scout', 'not_connected'), ('Amazon 广告', 'connected'))
# expert code -> skills / tools (tool, required_for)
CAPABILITIES = {
    'AGT-007': (('竞品格局分析', '市场规模测算', '评论洞察摘要'), (('Amazon 广告', None), ('Jungle Scout', '销量估算'))),
    'AGT-022': (('Amazon 关键词研究', '竞品格局分析'), (('Amazon 广告', 'Amazon 关键词研究'),)),
    # 竞品格局分析 is "用于 3 位专家" on 43 屏: 望野, 觅位 and 北辰.
    'AGT-021': (('定价策略', '竞品格局分析'), ()),
    'AGT-026': (('定价策略',), ()),
    'AGT-001': ((), (('PPT 生成', None),)),
    'AGT-003': ((), (('PPT 生成', None), ('表格处理', None))),
    'AGT-029': ((), (('PPT 生成', None),)),
    'AGT-030': ((), (('PPT 生成', None),)),
    'AGT-040': ((), (('表格处理', None),)),
    'AGT-041': ((), (('表格处理', None),)),
    'AGT-016': ((), (('表格处理', None),)),
    'AGT-017': ((), (('表格处理', None),)),
    'AGT-045': ((), (('表格处理', None),)),
}

TEAMS = (
    # name, description, lead, members, source, visibility, creator
    ('新市场进入小组', '竞品、关键词、合规、定价与数字复核，一次组齐', 'AGT-007',
     ('AGT-007', 'AGT-022', 'AGT-044', 'AGT-026', 'AGT-040'), 'template', 'account', None),
    ('售后快速响应', '差评与买家消息的回复、补偿方案和批次排查', 'AGT-037', ('AGT-037', 'AGT-036', 'AGT-018'),
     'custom', 'only_me', 'lillian'),
    ('内容审核组', '上架前的 Listing、主图与广告素材审核', 'AGT-044', ('AGT-044', 'AGT-029', 'AGT-031'),
     'custom', 'account', 'chen'),
)


def _workforce():
    path = Path(settings.BASE_DIR) / 'digital-employees' / 'workforce.json'
    rows = json.loads(path.read_text(encoding='utf-8'))
    return [row for row in rows if row['id'] <= 'AGT-050']


def _upsert_expert(code, name, **fields):
    expert = Expert.objects.filter(catalog_code=code).first() or Expert.objects.filter(
        account__isnull=True, source='system', name=name).exclude(status='archived').first()
    values = dict(source='system', catalog_code=code, name=name, status='published', share_scope='account',
                  communication_style='conclusion_first', uncertainty_policy='ask_first', **fields)
    if expert is None:
        return Expert.objects.create(account=None, **values)
    for key, value in values.items():
        setattr(expert, key, value)
    expert.save()
    return expert


def seed_experts():
    experts = {}
    for code, name, title, specialty, responsibilities, tags in MANAGEMENT:
        experts[code] = _upsert_expert(code, name, group='management', title=title, domain='', specialty=specialty,
                                       responsibilities=responsibilities, capability_tags=tags,
                                       avatar_ref=MANAGEMENT_AVATAR % code, version_label='v1.0')
    for row in _workforce():
        code = row['id']
        responsibilities, tags = ROLE_COPY.get(code, ('围绕%s，交付%s。' % (row['job'], row['deliver']), []))
        experts[code] = _upsert_expert(code, row['name'], group='role', title=row['job'][:40], domain=row['domain'],
                                       specialty=SPECIALTY.get(code), responsibilities=responsibilities,
                                       capability_tags=tags, avatar_ref=ROLE_AVATAR % code,
                                       version_label=VERSION_LABEL.get(code, 'v1.0'))
    name, title, responsibilities = ASSISTANT
    experts[ASSISTANT_CODE] = _upsert_expert(ASSISTANT_CODE, name, group='role', title=title, domain='',
                                             specialty=title, responsibilities=responsibilities, capability_tags=[],
                                             avatar_ref=None, version_label='v1.0')
    for code, boss in REPORTS_TO.items():
        Expert.objects.filter(pk=experts[code].pk).update(reports_to=experts[boss])
    for code, note in NETWORK_NOTE.items():
        Expert.objects.filter(pk=experts[code].pk).update(collaboration_note=note)
    Expert.objects.filter(pk=experts['AGT-007'].pk).update(start_description=START_EXAMPLE)
    # 28 屏: "v1.2 · 10月30日"
    Expert.objects.filter(pk=experts['AGT-007'].pk).update(updated_at=timezone.now() - timezone.timedelta(days=20))
    return experts


def seed_extensions(business):
    rows = {}
    for name, kind, tier, version, description, extra in EXTENSIONS:
        extra = dict(extra)
        account = business if extra.pop('account', False) else None
        required = extra.pop('required', None)
        values = dict(kind=kind, publisher_tier=tier, latest_version=version, description=description,
                      required_connector=rows[required] if required else None,
                      permissions=extra.pop('permissions', []), **extra)
        ext = Extension.objects.filter(account=account, name=name).first()
        if ext is None:
            ext = Extension.objects.create(account=account, name=name, **values)
        else:
            for key, value in values.items():
                setattr(ext, key, value)
            ext.save()
        rows[name] = ext
    return rows


def seed_installs(business, owner, extensions):
    for name, enabled in INSTALLS:
        ext = extensions[name]
        ExtensionInstall.objects.get_or_create(account=business, extension=ext, defaults={
            'installed_version': ext.latest_version, 'enabled': enabled, 'installed_by': owner})
    now = timezone.now()
    for name, state in CONNECTIONS:
        ConnectorConnection.objects.get_or_create(
            account=business, extension=extensions[name], owner_user=None,
            defaults={'state': state, 'connected_by': owner if state != 'not_connected' else None,
                      'connected_at': now if state != 'not_connected' else None,
                      'credential_ref': 'seed:%s' % name if state == 'connected' else None,
                      'last_error_code': 'TOKEN_EXPIRED' if state == 'expired' else None})


def seed_capabilities(business, experts, extensions):
    for code, (skills, tools) in CAPABILITIES.items():
        for name in skills:
            ExpertCapability.objects.get_or_create(account=business, expert=experts[code],
                                                   extension=extensions[name])
        for name, required_for in tools:
            ExpertCapability.objects.get_or_create(
                account=business, expert=experts[code], extension=extensions[name],
                defaults={'required_for': extensions[required_for] if required_for else None})


def seed_teams(business, users, experts):
    for name, description, lead, members, source, visibility, creator in TEAMS:
        account = None if source == 'template' else business
        team = ExpertTeam.objects.filter(account=account, name=name).first()
        if team is None:
            team = ExpertTeam.objects.create(account=account, name=name, description=description,
                                             lead_expert=experts[lead], source=source, visibility=visibility,
                                             created_by=users[creator] if creator else None)
        team.members.set([experts[code] for code in members])


def seed(ctx):
    business, users = ctx['business'], ctx['users']
    experts = seed_experts()
    extensions = seed_extensions(business)
    seed_installs(business, users['lillian'], extensions)
    seed_capabilities(business, experts, extensions)
    seed_teams(business, users, experts)
    # 37 屏「新建专家 · 知识」向导需要一个进行中的草稿才能渲染知识库列表
    if not Expert.objects.filter(account=business, created_by=users['lillian'], status='draft').exists():
        Expert.objects.create(account=business, source='custom', group='role', name='售后质检助理',
                              title='售后质检助理', responsibilities='抽查售后回复的话术与事实准确性',
                              version_label='', status='draft', share_scope='only_me',
                              created_by=users['lillian'], start_mode='describe',
                              start_description='帮我建一个抽查售后回复质量的专家',
                              communication_style='conclusion_first', uncertainty_policy='ask_first')
    ctx.update(experts=experts, extensions=extensions)
