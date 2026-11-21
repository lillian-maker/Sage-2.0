import type { ReactNode } from 'react';
import { ActionBar } from '../components/ActionBar/ActionBar';
import { WindowControl } from '../components/AppShell/WindowControl';
import { AppSidebar, type SidebarWorkspace } from '../components/AppSidebar/AppSidebar';
import { SidebarAccountBar } from '../components/AppSidebar/SidebarAccountBar';
import { SidebarNavItem } from '../components/AppSidebar/SidebarNavItem';
import { SidebarShowMore } from '../components/AppSidebar/SidebarShowMore';
import { SidebarTaskItem } from '../components/AppSidebar/SidebarTaskItem';
import { SidebarWorkspaceItem } from '../components/AppSidebar/SidebarWorkspaceItem';
import { Avatar } from '../components/Avatar/Avatar';
import { BackLink } from '../components/BackLink/BackLink';
import { Button } from '../components/Button/Button';
import { CardHeader } from '../components/Card/CardHeader';
import { ChoiceChip } from '../components/ChoiceChip/ChoiceChip';
import { ChoiceChipGroup } from '../components/ChoiceChip/ChoiceChipGroup';
import { Composer } from '../components/Composer/Composer';
import { ComposerContextBar } from '../components/Composer/ComposerContextBar';
import { ConfirmDialog } from '../components/ConfirmDialog/ConfirmDialog';
import { CountBadge } from '../components/CountBadge/CountBadge';
import { HeaderMemberActions } from '../components/HeaderMemberActions/HeaderMemberActions';
import { Icon } from '../components/Icon/Icon';
import { IconButton } from '../components/IconButton/IconButton';
import { IconTile } from '../components/IconTile/IconTile';
import { InlineNotice } from '../components/InlineNotice/InlineNotice';
import { Kbd } from '../components/Kbd/Kbd';
import { MenuItem } from '../components/Menu/MenuItem';
import { PageHeader } from '../components/PageHeader/PageHeader';
import { PersonRow } from '../components/PersonRow/PersonRow';
import { SearchField } from '../components/SearchField/SearchField';
import { SegmentedControlItem } from '../components/SegmentedControl/SegmentedControlItem';
import { Select } from '../components/Select/Select';
import { SettingsNavItem } from '../components/SettingsNav/SettingsNavItem';
import { StatusDot } from '../components/StatusDot/StatusDot';
import { StepMarker } from '../components/StepMarker/StepMarker';
import { Switch } from '../components/Switch/Switch';
import { Tab } from '../components/Tabs/Tab';
import { Tag } from '../components/Tag/Tag';
import { TextField } from '../components/TextField/TextField';
import { ApprovalCard, type ApprovalRequest } from '../features/approvals/ApprovalCard';
import { GoogleContinueCard } from '../features/auth/GoogleContinueCard';
import { PlaybookCard } from '../features/automations/PlaybookCard';
import { RunEstimateCard } from '../features/automations/RunEstimateCard';
import { ScheduleField } from '../features/automations/ScheduleField';
import { ExpertPreviewCard } from '../features/experts/ExpertPreviewCard';
import { CapabilityRow } from '../features/extensions/CapabilityRow';
import { DocumentPreview } from '../features/knowledge/DocumentPreview';
import { ContentReviewCard } from '../features/review/ContentReviewCard';
import { SearchResultRow } from '../features/search/SearchResultRow';
import { SeatSummaryBar } from '../features/settings/SeatSummaryBar';
import { ArtifactRow } from '../features/task/ArtifactRow';
import { ChecklistItem } from '../features/task/ChecklistItem';
import { DeliveryReviewCard } from '../features/task/DeliveryReviewCard';
import { MessageItem } from '../features/task/MessageItem';
import { PlanStepRow } from '../features/task/PlanStepRow';
import { RunSummary } from '../features/task/RunSummary';
import { SuggestionChip } from '../features/task/SuggestionChip';
import { TaskBriefCard } from '../features/task/TaskBriefCard';
import { TaskHeaderBar } from '../features/task/TaskHeaderBar';
import { TaskListRow } from '../features/tasks/TaskListRow';

/*
 * Gallery-only adapters: component-map.csv describes each Figma master with designer-level props
 * (titles instead of ids, counts instead of lists). These turn them into real component props using
 * the sample copy drawn in the master itself. Nothing here is used by screens.
 */

