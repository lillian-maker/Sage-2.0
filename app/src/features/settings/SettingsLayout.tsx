import type { ReactNode } from 'react';
import { navigate } from '../../app/router';
import { Icon } from '../../components/Icon/Icon';
import { SettingsNavItem } from '../../components/SettingsNav/SettingsNavItem';
import './settings.css';

export type SettingsKey =
  | 'profile'
  | 'appearance'
  | 'notifications'
  | 'memory'
  | 'shortcuts'
  | 'models'
  | 'extensions'
  | 'security'
  | 'org'
  | 'help';

const NAV: Array<{ key: SettingsKey; label: string; path: string }> = [
  { key: 'profile', label: '个人资料', path: '/settings/profile' },
  { key: 'appearance', label: '外观与语言', path: '/settings/appearance' },
  { key: 'notifications', label: '通知', path: '/settings/notifications' },
  { key: 'memory', label: '记忆', path: '/settings/memory' },
  { key: 'shortcuts', label: '快捷键', path: '/settings/shortcuts' },
  { key: 'models', label: '模型', path: '/settings/models' },
  { key: 'extensions', label: '扩展', path: '/settings/extensions' },
  { key: 'security', label: '安全', path: '/settings/security' },
  { key: 'org', label: '企业管理', path: '/settings/org/members' },
  { key: 'help', label: '帮助与诊断', path: '/settings/help' },
];

export type SaveStatus = 'idle' | 'saving' | 'saved' | 'error';

export function SaveIndicator({ status, message }: { status: SaveStatus; message?: string | null }) {
  if (status === 'idle') return null;
  if (status === 'saving')
    return (
      <span className="sg-saved is-saving" role="status">
        <Icon name="loader" size={12} className="sg-spin" />
        正在保存…
      </span>
    );
  if (status === 'error')
    return (
      <span className="sg-saved is-error" role="alert">
        {message || '保存失败，请重试'}
      </span>
    );
  return (
    <span className="sg-saved" role="status">
      <Icon name="check" size={12} />
      已保存
    </span>
  );
}

export interface SettingsLayoutProps {
  active: SettingsKey;
  title: string;
  save?: { status: SaveStatus; message?: string | null };
  /** Line under the title (企业管理 scope note). */
  subtitle?: ReactNode;
  /** Appearance uses the 920 wide content column; the other pages use 756. */
  wide?: boolean;
  children: ReactNode;
}

export function SettingsLayout({ active, title, save, subtitle, wide, children }: SettingsLayoutProps) {
  return (
    <div className="sg-settings">
      <nav className="sg-settings__nav" aria-label="设置">
        <div className="sg-settings__navtitle">设置</div>
        {NAV.map((item) => (
          <SettingsNavItem key={item.key} label={item.label} active={item.key === active} onSelect={() => navigate(item.path)} />
        ))}
      </nav>
      <div className={`sg-settings__content${wide ? ' is-wide' : ''}`}>
        <header className="sg-settings__header">
          <h1>{title}</h1>
          {save && <SaveIndicator status={save.status} message={save.message ?? null} />}
        </header>
        {subtitle && <div className="sg-settings__subtitle">{subtitle}</div>}
        {children}
      </div>
    </div>
  );
}

export function SettingsSection({
  title,
  description,
  actions,
  children,
}: {
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <section className="sg-settings__section">
      {(title || actions) && (
        <div className="sg-settings__sectionhead">
          <div>
            {title && <h2>{title}</h2>}
            {description && <p>{description}</p>}
          </div>
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function SettingsRow({
  label,
  description,
  children,
  muted,
}: {
  label: ReactNode;
  description?: ReactNode;
  children?: ReactNode;
  muted?: boolean;
}) {
  return (
    <div className={`sg-settings__row${muted ? ' is-muted' : ''}`}>
      <div className="sg-settings__rowtext">
        <div className="sg-settings__label">{label}</div>
        {description && <div className="sg-settings__desc">{description}</div>}
      </div>
      {children !== undefined && <div className="sg-settings__control">{children}</div>}
    </div>
  );
}
