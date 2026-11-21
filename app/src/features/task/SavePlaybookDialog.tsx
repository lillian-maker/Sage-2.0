import { useEffect, useState } from 'react';
import { useSubmit } from '../../app/useApi';
import { Avatar } from '../../components/Avatar/Avatar';
import { Button } from '../../components/Button/Button';
import { Icon } from '../../components/Icon/Icon';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { Select } from '../../components/Select/Select';
import { TextField } from '../../components/TextField/TextField';
import { apiMessage, avatarOf, taskApi, type TaskDetail, type TaskStep } from './model';
import './detail.css';
import { Modal } from '../../components/Modal/Modal';

export interface SavePlaybookDialogProps {
  task: TaskDetail;
  steps: TaskStep[];
  onClose: () => void;
}

export function SavePlaybookDialog({ task, steps, onClose }: SavePlaybookDialogProps) {
  const [name, setName] = useState(task.title);
  const [picked, setPicked] = useState<Set<string>>(() => new Set(steps.map((s) => s.id)));
  const [visibility, setVisibility] = useState('only_me');
  const [saved, setSaved] = useState(false);
  const [variables, setVariables] = useState<Array<{ name: string; default: string }>>([]);
  const [varName, setVarName] = useState('');
  const [varValue, setVarValue] = useState('');
  useEffect(() => setPicked(new Set(steps.map((s) => s.id))), [steps]);

  // A declared variable replaces its value in the step titles, so the saved playbook is parameterized.
  const substituted = (title: string) =>
    variables.reduce((t, v) => (v.default ? t.split(v.default).join(`{${v.name}}`) : t), title);
  const save = useSubmit(() =>
    taskApi.savePlaybook({
      source_task: task.id,
      name: name.trim(),
      visibility,
      variables,
      steps: steps
        .filter((s) => picked.has(s.id))
        .map((s, i) => ({ source_step: s.id, order: i + 1, title: substituted(s.title) })),
    }),
  );
  const addVariable = () => {
    const v = { name: varName.trim(), default: varValue.trim() };
    if (!v.name || !v.default || v.name.includes('{') || v.name.includes('}')) return;
    if (variables.some((x) => x.name === v.name)) return;
    setVariables((vs) => [...vs, v]);
    setVarName('');
    setVarValue('');
  };
  const accepted = task.status === 'accepted';
  const canSave = accepted && name.trim().length > 0 && picked.size > 0;
  const options = [
    { value: 'only_me', label: '仅自己' },
    ...(task.workspace ? [{ value: 'workspace', label: `工作区“${task.workspace.name}”成员` }] : []),
    { value: 'account', label: '企业内所有成员' },
  ];

  return (
    <Modal className="sg-pbdialog" labelledBy="sg-pb-title" onClose={onClose}>
        <h2 id="sg-pb-title">存为 Playbook</h2>
        <p className="sg-pbdialog__desc">把这次验收通过的执行图存为模板，下次直接复用；不包含本次的资料内容与对话</p>
        {saved ? (
          <>
            <InlineNotice icon="check" tone="muted" text="已保存" />
            <p className="sg-pbdialog__savednote">可在自动化中选择此 Playbook。</p>
            <div className="sg-pbdialog__footer">
              <span />
              <Button variant="primary" size={32} label="关闭" onClick={onClose} />
            </div>
          </>
        ) : (
          <>
            {!accepted && <InlineNotice icon="info" tone="info" text="任务验收通过后才能存为 Playbook" />}
            <label className="sg-pbdialog__label">名称</label>
            <TextField size={32} value={name} maxLength={40} onValueChange={setName} aria-label="Playbook 名称" />
            <div className="sg-pbdialog__label">步骤 · {picked.size}（来自本次执行图，可以删减）</div>
            <div className="sg-pbdialog__steps">
              {steps.map((s) => {
                const on = picked.has(s.id);
                return (
                  <button
                    key={s.id}
                    type="button"
                    role="checkbox"
                    aria-checked={on}
                    className="sg-pbdialog__step"
                    onClick={() =>
                      setPicked((p) => {
                        const next = new Set(p);
                        if (on) next.delete(s.id);
                        else next.add(s.id);
                        return next;
                      })
                    }
                  >
                    <span className={`sg-checkitem__box${on ? ' is-checked' : ''}`}>{on && <Icon name="check" size={10} />}</span>
                    {s.expert && <Avatar {...avatarOf(s.expert.name, s.expert.avatar_ref)} size={20} />}
                    <span className="sg-pbdialog__steptitle">{substituted(s.title)}</span>
                    {s.skill && <span className="sg-pbdialog__stepmeta">· {s.skill.name}</span>}
                  </button>
                );
              })}
            </div>
            {variables.length > 0 && (
              <>
                <div className="sg-pbdialog__label">变量 · 运行时填写</div>
                <div className="sg-pbdialog__vars">
                  {variables.map((v) => (
                    <div key={v.name} className="sg-pbdialog__var">
                      <span>{`{${v.name}} = ${v.default}`}</span>
                      <button
                        type="button"
                        aria-label={`移除变量 ${v.name}`}
                        onClick={() => setVariables((vs) => vs.filter((x) => x.name !== v.name))}
                      >
                        <Icon name="x" size={12} />
                      </button>
                    </div>
                  ))}
                </div>
              </>
            )}
            <div className="sg-pbdialog__varform">
              <TextField size={32} value={varName} onValueChange={setVarName} placeholder="变量名，如 平台" aria-label="变量名" />
              <TextField size={32} value={varValue} onValueChange={setVarValue} placeholder="本次任务中的值，如 Amazon.de" aria-label="默认值" />
              <Button variant="ghost" size={32} label="添加变量" onClick={addVariable} disabled={!varName.trim() || !varValue.trim()} />
            </div>
            <div className="sg-pbdialog__label">可见范围</div>
            <Select size={32} value={visibility} options={options} onChange={setVisibility} label="可见范围" />
            {save.error && <InlineNotice icon="info" tone="error" text={apiMessage(save.error, '保存没有成功，请重试')} />}
            <div className="sg-pbdialog__footer">
              <span className="sg-pbdialog__hint">授权不会随 Playbook 保存，运行时照常确认</span>
              <Button variant="ghost" size={32} label="取消" onClick={onClose} />
              <Button
                variant="primary"
                size={32}
                label="保存 Playbook"
                busy={save.submitting}
                disabled={!canSave}
                onClick={async () => {
                  const r = await save.submit();
                  if (r !== undefined) setSaved(true);
                }}
              />
            </div>
          </>
        )}
    </Modal>
  );
}