type P = Record<string, any>;
const noop = () => undefined;
const PEOPLE = ['望野', '安界', '立言', '清账', '知量'];
const person = (name: string) => ({ alt: name, initials: name.slice(0, 1) });
const members = (n: unknown) => PEOPLE.slice(0, typeof n === 'number' ? n : 0).map(person);

const SIDEBAR: SidebarWorkspace[] = [
  { id: 'ws-de', name: '德国市场进入', kind: 'workspace', pinned: true, taskCount: 6, tasks: [
    { id: 't-de-1', title: '德国站竞品格局调研' }, { id: 't-de-2', title: '德国 EPR 与 WEEE 合规清单' }, { id: 't-de-3', title: 'Amazon.de 关键词与类目机会' }] },
  { id: 'ws-web', name: '品牌官网改版', kind: 'workspace', pinned: false, taskCount: 3, tasks: [
    { id: 't-web-1', title: '首页文案 A/B 方案' }, { id: 't-web-2', title: '官网 SEO 关键词梳理' }, { id: 't-web-3', title: '团队页头像替换' }] },
  { id: 'ws-bf', name: '黑五大促', kind: 'workspace', pinned: false, taskCount: 2, tasks: [
    { id: 't-bf-1', title: '黑五库存预警规则' }, { id: 't-bf-2', title: '促销组合与定价测算' }] },
  { id: 'ws-none', name: '未关联工作区', kind: 'unassigned', pinned: false, taskCount: 1, tasks: [{ id: 't-none-1', title: '整理本周 Amazon 差评' }] },
];

function sidebar(p: P) {
  const dots = new Set<string>(p.taskDots ?? []);
  const workspaces = SIDEBAR.map((w) => ({ ...w, tasks: w.tasks.map((t) => (dots.has(t.title) ? { ...t, dot: 'accent' as const } : t)) }));
  const activeTask = workspaces.flatMap((w) => w.tasks).find((t) => t.title === p.activeTask);
  const activeWs = workspaces.find((w) => w.name === p.activeWorkspace);
  return (
    <AppSidebar
      activeNav={p.activeNav}
      {...(activeTask ? { activeTaskId: activeTask.id } : {})}
      {...(activeWs ? { activeWorkspaceId: activeWs.id } : {})}
      counts={p.counts ?? {}}
      workspaces={workspaces}
      user={{ name: 'Lillian Li', initials: 'L' }}
      updateAvailable
    />
  );
}

const APPROVALS: Record<string, ApprovalRequest> = {};
function approval(title: string, details: Array<[string, string]>, statusLabel?: string): ApprovalRequest {
  return { title, details: details.map(([label, value]) => ({ label, value })), ...(statusLabel ? { statusLabel } : {}) };
}
const JS50 = '望野想用 Jungle Scout 读取 Amazon.de 小家电类目前 50 名竞品的销量估算。';
const JS20 = '望野想用 Jungle Scout 读取 Amazon.de 小家电类目前 20 名竞品的销量估算。';
const BASE: Array<[string, string]> = [['动作', '只读查询，不写入任何外部系统']];
APPROVALS['3378:34465'] = approval(JS50, [...BASE, ['范围', 'Amazon.de · 小家电类目 · 前 50 名'], ['预计消耗', '约 40 Credits'], ['有效期', '仅本次任务']]);
APPROVALS['3378:39406'] = approval(JS50, [...BASE, ['范围', 'Amazon.de · 小家电类目 · 前 50 名'], ['预计消耗', '约 40 Credits'], ['有效期', '仅本次任务']]);
APPROVALS['3378:38613'] = approval(JS20, [...BASE, ['范围', 'Amazon.de · 小家电类目 · 前 20 名'], ['预计消耗', '待重新估算'], ['有效期', '仅本次任务']]);
APPROVALS['3378:38773'] = approval(JS20, [...BASE, ['范围', 'Amazon.de · 小家电类目 · 前 20 名'], ['预计消耗', '约 20 Credits · 计入企业额度'], ['有效期', '仅本次任务']]);
const INITIATOR: [string, string] = ['发起', '望野（主责专家）· 任务负责人 Lillian Li'];
APPROVALS['3378:38036'] = approval(JS20, [INITIATOR, ...BASE, ['范围', 'Amazon.de · 小家电类目 · 前 20 名'], ['预计消耗', '待重新估算'], ['有效期', '仅本次任务']]);
APPROVALS['3378:38431'] = approval(JS20, [INITIATOR, ...BASE, ['范围', 'Amazon.de · 小家电类目 · 前 20 名'], ['预计消耗', '约 20 Credits · 计入企业额度'], ['有效期', '仅本次任务']]);
APPROVALS['3378:38929'] = approval('使用 Keepa 读取历史价格（只读）。', [['发起', '示例发起人'], ['动作', '读取历史价格（只读）'], ['范围', 'Amazon.fr · 家用小家电 · 虚构商品 A、B、C · 最近 30 天'], ['预计消耗', '12 Credits（虚构示例）'], ['有效期', '仅本次示例运行']], '批准处理中 · 虚构测试示例');
APPROVALS['3378:39888'] = approval('使用 Keepa 读取历史价格（只读）。', [['发起', '待确认'], ['动作', '读取历史价格（只读）'], ['范围', '待确认'], ['预计消耗', '待确认'], ['有效期', '待确认']], '处理中');

