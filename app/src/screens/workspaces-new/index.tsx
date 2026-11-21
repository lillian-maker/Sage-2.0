import { useShell } from '../../app/AppShell';
import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { ErrorBanner, LoadingState, errorMessage } from '../../components/States/States';
import { wsApi } from '../../features/workspaces/model';
import { CreateWorkspaceDialog } from '../../features/workspaces/parts';
import '../../features/workspaces/workspaces.css';
import WorkspaceScreen from '../workspaces-workspaceId';

export default function Screen(props: ScreenProps) {
  const { query, navigate } = props;
  const shell = useShell();
  const list = useApiQuery((s) => wsApi.list(s), []);
  const items = list.data?.items ?? [];
  // The form floats over the workspace the user came from (Figma 19 draws it over 16).
  const behind = query.get('from') ?? items[0]?.id ?? null;
  // 设计稿 723:523：“取消”回到表单下方的工作区（16），不是浏览器上一页。
  const close = () => navigate(behind ? `/workspaces/${encodeURIComponent(behind)}` : '/tasks', { replace: true });

  return (
    <>
      {behind ? (
        <WorkspaceScreen {...props} params={{ workspaceId: behind }} query={new URLSearchParams()} />
      ) : (
        <div className="sg-ws">
          <div className="sg-ws__main">{list.status === 'loading' && <LoadingState rows={4} />}</div>
        </div>
      )}
      {list.status === 'error' && (
        <div className="sg-ws__state">
          <ErrorBanner message={errorMessage(list.error, '工作区列表暂时无法读取')} onRetry={list.reload} />
        </div>
      )}
      <CreateWorkspaceDialog
        workspaces={items}
        onCancel={close}
        onCreated={(id) => {
          shell.refresh();
          navigate(`/workspaces/${encodeURIComponent(id)}`, { replace: true });
        }}
      />
    </>
  );
}
