import { useMemo, useState } from 'react';
import { useShell } from '../../app/AppShell';
import type { ScreenProps } from '../../app/router';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { IconButton } from '../../components/IconButton/IconButton';
import { Select } from '../../components/Select/Select';
import { TextField } from '../../components/TextField/TextField';
import { errorMessage, QueryGate } from '../../components/States/States';
import { allPages, expertsApi, type ExpertItem, type PersonRef } from '../../features/experts/api';

import { TeamCard } from '../../features/experts/TeamCard';
import '../../features/experts/frame.css';
import '../../features/experts/teams.css';
import { Modal } from '../../components/Modal/Modal';

const MAX_MEMBERS = 8;

export default function Screen({ navigate }: ScreenProps) {
  const { toast } = useShell();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [members, setMembers] = useState<PersonRef[]>([]);
  const [leadId, setLeadId] = useState<string | null>(null);
  const [visibility, setVisibility] = useState<'only_me' | 'account'>('only_me');
  const [filter, setFilter] = useState('');
  const [error, setError] = useState<string | null>(null);

  const backdrop = useApiQuery((signal) => expertsApi.teams(signal), []);
  const experts = useApiQuery(
    (signal) => allPages<ExpertItem>((offset) => expertsApi.list({ group: 'all', offset: offset || undefined }, signal) as Promise<never>),
    [],
  );

  const candidates = useMemo(() => {
    const picked = new Set(members.map((m) => m.id));
    const list = (experts.data ?? []).filter((e) => e.assignable && e.status === 'published' && !picked.has(e.id));
    const f = filter.trim();
    return (f ? list.filter((e) => e.name.includes(f) || (e.title || '').includes(f)) : list).slice(0, 8);
  }, [experts.data, members, filter]);

  const create = useSubmit(async () => {
    const res = await expertsApi.createTeam({
      name: name.trim(),
      ...(description.trim() ? { description: description.trim() } : {}),
      member_ids: members.map((m) => m.id),
      lead_expert_id: leadId ?? members[0]!.id,
      visibility,
    });
    return res.team;
  });

  const submit = async () => {
    setError(null);
    const team = await create.submit();
    if (team) {
      toast({ tone: 'undo', message: `已创建团队“${team.name}”` });
      navigate('/experts/teams');
    }
  };

  const invalid =
    !name.trim() || members.length < 2 ? (!name.trim() ? '' : '团队至少需要 2 位成员') : null;

  const add = (e: ExpertItem) => {
    if (members.length >= MAX_MEMBERS) return;
    const ref: PersonRef = { id: e.id, name: e.name, avatar_ref: e.avatar_ref };
    setMembers((m) => [...m, ref]);
    if (!leadId) setLeadId(e.id);
    setFilter('');
  };

  return (
    <>
      <div className="sg-dpage" aria-hidden="true">
        <div className="sg-dpage__body">
          <div className="sg-tgrid" style={{ marginTop: 22 }}>
            {(backdrop.data?.items ?? []).map((team) => (
              <TeamCard key={team.id} team={team} />
            ))}
          </div>
        </div>
      </div>
      <Modal className="sg-dmodal__card" label="新建团队" onClose={() => navigate('/experts/teams')}>
        <div className="sg-dmodal__head">
          <h2>新建团队</h2>
          <IconButton icon="x" size={28} variant="ghost" label="关闭" onClick={() => navigate('/experts/teams')} />
        </div>
        <p className="sg-dmodal__sub">团队决定“谁来做”；在输入框 @ 团队，就能整组交办</p>
        <div className="sg-dfield">
          <span className="sg-dfield__label">名称</span>
          <TextField size={32} value={name} placeholder="例如：新市场进入小组" onValueChange={setName} />
        </div>
        <div className="sg-dfield" style={{ position: 'relative' }}>
          <span className="sg-dfield__label">
            <span>
              成员 · {members.length} / {MAX_MEMBERS}（点头像设为默认主责）
            </span>
          </span>
          <div className="sg-members">
            {members.map((m) => (
              <span key={m.id} className={`sg-member${m.id === leadId ? ' is-lead' : ''}`}>
                <button type="button" className="sg-member" title="设为默认主责" onClick={() => setLeadId(m.id)}>
                  <Avatar size={20} src={m.avatar_ref} alt={m.name} />
                  {m.name}
                  {m.id === leadId && <small>· 主责</small>}
                </button>
                <IconButton
                  icon="x"
                  size={25}
                  iconSize={10}
                  variant="ghost"
                  label={`移除 ${m.name}`}
                  onClick={() => {
                    setMembers((list) => list.filter((x) => x.id !== m.id));
                    if (leadId === m.id) setLeadId(null);
                  }}
                />
              </span>
            ))}
            <input value={filter} placeholder="输入名字或 @ 添加…" aria-label="搜索专家" onChange={(e) => setFilter(e.target.value)} />
          </div>
          {filter.trim() && (
            <div className="sg-menu" style={{ position: 'absolute', top: '100%', left: 0, right: 0, zIndex: 5, background: 'var(--color-neutral-0)', boxShadow: 'var(--shadow-0_8_24-blue-840-a24)', borderRadius: 8 }}>
              {!experts.data ? (
                <QueryGate q={experts} label="专家" rows={2} />
              ) : candidates.length === 0 ? (
                <p className="sg-dmuted" style={{ padding: '8px 12px', margin: 0 }}>
                  没有匹配的专家
                </p>
              ) : (
                candidates.map((e) => (
                  <button key={e.id} type="button" className="sg-menuitem" onClick={() => add(e)}>
                    <Avatar size={20} src={e.avatar_ref} alt={e.name} />
                    <span className="truncate">{e.name}</span>
                    <span className="sg-menuitem__hint">{e.title || e.specialty || ''}</span>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
        <div className="sg-dfield">
          <span className="sg-dfield__label">说明（选填）</span>
          <TextField size={32} value={description} placeholder="这个团队一起完成什么" onValueChange={setDescription} />
        </div>
        <div className="sg-dfield">
          <span className="sg-dfield__label">可见范围</span>
          <Select
            size={32}
            width={220}
            value={visibility}
            options={[
              { value: 'only_me', label: '仅自己' },
              { value: 'account', label: '企业全员' },
            ]}
            onChange={(v) => setVisibility(v as 'only_me' | 'account')}
          />
        </div>
        {(error || invalid || create.error) && <span className="sg-dfield__error">{error ?? invalid ?? errorMessage(create.error, '创建失败，请重试')}</span>}
        <div className="sg-dmodal__foot">
          <span className="sg-dmodal__hint">
            <Icon name="info" size={12} /> 系统会按成员职责推荐分工，你仍可在编排视图中改派
          </span>
          <Button variant="ghost" size={32} label="取消" onClick={() => navigate('/experts/teams')} />
          <Button variant="primary" size={32} label="创建团队" busy={create.submitting} disabled={!name.trim() || members.length < 2} onClick={submit} />
        </div>
      </Modal>
    </>
  );
}