const DELIVERY = [
  { checked: true, label: '覆盖前 20 名竞品，含价格带与差评集中点', evidence: '报告 §2 · 数据表' },
  { checked: true, label: '每个数字注明来源与数据日期', evidence: '报告附录 A' },
  { checked: true, label: '列出上架前必须完成的合规事项与周期', evidence: '报告 §3' },
  { checked: true, label: '给出“进入 / 暂缓”结论及 3 条依据', evidence: '报告 §1' },
  { checked: true, label: '关键数字由清账复核', evidence: '复核记录' },
];

const tag = (label: unknown, tone: any = 'neutral', size: any = 22, icon?: any) =>
  typeof label === 'string' ? { label, tone, size, ...(icon ? { icon } : {}) } : undefined;

export const renderers: Record<string, (p: P, key: string) => ReactNode> = {
  ActionBar: (p) => (
    <ActionBar
      variant={p.variant}
      hint={p.hint}
      actions={p.actions ?? []}
      {...(p.extraActions ? { extraActions: p.extraActions.map((label: string) => ({ label })) } : {})}
      {...(p.secondaryLink ? { secondaryLink: { label: p.secondaryLink } } : {})}
    />
  ),
  AppSidebar: sidebar,
  ApprovalCard: (p, key) => {
    const request = APPROVALS[key] ?? approval(p.title ?? '', []);
    const estimatePending = request.details.some((d) => d.value === '待重新估算');
    return <ApprovalCard status={p.status} layout={p.layout} request={request} estimatePending={estimatePending} onEstimate={noop} />;
  },
  ArtifactRow: (p) => <ArtifactRow {...(p as any)} />,
  Avatar: (p) => <Avatar size={p.size} alt={p.alt ?? ''} initials={p.initials} {...(p.src ? { src: p.src } : {})} />,
  BackLink: (p) => <BackLink label={p.label} size={p.size} onBack={noop} />,
  Button: (p) => <Button {...(p as any)} />,
  CapabilityRow: (p) => <CapabilityRow {...(p as any)} />,
  CardHeader: (p) => <CardHeader icon={p.icon} title={p.title} subtitle={p.subtitle} {...(p.tag ? { tag: tag(p.tag, 'warning', 22)! } : {})} {...(p.actions ? { actions: p.actions } : {})} />,
  ChecklistItem: (p) => <ChecklistItem {...(p as any)} />,
  ChoiceChip: (p) => <ChoiceChip label={p.label} selected={p.selected === true} size={p.size} {...(p.dotColor ? { dotColor: p.dotColor } : {})} />,
  ChoiceChipGroup: (p) => <ChoiceChipGroup {...(p as any)} />,
  Composer: (p) => (
    <Composer
      placeholder={p.placeholder}
      value={p.value}
      tools={p.tools ?? []}
      canSend={!!p.canSend}
      {...(p.contextTag ? { contextTag: { label: p.contextTag, tone: 'neutral', size: 26, icon: 'folder', removable: true } } : {})}
    />
  ),
  ComposerContextBar: (p) => <ComposerContextBar {...(p as any)} />,
  ConfirmDialog: (p) => <ConfirmDialog {...(p as any)} inline />,
  ContentReviewCard: () => (
    <ContentReviewCard
      subtitle="X1 Listing DE v2 · 主图 · Amazon.de · 德国"
      issueCount={3}
      dimensions={[{ name: '平台政策', issues: 1 }, { name: '目标市场法规', issues: 1 }, { name: '品牌规范', issues: 1 }, { name: '事实与数据', issues: 0 }]}
      issues={[
        { id: 'i1', severity: '高', title: '健康功效宣称超出允许范围', original: '标题：Luftreiniger X1 – tötet 99,9 % aller Viren …', suggestion: 'Luftreiniger X1 – filtert bis zu 99,9 % der Partikel ab 0,3 µm（经第三方检测）', basis: '德国《反不正当竞争法》（UWG）§5；Amazon 受限产品政策 · 医疗宣称' },
        { id: 'i2', severity: '中', title: '品牌规范：对外内容应使用正式称谓“Sie”', original: '五点描述第 2 条：Du wirst die frische Luft lieben …', suggestion: 'Sie werden die frische Luft lieben …', basis: '工作区“德国市场进入”规则 v3 第 1 条' },
        { id: 'i3', severity: '中', title: '主图右上角含促销文字', original: '', suggestion: '', basis: '' },
      ]}
    />
  ),
  CountBadge: (p) => <CountBadge {...(p as any)} />,
  DeliveryReviewCard: () => <DeliveryReviewCard subtitle="验收清单 5/5 已满足 · 德国站竞品格局报告 v2" checklist={DELIVERY} />,
  DocumentPreview: () => (
    <DocumentPreview
      doc={{
        title: '德国 EPR 法规摘要（2026 版）',
        paragraphs: [
          '1. 适用范围：在德国销售电子电气设备的生产者、进口商与跨境卖家，均需在 stiftung ear 完成 WEEE 注册，注册号须展示在商品页。',
          '2. 包装法（VerpackG）：向德国终端消费者销售带包装商品前，须在 LUCID 系统注册并加入双元回收体系。',
          '3. 平台责任：自 2023 年起，Amazon.de 有义务核验卖家的 EPR 注册号，缺少注册号的商品会被下架。',
          '4. 办理周期：WEEE 注册一般需要 4–6 周，需准备授权代表协议、产品类别与预计年销量。',
        ],
        page: 1,
        pageCount: 6,
      }}
      highlights={['WEEE 注册', '缺少注册号的商品会被下架']}
    />
  ),
  ExpertPreviewCard: () => <ExpertPreviewCard expert={{ name: '德语内容官', specialty: '德国市场内容本地化与合规审校', tags: ['德语本地化', '宣称合规', '人工确认点'] }} />,
  GoogleContinueCard: () => <GoogleContinueCard onSSO={noop} />,
  HeaderMemberActions: (p) => <HeaderMemberActions members={members(p.members)} onInvite={noop} onMore={noop} />,
  Icon: (p) => <Icon name={p.name} size={p.size} {...(p.color ? { color: p.color } : {})} />,
  IconButton: (p) =>
    // The map names the send button's glyph 'arrow-up'; Composer draws that master from the rotated arrow-right glyph.
    p.icon === 'arrow-up' ? (
      <IconButton {...(p as any)} icon="arrow-right" className="sg-composer__send" label="发送" />
    ) : (
      <IconButton {...(p as any)} label={p.icon} />
    ),
  IconTile: (p) => <IconTile {...(p as any)} />,
  InlineNotice: (p) => <InlineNotice {...(p as any)} />,
  Kbd: (p) => <Kbd keys={p.keys} />,
  MenuItem: (p) => <MenuItem {...(p as any)} />,
  MessageItem: (p) => (
    <MessageItem
      role={p.role}
      author={p.author}
      avatar={person(p.author ?? '')}
      time={p.time}
      body={p.body}
      leadTag={p.leadTag}
      subtitle={p.subtitle}
      {...(p.attachments ? { attachments: p.attachments } : {})}
    />
  ),
  PageHeader: (p) => (
    <PageHeader
      title={p.title}
      description={p.description}
      actions={(p.actions ?? []).map((label: string) => <Button key={label} variant="primary" size={32} icon="plus" label={label} />)}
    />
  ),
  PersonRow: (p) => <PersonRow name={p.name} subtitle={p.subtitle} size={p.size} avatar={person(p.name ?? '')} />,
  PlanStepRow: (p) => (
    <PlanStepRow
      layout={p.layout}
      status={p.status}
      title={p.title}
      meta={p.meta}
      credits={p.credits}
      duration={p.duration}
      {...(p.expert ? { expert: person(p.expert) } : {})}
      {...(p.variable ? { variable: tag(p.variable, 'info', 20)! } : {})}
    />
  ),
  PlaybookCard: (p) => <PlaybookCard playbook={{ name: p.name, meta: p.meta }} variables={(p.variables ?? []).map((v: string) => tag(v, 'info', 22)!)} />,
  RunEstimateCard: (p) => <RunEstimateCard {...(p as any)} />,
  RunSummary: (p) => <RunSummary {...(p as any)} />,
  ScheduleField: (p) => <ScheduleField frequency={p.frequency} day={p.time} time="09:00" />,
  SearchField: (p) => <SearchField {...(p as any)} />,
  SearchResultRow: (p) => <SearchResultRow {...(p as any)} />,
  SeatSummaryBar: (p) => <SeatSummaryBar purchased={p.purchased} assigned={p.assigned} invited={1} locked={0} />,
  SegmentedControlItem: (p) => <SegmentedControlItem {...(p as any)} />,
  Select: (p) => (
    <Select
      size={p.size}
      width={p.width}
      value={p.value}
      placeholder={p.placeholder}
      {...(p.leadingAvatar ? { leadingAvatar: { size: 20, ...person(String(p.value ?? '')) } } : {})}
    />
  ),
  SettingsNavItem: (p) => <SettingsNavItem {...(p as any)} />,
  SidebarAccountBar: (p) => <SidebarAccountBar user={p.user} />,
  SidebarNavItem: (p) => <SidebarNavItem {...(p as any)} />,
  SidebarShowMore: (p) => <SidebarShowMore {...(p as any)} />,
  SidebarTaskItem: (p) => <SidebarTaskItem {...(p as any)} />,
  SidebarWorkspaceItem: (p) => <SidebarWorkspaceItem {...(p as any)} />,
  StatusDot: (p) => <StatusDot {...(p as any)} />,
  StepMarker: (p) => <StepMarker {...(p as any)} />,
  SuggestionChip: (p) => <SuggestionChip {...(p as any)} />,
  Switch: (p) => <Switch checked={!!p.checked} label="开关" />,
  Tab: (p) => <Tab {...(p as any)} />,
  Tag: (p) => <Tag {...(p as any)} />,
  TaskBriefCard: () => (
    <TaskBriefCard
      brief={{
        subtitle: '复杂任务 · 预计 5 位专家 · 约 25 分钟',
        questions: [
          { question: '重点评估哪个渠道？', options: [], value: '' },
          { question: '交付成什么形式？', options: ['一页结论', '完整报告 + 数据表', '由 Sage 决定'], value: '完整报告 + 数据表' },
        ],
        description: '',
        criteria: ['覆盖前 20 名竞品与价格带', '数字注明来源与日期', '列出上架前的合规事项', '给出进入或暂缓的结论', '关键数字由清账复核'],
        capabilities: [
          { icon: 'zap', name: '销量估算', kind: '技能', action: '安装' },
          { icon: 'globe', name: 'Jungle Scout', kind: '连接器 · 只读', action: '连接' },
        ],
        planSummary: '执行计划 · 8 步 · 5 位专家（展开查看与调整）',
        hint: '不安装也可以开始，相关步骤会标为“缺少能力”',
      }}
    />
  ),
  TaskHeaderBar: (p) => (
    <TaskHeaderBar
      title={p.title}
      status={p.status}
      members={members(p.members)}
      actions={p.actions ?? []}
      {...(p.statusTag ? { statusTag: tag(p.statusTag, 'warning', 22)! } : {})}
      {...(p.workspaceTag ? { workspaceTag: tag(p.workspaceTag, 'neutral', 22, 'folder')! } : {})}
    />
  ),
  TaskListRow: (p) => <TaskListRow {...(p as any)} />,
  TextField: (p) => <TextField {...(p as any)} />,
  WindowControl: (p) => <WindowControl kind={p.kind} />,
};
