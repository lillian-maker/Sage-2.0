import { useState, type ReactNode } from 'react';
import { navigate } from '../../app/router';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { NotificationBell } from '../../components/NotificationBell/NotificationBell';
import { Menu, MenuItem, Popover } from '../../components/Menu/MenuItem';
import type { ExpertItem } from './api';
import './frame.css';

/** Main column with the 52px top bar (notifications bell) and an optional full-height right rail. */
export function DPage({ children, rail, railWidth = 360, className }: { children: ReactNode; rail?: ReactNode; railWidth?: number; className?: string }) {
  const bell = <NotificationBell onClick={() => navigate('/notifications')} />;
  if (!rail)
    return (
      <div className={`sg-dpage${className ? ' ' + className : ''}`}>
        <div className="sg-dpage__top">{bell}</div>
        <div className="sg-dpage__body">{children}</div>
      </div>
    );
  return (
    <div className={`sg-dpage has-rail${className ? ' ' + className : ''}`} style={{ gridTemplateColumns: `minmax(0, 1fr) ${railWidth}px` }}>
      <div className="sg-dpage__col">
        <div className="sg-dpage__top" />
        <div className="sg-dpage__body">{children}</div>
      </div>
      <aside className="sg-dpage__rail">
        <div className="sg-dpage__top">{bell}</div>
        <div className="sg-dpage__railbody">{rail}</div>
      </aside>
    </div>
  );
}

export function SectionHead({ title, count, right }: { title: string; count?: string; right?: ReactNode }) {
  return (
    <div className="sg-dsection">
      <span className="sg-dsection__title">{title}</span>
      {count && <span className="sg-dsection__count">{count}</span>}
      {right && <span className="sg-dsection__right">{right}</span>}
    </div>
  );
}

export function MutedLabel({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="sg-dlabel">
      <span>{children}</span>
      {right && <span className="sg-dlabel__right">{right}</span>}
    </div>
  );
}

export function KvRow({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="sg-dkv">
      <span className="sg-dkv__k">{k}</span>
      <span className="sg-dkv__v">{children}</span>
    </div>
  );
}

/** Text trigger with a chevron that opens a single-choice menu (领域：全部 ⌄, 范围：全部可访问 ⌄). */
export function TextMenu({ prefix, value, options, onChange }: { prefix: string; value: string; options: Array<{ value: string; label: string; hint?: string }>; onChange: (v: string) => void }) {
  const [at, setAt] = useState<{ x: number; y: number } | null>(null);
  const current = options.find((o) => o.value === value)?.label ?? '';
  return (
    <>
      <button
        type="button"
        className="sg-dtextmenu"
        aria-haspopup="menu"
        aria-expanded={!!at}
        onClick={(e) => {
          const r = e.currentTarget.getBoundingClientRect();
          setAt({ x: r.right - 200, y: r.bottom + 6 });
        }}
      >
        {prefix}
        {current}
        <Icon name="chevron-down" size={12} />
      </button>
      {at && (
        <Popover at={at} width={200} onClose={() => setAt(null)}>
          <Menu label={prefix}>
            {options.map((o) => (
              <MenuItem
                key={o.value}
                icon="dot"
                label={o.label}
                checked={o.value === value}
                {...(o.hint ? { hint: o.hint } : {})}
                onSelect={() => {
                  setAt(null);
                  onChange(o.value);
                }}
              />
            ))}
          </Menu>
        </Popover>
      )}
    </>
  );
}

export function chatWith(expertId: string) {
  navigate(`/tasks/new?expert=${encodeURIComponent(expertId)}`);
}

export function ExpertCard({ expert, onOpen, onChat, onTemplate, brief }: { expert: ExpertItem; onOpen?: () => void; onChat?: () => void; onTemplate?: () => void; brief?: boolean }) {
  const [menuAt, setMenuAt] = useState<{ x: number; y: number } | null>(null);
  const title = expert.title || expert.specialty || '';
  return (
    <article className={`sg-ecard${brief ? ' is-brief' : ''}`}>
      <button type="button" className="sg-ecard__hit" aria-label={`查看 ${expert.name}`} onClick={onOpen} />
      <div className="sg-ecard__head">
        <Avatar size={48} src={expert.avatar_ref} alt={expert.name} />
        <div className="sg-ecard__name">
          <b className="truncate">{expert.name}</b>
          <span className="truncate">{title}</span>
        </div>
        {!brief && (
          <span className="sg-ecard__more">
            <IconButton
              icon="ellipsis"
              size={25}
              variant="ghost"
              label="更多"
              onClick={(e) => {
                const r = e.currentTarget.getBoundingClientRect();
                setMenuAt({ x: r.right - 180, y: r.bottom + 4 });
              }}
            />
          </span>
        )}
      </div>
      <p className="sg-ecard__resp">{expert.responsibilities}</p>
      {expert.capability_tags.length > 0 && (
        <div className="sg-ecard__tags">
          {expert.capability_tags.map((t) => (
            <span key={t}>{t}</span>
          ))}
        </div>
      )}
      <div className="sg-ecard__foot">
        <span>{expert.status === 'draft' ? '草稿' : expert.assignable ? '可交办' : '暂不可交办'}</span>
        {!brief && onChat && <Button variant="secondary" size={28} label="对话" onClick={onChat} disabled={!expert.assignable} />}
      </div>
      {menuAt && (
        <Popover at={menuAt} width={180} onClose={() => setMenuAt(null)}>
          <Menu label="专家操作">
            <MenuItem icon="contact" label="查看资料" onSelect={() => navigate(`/experts/${encodeURIComponent(expert.id)}`)} />
            <MenuItem icon="users" label="协作网络" onSelect={() => navigate(`/experts/network?expert=${encodeURIComponent(expert.id)}`)} />
            {onTemplate && <MenuItem icon="plus" label="以此为模板新建" onSelect={onTemplate} />}
          </Menu>
        </Popover>
      )}
    </article>
  );
}

