import type { ScreenProps } from '../../app/router';
import { BillingOverview } from '../../features/billing/BillingOverview';

/** 63 用量与账单 (641:2). */
export default function Screen(_props: ScreenProps) {
  return <BillingOverview />;
}
