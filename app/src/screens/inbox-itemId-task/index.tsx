import type { ScreenProps } from '../../app/router';
import { useApiQuery } from '../../app/useApi';
import { EmptyState, ErrorBanner, ForbiddenState, LoadingState, errorMessage } from '../../components/States/States';
import { inboxApi } from '../../features/inbox/api';
import '../../features/inbox/inbox.css';
import TaskDetailScreen from '../tasks-taskId';

/** 任务页：收件消息交给专家后的落地页，直接展示交办生成的任务。 */
export default function Screen(props: ScreenProps) {
  const { params, navigate } = props;
  const itemId = params.itemId ?? '';
  const item = useApiQuery((s) => inboxApi.item(itemId, s), [itemId]);

  if (item.status === 'loading' && !item.data) return <div className="sg-taskpage__state"><LoadingState rows={4} /></div>;
  if (item.status === 'error' && !item.data) {
    if (item.error.status === 404 || item.error.status === 403)
      return (
        <div className="sg-taskpage__state">
          <ForbiddenState title="这条消息不存在或你没有访问权限" action={{ label: '返回收件', onClick: () => navigate('/inbox') }} />
        </div>
      );
    return (
      <div className="sg-taskpage__state">
        <ErrorBanner message={errorMessage(item.error, '这条消息暂时无法读取')} onRetry={item.reload} />
      </div>
    );
  }
  const taskId = item.data?.item.task?.id;
  if (!taskId)
    return (
      <div className="sg-taskpage__state">
        <EmptyState
          icon="message-square"
          title="这条消息还没有交给专家"
          description="在收件详情里点“交给专家”，专家开工后任务会出现在这里"
          action={{ label: '返回收件', onClick: () => navigate('/inbox') }}
        />
      </div>
    );
  return <TaskDetailScreen {...props} params={{ ...params, taskId }} />;
}
