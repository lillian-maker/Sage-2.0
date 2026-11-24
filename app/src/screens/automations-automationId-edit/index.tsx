import { useEffect, useMemo, useState } from 'react';
import { currentSession } from '../../api/client';
import { experts, workspaces } from '../../api/endpoints';
import type { ScreenProps } from '../../app/router';
import { useShell } from '../../app/AppShell';
import { useApiQuery, useSubmit } from '../../app/useApi';
import { BackLink } from '../../components/BackLink/BackLink';
import { Button } from '../../components/Button/Button';
import { ChoiceChip } from '../../components/ChoiceChip/ChoiceChip';
import { ConfirmDialog } from '../../components/ConfirmDialog/ConfirmDialog';
import { Icon } from '../../components/Icon/Icon';
import { Select } from '../../components/Select/Select';
import { EmptyState, ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { Tag } from '../../components/Tag/Tag';
import { TextField } from '../../components/TextField/TextField';
import { automationsApi, type AutomationPayload, type PlaybookBrief } from '../../features/automations/api';
import '../../features/automations/automations.css';
import { PlaybookCard } from '../../features/automations/PlaybookCard';
import { RunEstimateCard } from '../../features/automations/RunEstimateCard';
import { joinFields, nextRunText } from '../../features/automations/format';
import { Modal } from '../../components/Modal/Modal';

interface ExpertOption {
  id: string;
  name: string;
  avatar_ref?: string | null;
  specialty?: string | null;
  title?: string | null;
}

type Freq = 'daily' | 'weekly' | 'monthly' | '';

interface FormState {
  name: string;
  kind: 'prompt' | 'playbook';
  prompt: string;
  leadExpert: string;
  playbookId: string;
  variables: Record<string, string>;
  freq: Freq;
  weekday: string;
  monthday: string;
  time: string;
  workspace: string;
}

const FREQ_OPTIONS = [
  { value: 'daily', label: '每天' },
  { value: 'weekly', label: '每周' },
  { value: 'monthly', label: '每月' },
];
const WEEKDAY_OPTIONS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日'].map((label, i) => ({ value: String(i + 1), label }));
const MONTHDAY_OPTIONS = Array.from({ length: 31 }, (_, i) => ({ value: String(i + 1), label: `${i + 1} 日` }));
const TIME_OPTIONS = Array.from({ length: 24 }, (_, h) => `${String(h).padStart(2, '0')}:00`).map((v) => ({ value: v, label: v }));

const DEFAULT_FORM: FormState = {
  name: '',
  kind: 'prompt',
  prompt: '',
  leadExpert: '',
  playbookId: '',
  variables: {},
  freq: '',
  weekday: '1',
  monthday: '1',
  time: '',
  workspace: '',
};

const PERMISSION_TAG: Record<string, { label: string; tone: 'success' | 'warning' | 'neutral' }> = {
  granted: { label: '已有权限', tone: 'success' },
  needs_connection: { label: '需连接', tone: 'warning' },
  needs_grant: { label: '待确认', tone: 'neutral' },
};

function toPayload(f: FormState): AutomationPayload {
  return {
    name: f.name.trim(),
    content_kind: f.kind,
    schedule_kind: f.freq || 'daily',
    schedule_time: f.time,
    timezone: 'Asia/Shanghai',
    workspace: f.workspace,
    lead_expert: f.leadExpert || null,
    prompt: f.kind === 'prompt' ? f.prompt.trim() || null : null,
    playbook: f.kind === 'playbook' ? f.playbookId || null : null,
    playbook_variables: f.kind === 'playbook' ? f.variables : null,
    schedule_weekday: f.freq === 'weekly' ? Number(f.weekday) : null,
    schedule_monthday: f.freq === 'monthly' ? Number(f.monthday) : null,
  };
}

function missingFields(f: FormState): string[] {
  const missing: string[] = [];
  if (!f.name.trim()) missing.push('名称');
  if (f.kind === 'prompt' && !f.prompt.trim()) missing.push('指令');
  if (f.kind === 'playbook' && !f.playbookId) missing.push('Playbook');
  if (!f.workspace) missing.push('工作区');
  if (!f.freq || !f.time) missing.push('计划');
  if (f.kind === 'prompt' && !f.leadExpert) missing.push('主责专家');
  return missing;
}

function validate(f: FormState): string | null {
  const missing = missingFields(f);
  if (missing.length === 0) return null;
  const all = f.kind === 'prompt' ? 5 : 4;
  if (missing.length === all) return f.kind === 'prompt' ? '填写名称、指令、工作区、计划和主责专家后可保存' : '请填写所需信息并选择 Playbook';
  return `还需填写${joinFields(missing)}`;
}

export default function Screen({ params, query, navigate }: ScreenProps) {
  const shell = useShell();
  const id = params.automationId ?? '';
  const isNew = id === 'new';
  const typeParam = query.get('type') === 'playbook' ? 'playbook' : 'prompt';

  const detail = useApiQuery((s) => (isNew ? Promise.resolve(null) : automationsApi.detail(id, s)), [id, isNew]);
  const expertsPage = useApiQuery((s) => experts.getExperts(undefined, s) as Promise<{ items: ExpertOption[] }>, []);
  const workspacesPage = useApiQuery((s) => workspaces.getWorkspaces(undefined, s) as Promise<{ items: Array<{ id: string; name: string }> }>, []);
  const playbooksPage = useApiQuery(
    (s) => (isNew && typeParam === 'playbook') || detail.data?.automation.content_kind === 'playbook' ? automationsApi.playbooks(s) : Promise.resolve(null),
    [isNew, typeParam, detail.data],
  );

  const [form, setForm] = useState<FormState | null>(null);
  const [dirty, setDirty] = useState(false);
  const [confirmLeave, setConfirmLeave] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [justSaved, setJustSaved] = useState(query.get('saved') === '1');

  useEffect(() => {
    if (form) return;
    if (isNew) {
      if (workspacesPage.data) {
        setForm({ ...DEFAULT_FORM, kind: typeParam, playbookId: typeParam === 'playbook' ? (query.get('playbook') ?? '') : '' });
      }
      return;
    }
    const a = detail.data?.automation;
    if (a) {
      setForm({
        name: a.name,
        kind: a.content_kind,
        prompt: a.prompt ?? '',
        leadExpert: a.lead_expert?.id ?? '',
        playbookId: a.playbook?.id ?? '',
        variables: a.playbook_variables ?? {},
        freq: a.schedule.kind,
        weekday: String(a.schedule.weekday ?? 1),
        monthday: String(a.schedule.monthday ?? 1),
        time: a.schedule.time,
        workspace: a.workspace?.id ?? '',
      });
    }
  }, [form, isNew, typeParam, detail.data, workspacesPage.data]);

  const set = (patch: Partial<FormState>) => {
    setForm((f) => (f ? { ...f, ...patch } : f));
    setDirty(true);
    setJustSaved(false);
  };

  const save = useSubmit(async () => {
    if (!form) return;
    const payload = toPayload(form);
    if (isNew) {
      const created = await automationsApi.create(payload);
      setDirty(false);
      shell.refresh();
      navigate(`/automations/${created.automation.id}/edit?type=${form.kind}&saved=1`, { replace: true });
      return;
    }
    if (dirty) {
      const version = detail.data?.automation.version ?? 0;
      // content_kind 不可修改，后端 PATCH 也明确排除该字段。
      const { content_kind: _drop, ...rest } = payload;
      await automationsApi.patch(id, { ...rest, version });
      setDirty(false);
      shell.refresh();
    }
    // 设计稿 58/59：编辑已有规则后“保存”回到自动化列表（640:2 / 2827:2213）。
    navigate('/automations');
  });

  const leave = () => {
    if (dirty) setConfirmLeave(true);
    else navigate('/automations');
  };

  const playbooks = playbooksPage.data?.items ?? [];
  const playbook = playbooks.find((p) => p.id === form?.playbookId) ?? detail.data?.automation.playbook ?? null;
  const variableNames = useMemo(() => (playbook?.variables ?? []).map((v) => v.name), [playbook]);

  if (!isNew && detail.status === 'loading' && !detail.data) return <div className="sg-taskpage__state"><LoadingState rows={4} /></div>;
  if (!isNew && detail.status === 'error' && !detail.data)
    return (
      <div className="sg-taskpage__state">
        <ErrorBanner message={errorMessage(detail.error, '这条规则暂时无法读取')} onRetry={detail.reload} />
      </div>
    );
  if (!form) return <div className="sg-taskpage__state"><LoadingState rows={4} /></div>;

  const expertOptions = (expertsPage.data?.items ?? []).map((e) => {
    const role = e.title || e.specialty;
    return { value: e.id, label: role ? `${e.name} · ${role}` : e.name };
  });
  const workspaceOptions = (workspacesPage.data?.items ?? []).map((w) => ({ value: w.id, label: w.name }));
  const invalid = validate(form);
  const estimate = isNew ? undefined : detail.data?.automation;
  const enabled = estimate?.state === 'enabled';
  const nextRun = nextRunText(form.freq, form.time, form.freq === 'weekly' ? Number(form.weekday) : null, form.freq === 'monthly' ? Number(form.monthday) : null);
  const permissions = estimate?.permissions ?? [];
  const missingPermission = permissions.some((p) => p.state !== 'granted');
  // A draft rule is already stored on the server, so it reads as saved even when opened later (3156:714).
  const savedDraft = justSaved || estimate?.state === 'draft';

  let status: string;
  if (isNew) status = '尚未保存';
  else if (dirty) status = enabled ? '有未保存的更改' : '有未保存的更改 · 未启用';
  else if (enabled) status = '已启用 · 按计划运行';
  else if (savedDraft) status = '已保存 · 未启用';
  else status = '未启用 · 保存后在列表中启用';

  let saveHint: string;
  if (invalid) saveHint = invalid;
  else if (isNew) saveHint = '填写完成，可以保存';
  else if (!dirty) saveHint = '修改后可保存';
  else saveHint = enabled ? '保存后按新计划运行' : '保存后仍需启用';

  const leadName = estimate?.lead_expert?.name;
  const creatorId = (playbook as PlaybookBrief | null)?.created_by?.id;
  const mine = !!creatorId && creatorId === currentSession()?.user?.id;

  const saveAndReport = () =>
    void save.submit().then(() => {
      if (save.error) return;
    });

  return (
    <div style={{ height: '100%', overflow: 'auto' }}>
      <div className="sg-autoedit">
        <BackLink label="自动化" size={30} onBack={leave} />
        <div>
          <h2 className="sg-indetail__title">{isNew ? '新建自动化' : form.name || '编辑自动化'}</h2>
          <p className="sg-indetail__text" style={{ color: 'var(--color-slate-540)' }}>
            {status}
          </p>
        </div>
        <div className="sg-autoedit__grid">
          <div className="sg-autoedit__form">
            <div className="sg-autofield">
              <span className="sg-autofield__label">名称</span>
              <TextField size={42} value={form.name} placeholder="请输入名称" onValueChange={(v) => set({ name: v })} />
            </div>
            <div className="sg-autofield">
              <span className="sg-autofield__label">运行内容</span>
              <div className="sg-inchips">
                <ChoiceChip role="radio" size={30} label="一段指令" selected={form.kind === 'prompt'} disabled={!isNew} onToggle={() => set({ kind: 'prompt' })} />
                <ChoiceChip role="radio" size={30} label="Playbook" selected={form.kind === 'playbook'} disabled={!isNew} onToggle={() => set({ kind: 'playbook' })} />
                {!isNew && <span className="sg-autofield__hint" style={{ marginLeft: 8, alignSelf: 'center' }}>运行内容创建后不可修改</span>}
              </div>
            </div>
            {form.kind === 'prompt' ? (
              <div className="sg-autofield">
                <span className="sg-autofield__label">指令</span>
                <TextField multiline rows={5} value={form.prompt} placeholder="请输入要运行的指令" onValueChange={(v) => set({ prompt: v })} />
              </div>
            ) : (
              <>
                <div className="sg-autofield">
                  <span className="sg-autofield__label">Playbook</span>
                  {playbook ? (
                    <PlaybookCard
                      playbook={{
                        name: playbook.name,
                        meta: `${(playbook as PlaybookBrief).step_count ?? ''} 步 · ${(playbook as PlaybookBrief).expert_count ?? ''} 位专家${mine ? ' · 你创建' : ''}`,
                      }}
                      variables={variableNames.map((n) => ({
                        label: `{${n}} = ${form.variables[n] ?? playbook.variables?.find((v) => v.name === n)?.default ?? ''}`,
                        tone: 'neutral' as const,
                        size: 18 as const,
                      }))}
                      variablesLabel={isNew || justSaved ? undefined : '每次运行前填写变量：'}
                    />
                  ) : (
                    <span className="sg-autofield__hint">请选择 Playbook</span>
                  )}
                  <div>
                    <Button variant="secondary" size={30} label={playbook ? '更换 Playbook' : '选择 Playbook'} onClick={() => setPickerOpen(true)} />
                  </div>
                </div>
                {playbook && variableNames.length > 0 && (
                  <div className="sg-autofield">
                    <span className="sg-autofield__label">变量</span>
                    <div className="sg-autovars">
                      {variableNames.map((n) => (
                        <div key={n} className="sg-autovars__row">
                          <span>{n}</span>
                          <TextField
                            size={32}
                            value={form.variables[n] ?? playbook.variables?.find((v) => v.name === n)?.default ?? ''}
                            placeholder={playbook.variables?.find((v) => v.name === n)?.default ?? ''}
                            onValueChange={(v) => set({ variables: { ...form.variables, [n]: v } })}
                          />
                        </div>
                      ))}
                    </div>
                    <span className="sg-autofield__hint">变量名必须与 Playbook 完全一致，保存时后端会校验</span>
                  </div>
                )}
              </>
            )}
            <div className="sg-autofield">
              <span className="sg-autofield__label">归属工作区</span>
              <span className="sg-autofield__hint">按该工作区的规则与资料权限运行</span>
              <Select size={36} label="归属工作区" placeholder="请选择" value={form.workspace || undefined} options={workspaceOptions} onChange={(v) => set({ workspace: v })} />
            </div>
            <div className="sg-autofield">
              <span className="sg-autofield__label">计划</span>
              <span className="sg-autofield__hint">时区：北京时间（UTC+8）</span>
              <div className="sg-schedule" style={{ paddingTop: 0 }}>
                <Select size={36} label="频率" placeholder="—" value={form.freq || undefined} options={FREQ_OPTIONS} onChange={(v) => set({ freq: v as Freq })} />
                {form.freq === 'weekly' && <Select size={36} label="日期" value={form.weekday} options={WEEKDAY_OPTIONS} onChange={(v) => set({ weekday: v })} />}
                {form.freq === 'monthly' && <Select size={36} label="日期" value={form.monthday} options={MONTHDAY_OPTIONS} onChange={(v) => set({ monthday: v })} />}
                {!form.freq && <Select size={36} label="日期" placeholder="—" options={[]} onChange={() => undefined} />}
                <Select size={36} label="时间" placeholder="—" value={form.time || undefined} options={TIME_OPTIONS} onChange={(v) => set({ time: v })} />
              </div>
              <span className="sg-autofield__hint">
                {nextRun
                  ? `${!isNew && !enabled && justSaved ? '启用后' : ''}下次运行：${nextRun}。Mac 未开机或 Sage 未运行时会记为“已错过”。`
                  : '请选择执行周期、日期和时间'}
              </span>
              {isNew && nextRun && <span className="sg-autofield__hint">保存后仍需启用</span>}
            </div>
            <div className="sg-autofield">
              <span className="sg-autofield__label">主责专家</span>
              {form.kind === 'prompt' ? (
                <Select
                  size={36}
                  label="主责专家"
                  placeholder="请选择主责专家"
                  value={form.leadExpert || undefined}
                  options={expertOptions}
                  onChange={(v) => set({ leadExpert: v })}
                />
              ) : (
                <span className="sg-autofield__hint">{leadName ? `${leadName} · 由 Playbook 指定` : '待确认'}</span>
              )}
            </div>
            <div className="sg-autofield">
              <span className="sg-autofield__label">需要的权限</span>
              {isNew || !estimate ? (
                <>
                  <span className="sg-autofield__hint">{invalid ? '完成设置后确认' : '启用时确认'}</span>
                  <span className="sg-autofield__hint">{form.playbookId ? '具体权限' : '选择内容后显示'}</span>
                  <div>
                    <Tag label="待确认" tone="neutral" size={18} />
                  </div>
                </>
              ) : (
                <>
                  <span className="sg-autofield__hint">{savedDraft && missingPermission ? '缺少权限时无法启用' : '启用时会逐项确认'}</span>
                  <span className="sg-autofield__hint">具体权限</span>
                  {permissions.length === 0 && (
                    <div>
                      <Tag label="待确认" tone="neutral" size={18} />
                    </div>
                  )}
                  {permissions.map((p) => (
                    <div key={p.id} className="sg-incard__row" style={{ gap: 6 }}>
                      <Icon name={p.state === 'granted' ? 'check' : 'info'} size={13} color={p.state === 'granted' ? 'color.green.660' : 'color.orange.660'} />
                      <span className="sg-autofield__hint" style={{ flex: 1 }}>{p.label}</span>
                      <Tag label={PERMISSION_TAG[p.state]?.label ?? p.state} tone={PERMISSION_TAG[p.state]?.tone ?? 'neutral'} size={18} />
                    </div>
                  ))}
                </>
              )}
            </div>
            {save.error && <ErrorBanner message={errorMessage(save.error, '保存失败，请检查填写后再试')} />}
            <div className="sg-incard__row" style={{ gap: 8 }}>
              <Button variant="primary" size={34} label="保存" busy={save.submitting} disabled={!!invalid} title={invalid ?? undefined} onClick={saveAndReport} />
              <Button variant="ghost" size={34} label="取消" onClick={leave} />
              <span className="sg-autofield__hint">{saveHint}</span>
            </div>
          </div>
          <div className="sg-autoedit__side">
            <RunEstimateCard
              duration={estimate?.estimated_duration_minutes != null ? `约 ${estimate.estimated_duration_minutes} 分钟` : undefined}
              credits={estimate?.estimated_credits != null ? `约 ${estimate.estimated_credits} Credits` : undefined}
              output={estimate?.expected_output ?? undefined}
              emptyLabel={form.kind === 'playbook' ? '待确认' : isNew && invalid ? '待填写' : '待估算'}
            />
          </div>
        </div>
      </div>

      {pickerOpen && (
        <Modal className="sg-dialog" label="选择 Playbook" style={{ width: 480, textAlign: 'left' }} onClose={() => setPickerOpen(false)}>
            <h2 id="sg-dialog-title">选择 Playbook</h2>
            {playbooksPage.status === 'loading' && <LoadingState rows={3} />}
            {playbooksPage.status === 'error' && <ErrorBanner message={errorMessage(playbooksPage.error, 'Playbook 暂时无法读取')} onRetry={playbooksPage.reload} />}
            {playbooksPage.data && playbooks.length === 0 && <EmptyState icon="layers" title="还没有可见的 Playbook" description="在任务里把一次成功的执行存为 Playbook 后，就能在这里复用" />}
            <div className="sg-autopick">
              {playbooks.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  className={`sg-autopick__row${form.playbookId === p.id ? ' is-selected' : ''}`}
                  onClick={() => {
                    const vars: Record<string, string> = {};
                    for (const v of p.variables) vars[v.name] = form.variables[v.name] ?? v.default ?? '';
                    set({ playbookId: p.id, variables: vars });
                    setPickerOpen(false);
                  }}
                >
                  <Icon name="layers" size={16} />
                  <span style={{ flex: 1, minWidth: 0 }}>
                    <span className="sg-autopick__name">{`${p.name} · ${p.step_count} 步 · ${p.expert_count} 位专家`}</span>
                    {p.created_by && (
                      <span className="sg-autopick__meta" style={{ display: 'block' }}>
                        {p.created_by.name}
                      </span>
                    )}
                  </span>
                  {form.playbookId === p.id && <Icon name="check" size={14} color="color.green.660" />}
                </button>
              ))}
            </div>
            <div className="sg-dialog__actions">
              <Button variant="ghost" size={32} label="取消" onClick={() => setPickerOpen(false)} />
            </div>
        </Modal>
      )}
      {confirmLeave && (
        <ConfirmDialog
          title="放弃未保存的更改？"
          message="退出后，本次未保存的更改将丢失。"
          confirmLabel="继续编辑"
          cancelLabel="放弃更改"
          onConfirm={() => setConfirmLeave(false)}
          onCancel={() => navigate('/automations')}
          onDismiss={() => setConfirmLeave(false)}
        />
      )}
    </div>
  );
}
