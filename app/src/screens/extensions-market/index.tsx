import { useState } from 'react';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { extensionsApi } from '../../features/extensions/api';
import { ExtensionsBoard } from '../../features/extensions/ExtensionsBoard';
import { DPage, SectionHead } from '../../features/experts/parts';
import { ExtensionsHeader, KindTabs } from '../extensions/header';
import '../../features/extensions/board.css';

export default function Screen({ query, navigate }: ScreenProps) {
  const kind = query.get('kind') ?? '';
  const [q, setQ] = useState(query.get('q') ?? '');
  const counts = useApiQuery((signal) => extensionsApi.list({ tab: 'installed' }, signal), []);

  return (
    <DPage>
      <ExtensionsHeader
        navigate={navigate}
        tab="market"
        installedCount={counts.data?.counts.installed}
        q={q}
        onSearch={setQ}
        description="插件、技能与智能体安装后用开关启用；连接器需要单独授权，首期全部只读"
      />
      <KindTabs kind={kind} onChange={(k) => navigate(k ? `/extensions/market?kind=${k}` : '/extensions/market', { replace: true })} />
      <SectionHead title="精选" count="官方与社区已审核" />
      <ExtensionsBoard tab="market" kind={kind} q={q.trim()} />
    </DPage>
  );
}
