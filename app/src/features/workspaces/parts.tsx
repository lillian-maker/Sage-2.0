import { useEffect, useMemo, useState } from 'react';
import { Link } from '../../app/router';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { Select } from '../../components/Select/Select';
import { Tab } from '../../components/Tabs/Tab';
import { Tag } from '../../components/Tag/Tag';
import { TextField } from '../../components/TextField/TextField';
import { avatarOf, apiMessage } from '../task/model';
import { taskState, timeAgo, wsApi, type CreatedFrom, type TaskItem, type WorkspaceDetail, type WorkspaceSummary } from './model';
import './workspaces.css';
import { Modal } from '../../components/Modal/Modal';

export type WorkspaceTabKey = 'tasks' | 'documents' | 'rules' | 'members';

export function WorkspaceTabs({
  ws,
  current,
  rulesLabel,
  memberCount,
  navigate,
}: {
  ws: WorkspaceDetail | undefined;
  current: WorkspaceTabKey;
  rulesLabel: string;
  /** The members page also counts pending invitations (Figma 18 “成员 4”); elsewhere only active members. */
  memberCount?: number;
  navigate: (to: string) => void;
}) {
  if (!ws) return <div className="sg-tabs" role="tablist" />;
  const base = `/workspaces/${encodeURIComponent(ws.id)}`;
  const tabs: Array<{ key: WorkspaceTabKey; label: string; to: string }> = [
    { key: 'tasks', label: `任务 ${ws.counts.tasks}`, to: base },
    { key: 'documents', label: '资料', to: `/knowledge?workspace=${encodeURIComponent(ws.id)}` },
    { key: 'rules', label: rulesLabel, to: `${base}/rules` },
    { key: 'members', label: `成员 ${memberCount ?? ws.counts.members}`, to: `${base}/members` },
  ];
  return (
    <div className="sg-tabs" role="tablist" aria-label="工作区">
      {tabs.map((t) => (
        <Tab key={t.key} size={37} label={t.label} selected={t.key === current} onSelect={() => navigate(t.to)} />
      ))}
    </div>
  );
}

export function StateLabel({ label, tone }: { label: string; tone: ReturnType<typeof taskState>['tone'] }) {
  if (tone === 'plain') return <span className="sg-wstask__plain">{label}</span>;
  return <Tag label={label} tone={tone} size={20} />;
}

export function WorkspaceTaskRow({ task, onOpen }: { task: TaskItem; onOpen: () => void }) {
  const st = taskState(task);
  const lead = task.lead_expert;
  const sub = [
    lead ? `主责 ${lead.name}` : null,
    lead?.specialty || null,
    task.counts.collaborators > 0 ? `协作 ${task.counts.collaborators} 位` : null,
  ]
    .filter(Boolean)
    .join(' · ');
  return (
    <button type="button" className="sg-wstask" onClick={onOpen}>
      <Icon
        name={st.icon}
        size={18}
        color={st.waiting ? 'color.orange.660' : st.tone === 'info' ? 'accent' : undefined}
        className={st.icon === 'loader-ring' ? 'sg-spin' : 'sg-wstask__icon'}
      />
      <span className="sg-wstask__text">
        <span className="sg-wstask__title truncate">{task.title}</span>
        <span className="sg-wstask__sub">
          {lead && <Avatar size={16} {...avatarOf(lead.name, lead.avatar_ref)} />}
          <span className="truncate">{sub || '尚未指派主责'}</span>
        </span>
      </span>
      <span className="sg-wstask__state">
        <StateLabel label={st.label} tone={st.tone} />
      </span>
      <span className="sg-wstask__time">{timeAgo(task.last_activity_at)}</span>
      <span className={`sg-wstask__dot${task.unread ? ' is-on' : ''}`} aria-label={task.unread ? '有未读更新' : undefined} />
    </button>
  );
}

interface StartOption {
  key: CreatedFrom;
  title: string;
  desc: string;
}

