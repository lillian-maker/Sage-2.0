import type { ScreenProps } from '../../app/router';
import { Button } from '../../components/Button/Button';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Kbd } from '../../components/Kbd/Kbd';
import { SettingsLayout } from '../../features/settings/SettingsLayout';

type Shortcut = { label: string; keys: string[] };
const GROUPS: Array<{ title: string; items: Shortcut[] }> = [
  {
    title: '全局',
    items: [
      { label: '新任务', keys: ['⌘', 'N'] },
      { label: '搜索', keys: ['⌘', 'K'] },
      { label: '显示 / 隐藏侧栏', keys: ['⌘', '\\'] },
      { label: '打开设置', keys: ['⌘', ','] },
      { label: '查看快捷键', keys: ['⌘', '/'] },
    ],
  },
  {
    title: '输入框',
    items: [
      { label: '发送任务', keys: ['Enter'] },
      { label: '换行', keys: ['⇧', 'Enter'] },
      { label: '选择专家', keys: ['@'] },
      { label: '引用资料', keys: ['#'] },
      { label: '选择技能', keys: ['/'] },
    ],
  },
  {
    title: '任务',
    items: [
      { label: '切换编排视图', keys: ['⌘', '⇧', 'G'] },
      { label: '从当前消息分叉', keys: ['⌘', '⇧', 'F'] },
      { label: '关闭浮层 / 抽屉', keys: ['Esc'] },
    ],
  },
  {
    title: '收件',
    items: [
      { label: '交给专家处理', keys: ['E'] },
      { label: '忽略', keys: ['#'] },
    ],
  },
];

/**
 * 71 设置 · 快捷键 (675:2). The shortcut preference store is not built on the server yet, so the
 * page lists the defaults read-only; editing and 恢复默认 stay disabled until it exists.
 */
export default function Screen(_props: ScreenProps) {
  return (
    <SettingsLayout active="shortcuts" title="快捷键">
      <p className="sg-settings__lead">悬停一行可以修改快捷键；与系统或其他快捷键冲突时会提示，不会覆盖。输入法组字过程中，@ # / 与 Enter 不会触发。</p>
      <InlineNotice icon="info" tone="muted" text="自定义快捷键暂未开放，当前显示的是默认快捷键。" />
      <div className="sg-shortcuts">
        {[0, 1].map((col) => (
          <div className="sg-shortcuts__col" key={col}>
            {GROUPS.filter((_, i) => i % 2 === col).map((g) => (
              <section className="sg-shortcuts__group" key={g.title}>
                <h2>{g.title}</h2>
                {g.items.map((s) => (
                  <div className="sg-shortcuts__row" key={s.label}>
                    <span>{s.label}</span>
                    <span className="sg-shortcuts__keys">
                      {s.keys.map((k, i) => (
                        <Kbd key={i} keys={k} />
                      ))}
                    </span>
                  </div>
                ))}
              </section>
            ))}
          </div>
        ))}
      </div>
      <div className="sg-shortcuts__reset">
        <Button variant="secondary" size={32} label="恢复默认" disabled title="自定义快捷键暂未开放" />
      </div>
    </SettingsLayout>
  );
}
