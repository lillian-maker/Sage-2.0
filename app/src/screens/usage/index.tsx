import type { ScreenProps } from '../../app/router';
import { SidebarAccountBar } from '../../components/AppSidebar/SidebarAccountBar';
import { StatusDot } from '../../components/StatusDot/StatusDot';
import { Tag } from '../../components/Tag/Tag';
import { fmtCredits, initialsOf } from '../../features/billing/format';
import { UsagePopover, useUsageSnapshot, type UsageState } from '../../features/billing/UsagePopover';
import { activeAccount, useSession } from '../../features/settings/common';
import '../../features/billing/billing.css';

const STATES: Array<{ key: UsageState; label: string; note: string; dot?: 'color.orange.660' | 'color.red.500' }> = [
  { key: 'normal', label: '正常', note: '侧栏底部只放一个图标按钮，不常驻展开。悬停显示“剩余与重置日期”。' },
  { key: 'low', label: '偏低（低于 10%）', note: '按钮右上角出现警告点；输入框下方的状态行同步提示一次，不弹窗、不加横幅。', dot: 'color.orange.660' },
  { key: 'exhausted', label: '已用尽', note: '错误点；发送时在输入框下方说明并保留草稿；补足后由用户手动继续。', dot: 'color.red.500' },
  { key: 'unavailable', label: '读数不可用', note: '不显示数字，也不显示 0；悬停说明原因，浮层内显示“用量暂不可用”。' },
];

/** 62 额度用量 (632:2): states of the sidebar quota button and its popover, filled from the live ledger. */
export default function Screen({ navigate }: ScreenProps) {
  const session = useSession();
  const snapshot = useUsageSnapshot(activeAccount(session)?.id ?? '');
  const user = session?.user;
  const bar = (
    <SidebarAccountBar
      user={{ name: user?.display_name ?? '—', initials: initialsOf(user?.display_name), avatarUrl: user?.avatar_url ?? null }}
      onUsage={() => navigate('/billing')}
      onSettings={() => navigate('/settings/profile')}
    />
  );
  return (
    <div className="sg-usageboard">
      <h1>侧栏额度按钮 · 状态与浮层</h1>
      <p>额度入口是侧栏底部头像行的图标按钮（位于“手机 App”“设置”左侧），点击或悬停展开浮层；数值来自账务服务。</p>
      <div className="sg-usageboard__states">
        {STATES.map((s) => (
          <div className="sg-usageboard__state" key={s.key}>
            <div className="sg-usageboard__label">
              {s.dot && <StatusDot color={s.dot} size={8} />}
              {s.label}
              {snapshot.state === s.key && !snapshot.loading && <Tag label="当前" tone="info" size={18} />}
            </div>
            <div className="sg-usageboard__bar" title={snapshot.state === s.key ? snapshot.tooltip : undefined}>
              {bar}
            </div>
            <p className="sg-usageboard__note">{s.note}</p>
          </div>
        ))}
      </div>
      <div className="sg-usageboard__row2">
        <div className="sg-usageboard__state">
          <div className="sg-usageboard__label">点击或键盘聚焦后（按钮为按下态）</div>
          <div className="sg-usageboard__bar">{bar}</div>
          <p className="sg-usageboard__note">
            点击或键盘聚焦额度按钮后，在按钮上方弹出浮层（左边与侧栏对齐，底边距按钮 8px）。按钮悬停只显示“剩余与重置日期”的提示：{snapshot.tooltip}。Esc 关闭并把焦点还给按钮。
          </p>
        </div>
        <UsagePopover snapshot={snapshot} />
        <div>
          <div className="sg-usageboard__label">Credits 偏低时，输入舱下的状态行</div>
          <div className="sg-usageboard__lowline">
            <span>Credits 偏低，剩余 {snapshot.remaining === null ? '—' : fmtCredits(snapshot.remaining)}，可能不足以完成较大的调研任务 ·</span>
            <button type="button" className="sg-bill__link sg-bill__link--small" onClick={() => navigate('/billing/purchase')}>
              加购
            </button>
          </div>
          <p className="sg-usageboard__note" style={{ paddingTop: 10 }}>
            同一时刻只在这一处提示；用户关闭后本期不再重复。
          </p>
        </div>
      </div>
    </div>
  );
}