/** 19 新建工作区 form; source workspace / playbook are user-selectable (Q-F-12). */
export function CreateWorkspaceDialog({
  workspaces,
  onCancel,
  onCreated,
}: {
  workspaces: WorkspaceSummary[];
  onCancel: () => void;
  onCreated: (workspaceId: string) => void;
}) {
  const playbooks = useApiQuery((s) => wsApi.playbooks(s), []);
  const withRules = useMemo(() => workspaces.filter((w) => w.current_rule_version), [workspaces]);
  const [name, setName] = useState('');
  const [from, setFrom] = useState<CreatedFrom>('blank');
  const [sourceWs, setSourceWs] = useState('');
  const [sourcePb, setSourcePb] = useState('');
  const [touched, setTouched] = useState(false);

  useEffect(() => {
    if (!sourceWs && withRules[0]) setSourceWs(withRules[0].id);
  }, [withRules, sourceWs]);
  useEffect(() => {
    const first = playbooks.data?.items[0];
    if (!sourcePb && first) setSourcePb(first.id);
  }, [playbooks.data, sourcePb]);

  const srcWs = withRules.find((w) => w.id === sourceWs);
  const srcPb = playbooks.data?.items.find((p) => p.id === sourcePb);
  const options: StartOption[] = [
    { key: 'blank', title: '空白', desc: '只建容器，稍后再添加' },
    {
      key: 'copy_rules',
      title: '复制规则',
      desc: srcWs ? `沿用“${srcWs.name}”的规则 v${srcWs.current_rule_version!.number}` : '还没有带规则的工作区',
    },
    {
      key: 'playbook',
      title: '用 Playbook',
      desc: srcPb ? `按“${srcPb.name}”建好首个任务` : playbooks.status === 'loading' ? '正在读取 Playbook…' : '还没有 Playbook',
    },
  ];

  const trimmed = name.trim();
  const nameError = touched && !trimmed ? '请填写名称' : trimmed.length > 40 ? '名称不超过 40 个字' : '';
  const sourceMissing = (from === 'copy_rules' && !srcWs) || (from === 'playbook' && !srcPb);

  const create = useSubmit(async () => {
    const r = await wsApi.create({
      name: trimmed,
      created_from: from,
      ...(from === 'copy_rules' ? { source_workspace: sourceWs } : {}),
      ...(from === 'playbook' ? { source_playbook: sourcePb } : {}),
    });
    onCreated(r.workspace.id);
  });

  const submit = () => {
    setTouched(true);
    if (!trimmed || trimmed.length > 40 || sourceMissing) return;
    void create.submit();
  };

  return (
    <Modal
      className="sg-wsdialog"
      labelledBy="sg-wsdialog-title"
      onClose={onCancel}
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
    >
        <div className="sg-wsdialog__head">
          <h2 id="sg-wsdialog-title">新建工作区</h2>
          <p>工作区是任务、资料、成员和规则的容器；独立任务也可以稍后移入</p>
        </div>
        <label className="sg-wsdialog__field">
          <span className="sg-wsdialog__label">名称</span>
          <TextField size={32} value={name} placeholder="例如：法国市场进入" maxLength={40} invalid={!!nameError} autoFocus onValueChange={setName} />
          {nameError && <span className="sg-wsdialog__error">{nameError}</span>}
        </label>
        <div className="sg-wsdialog__field">
          <span className="sg-wsdialog__label">从哪里开始</span>
          <div className="sg-wsdialog__options" role="radiogroup" aria-label="从哪里开始">
            {options.map((o) => (
              <button
                key={o.key}
                type="button"
                role="radio"
                aria-checked={from === o.key}
                className={`sg-wsdialog__option${from === o.key ? ' is-selected' : ''}`}
                onClick={() => setFrom(o.key)}
              >
                <span className="sg-wsdialog__optiontitle">{o.title}</span>
                <span className="sg-wsdialog__optiondesc">{o.desc}</span>
              </button>
            ))}
          </div>
          {from === 'copy_rules' && withRules.length > 1 && (
            <Select
              size={32}
              label="复制哪个工作区的规则"
              value={sourceWs}
              options={withRules.map((w) => ({ value: w.id, label: `${w.name} · 规则 v${w.current_rule_version!.number}` }))}
              onChange={setSourceWs}
            />
          )}
          {from === 'playbook' && (playbooks.data?.items.length ?? 0) > 1 && (
            <Select
              size={32}
              label="使用哪个 Playbook"
              value={sourcePb}
              options={(playbooks.data?.items ?? []).map((p) => ({ value: p.id, label: p.name }))}
              onChange={setSourcePb}
            />
          )}
        </div>
        <div className="sg-wsdialog__field">
          <span className="sg-wsdialog__label">成员</span>
          <TextField size={32} value="" placeholder="只有你 · 创建后可以邀请" disabled />
        </div>
        {create.error && <div className="sg-wsdialog__error" role="alert">{apiMessage(create.error, '创建失败，请重试')}</div>}
        <div className="sg-wsdialog__foot">
          <span className="sg-wsdialog__note">资料与授权都只在本工作区内生效</span>
          <Button variant="ghost" size={32} label="取消" onClick={onCancel} />
          <Button type="submit" variant="primary" size={32} label="创建" busy={create.submitting} disabled={sourceMissing} />
        </div>
    </Modal>
  );
}

export function SectionTitle({ title, action }: { title: string; action?: { label: string; href?: string; onClick?: () => void; icon?: 'plus' } }) {
  return (
    <div className="sg-wsside__title">
      <span>{title}</span>
      {action &&
        (action.href ? (
          <Link className="sg-wsside__action" href={action.href}>
            {action.icon && <Icon name={action.icon} size={12} />}
            {action.label}
          </Link>
        ) : (
          <button type="button" className="sg-wsside__action" onClick={action.onClick}>
            {action.icon && <Icon name={action.icon} size={12} />}
            {action.label}
          </button>
        ))}
    </div>
  );
}
