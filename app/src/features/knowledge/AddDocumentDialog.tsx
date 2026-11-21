import { useRef, useState } from 'react';
import { request, ApiError } from '../../api/client';
import { Button } from '../../components/Button/Button';
import { IconButton } from '../../components/IconButton/IconButton';
import { SegmentedControl } from '../../components/SegmentedControl/SegmentedControl';
import { Select } from '../../components/Select/Select';
import { TextField } from '../../components/TextField/TextField';
import { errorMessage } from '../../components/States/States';
import type { DocumentDetail, KnowledgeBase } from './api';
import { Modal } from '../../components/Modal/Modal';

const INTERVALS = [
  { value: 'daily', label: '每天更新' },
  { value: 'weekly', label: '每周更新' },
  { value: 'monthly', label: '每月更新' },
];

const UPLOAD_ACCEPT = '.pdf,.docx,.doc,.xlsx,.xls,.csv,.pptx,.txt,.md';
const UPLOAD_MAX_MB = 20;

type Mode = 'file' | 'web';

function formatSize(bytes: number): string {
  return bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

/** 添加资料 dialog: local files go to POST app/documents/upload (multipart), web pages to POST app/documents. */
export function AddDocumentDialog({ bases, fixedKbId, onClose, onAdded }: { bases: KnowledgeBase[]; fixedKbId?: string | undefined; onClose: () => void; onAdded: (doc: DocumentDetail) => void }) {
  const editable = bases.filter((b) => b.can_edit);
  const [kbId, setKbId] = useState(fixedKbId ?? editable[0]?.id ?? '');
  const [mode, setMode] = useState<Mode>('file');
  const [file, setFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [interval, setInterval] = useState('weekly');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const pickFile = (next: File | undefined) => {
    setError(null);
    if (!next) return;
    if (next.size > UPLOAD_MAX_MB * 1024 * 1024) {
      setFile(null);
      setError(`文件超过 ${UPLOAD_MAX_MB} MB，请压缩或拆分后再上传`);
      return;
    }
    setFile(next);
  };

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      let res: { document: DocumentDetail };
      if (mode === 'file' && file) {
        const form = new FormData();
        form.append('knowledge_base_id', kbId);
        form.append('file', file);
        if (title.trim()) form.append('title', title.trim());
        res = await request<{ document: DocumentDetail }>('POST', 'app/documents/upload', { body: form });
      } else {
        res = await request<{ document: DocumentDetail }>('POST', 'app/documents', {
          body: { knowledge_base_id: kbId, source_kind: 'web', source_url: url.trim(), refresh_interval: interval, ...(title.trim() ? { title: title.trim() } : {}) },
        });
      }
      onAdded(res.document);
    } catch (err) {
      if (err instanceof ApiError && err.code === 'PERMISSION_DENIED') setError('你没有这个知识库的编辑权限');
      else if (err instanceof ApiError && mode === 'file' && (err.code === 'VALIDATION_FAILED' || err.code === 'INVALID_BODY_SIZE'))
        setError(`不支持这个文件：请上传 ${UPLOAD_MAX_MB} MB 以内的 PDF、Word、Excel、PPT、CSV、TXT 或 Markdown 文件`);
      else setError(errorMessage(err, '添加失败，请重试'));
    } finally {
      setBusy(false);
    }
  };

  const ready = mode === 'file' ? !!file : /^https?:\/\/.+/.test(url.trim());

  return (
    <Modal className="sg-dmodal__card" label="添加资料" onClose={onClose}>
        <div className="sg-dmodal__head">
          <h2>添加资料</h2>
          <IconButton icon="x" size={28} variant="ghost" label="关闭" onClick={onClose} />
        </div>
        <p className="sg-dmodal__sub">上传本地文件，或添加网页资料由系统定期抓取更新；添加后会自动建立索引。</p>
        <SegmentedControl<Mode>
          label="资料来源"
          value={mode}
          options={[
            { value: 'file', label: '本地文件' },
            { value: 'web', label: '网页' },
          ]}
          onChange={(m) => {
            setMode(m);
            setError(null);
          }}
        />
        {!fixedKbId && (
          <div className="sg-dfield">
            <span className="sg-dfield__label">添加到知识库</span>
            <Select size={32} width={320} value={kbId} options={editable.map((b) => ({ value: b.id, label: b.name }))} onChange={setKbId} />
          </div>
        )}
        {mode === 'file' ? (
          <div className="sg-dfield">
            <span className="sg-dfield__label">
              <span>文件</span>
              <span>{`PDF、Word、Excel、PPT、CSV、TXT、Markdown，${UPLOAD_MAX_MB} MB 以内`}</span>
            </span>
            <input ref={fileRef} type="file" accept={UPLOAD_ACCEPT} hidden onChange={(e) => pickFile(e.target.files?.[0])} />
            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
              <Button variant="secondary" size={32} label={file ? '重新选择' : '选择文件'} onClick={() => fileRef.current?.click()} />
              {file && <span className="sg-dmuted">{`${file.name} · ${formatSize(file.size)}`}</span>}
            </div>
          </div>
        ) : (
          <div className="sg-dfield">
            <span className="sg-dfield__label">网页链接</span>
            <TextField size={32} value={url} placeholder="https://…" onValueChange={setUrl} />
          </div>
        )}
        <div className="sg-dfield">
          <span className="sg-dfield__label">
            <span>标题</span>
            <span>{mode === 'file' ? '选填，默认同文件名' : '选填，默认同网址'}</span>
          </span>
          <TextField size={32} value={title} placeholder={mode === 'file' ? '例如：竞品价格带汇总' : '例如：德国包装法官网说明'} onValueChange={setTitle} />
        </div>
        {mode === 'web' && (
          <div className="sg-dfield">
            <span className="sg-dfield__label">更新频率</span>
            <Select size={32} width={220} value={interval} options={INTERVALS} onChange={setInterval} />
          </div>
        )}
        {editable.length === 0 && <span className="sg-dfield__error">你没有可编辑的知识库</span>}
        {error && <span className="sg-dfield__error">{error}</span>}
        <div className="sg-dmodal__foot">
          <Button variant="ghost" size={32} label="取消" onClick={onClose} />
          <Button variant="primary" size={32} label={mode === 'file' ? '上传' : '添加'} busy={busy} disabled={!kbId || !ready} onClick={submit} />
        </div>
    </Modal>
  );
}
