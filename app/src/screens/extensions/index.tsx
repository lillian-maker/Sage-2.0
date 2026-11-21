import { useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { extensionsApi } from '../../features/extensions/api';
import { ExtensionsBoard } from '../../features/extensions/ExtensionsBoard';
import { DPage } from '../../features/experts/parts';
import { ExtensionsHeader, KindTabs } from './header';
import '../../features/extensions/board.css';

export default function Screen({ query, navigate }: ScreenProps) {
  const kind = query.get('kind') ?? '';
  const [q, setQ] = useState(query.get('q') ?? '');
  const counts = useApiQuery((signal) => extensionsApi.list({ tab: 'installed' }, signal), []);

  return (
    <DPage>
      <ExtensionsHeader
        navigate={navigate}
        tab="installed"
        installedCount={counts.data?.counts.installed}
        q={q}
        onSearch={setQ}
        description="插件给专家增加工具，技能教专家方法，连接器接入外部数据，智能体接入外部 Agent；连接与授权都会单独确认"
        showImport
      />
      <KindTabs kind={kind} onChange={(k) => navigate(k ? `/extensions?kind=${k}` : '/extensions', { replace: true })} />
      <ExtensionsBoard tab="installed" kind={kind} q={q.trim()} />
    </DPage>
  );
}
