import './SettingsNav.css';

export interface SettingsNavItemProps {
  label: string;
  active: boolean;
  onSelect?: () => void;
  href?: string;
}

export function SettingsNavItem({ label, active, onSelect, href }: SettingsNavItemProps) {
  const cls = `sg-settingsnav${active ? ' is-active' : ''}`;
  if (href)
    return (
      <a className={cls} href={href} aria-current={active ? 'page' : undefined} onClick={onSelect}>
        {label}
      </a>
    );
  return (
    <button type="button" className={cls} aria-current={active ? 'page' : undefined} onClick={onSelect}>
      {label}
    </button>
  );
}
