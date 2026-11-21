import type { ScreenProps } from '../../app/router';
import { BillingOverview, remainingCredits } from '../../features/billing/BillingOverview';
import { fmtCredits } from '../../features/billing/format';
import { accountShortLabel } from '../../features/billing/labels';
import { SITE_LINKS, openSiteSignedIn } from '../../features/billing/site';
import { SettingsDialog } from '../../features/settings/common';

/** 65 加购（跳转官网）(741:2): the 63 page with the hand-off dialog on top. */
export default function Screen({ navigate }: ScreenProps) {
  const close = () => navigate('/billing', { replace: true });
  return (
    <BillingOverview
      overlay={({ accountQ, summaryQ, session }) => {
        const acct = accountQ.data;
        const ledger = acct?.account;
        const owner = ledger?.kind === 'business' ? accountShortLabel(ledger) : `个人 · ${session?.user?.display_name ?? ''}`;
        const remaining = acct ? remainingCredits(ledger?.kind, acct.credits.available, summaryQ.data?.period_used, summaryQ.data?.period_total) : null;
        return (
          <SettingsDialog
            title="加购 Credits"
            description="购买与支付在 Sage 官网完成，App 不保存任何支付信息"
            confirmLabel="前往官网加购 ↗"
            width={560}
            disabled={!acct}
            onCancel={close}
            onConfirm={() => {
              void openSiteSignedIn('topup', SITE_LINKS.topup);
              close();
            }}
          >
            <div className="sg-bill__sumbox">
              <div>
                <span>账本</span>
                <span>{ledger ? owner : '—'}</span>
              </div>
              <div>
                <span>当前剩余</span>
                <span>{remaining === null ? '暂不可用' : `${fmtCredits(remaining)} Credits`}</span>
              </div>
            </div>
            <p className="sg-bill__dialognote">
              点击后将在浏览器中打开官网加购页面，并自动带上当前账本，无需重新登录或选择。支付完成后，这里的额度会自动更新。
            </p>
          </SettingsDialog>
        );
      }}
    />
  );
}
