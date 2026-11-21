import { useApiQuery, useSubmit } from '../../app/useApi';
import { ForbiddenState } from '../../components/States/States';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { apiMessage } from '../task/model';
import { wsApi } from './model';

/**
 * 83 工作区「无权限」: a member of the ledger who is not in the workspace can ask to join; owners decide on
 * the members page. Join-request lookups answer NOT_FOUND for workspaces outside the ledger, so those keep
 * the generic state and offer no request.
 */
export function WorkspaceJoinState({ id, onBack }: { id: string; onBack: () => void }) {
  const mine = useApiQuery((s) => wsApi.myJoinRequest(id, s), [id]);
  const act = useSubmit(async (fn: () => Promise<unknown>) => {
    await fn();
    mine.reload();
  });
  if (mine.status === 'error' || (mine.status === 'loading' && !mine.data)) {
    return <ForbiddenState title="无法访问这个工作区" action={{ label: '返回全部任务', onClick: onBack }} />;
  }
  const request = mine.data?.join_request;
  const pending = request?.state === 'pending';
  return (
    <>
      <ForbiddenState
        title="你还不是这个工作区的成员"
        action={
          pending
            ? { label: '撤回申请', onClick: () => void act.submit(() => wsApi.withdrawJoin(id, request.id)) }
            : { label: '申请加入', onClick: () => void act.submit(() => wsApi.requestJoin(id)) }
        }
      />
      {pending && <InlineNotice icon="info" tone="info" text="已申请加入，负责人处理后会通知你" />}
      {request?.state === 'rejected' && <InlineNotice icon="info" tone="info" text="上次的申请未通过，可以重新申请" />}
      {act.error && <InlineNotice icon="info" tone="error" text={apiMessage(act.error, '申请没有发出，请重试')} />}
    </>
  );
}
