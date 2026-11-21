/**
 * Purchases, plan changes, payment methods and invoice requests happen on the Sage website
 * (Q-X8, Q-X9). 加购 goes through a one-time sign-in hand-off (POST app/billing/handoff): the
 * website signs the browser in as the same user with the issuing ledger selected. The other
 * links open the website pages directly and the user may need to sign in there.
 */
import { request } from '../../api/client';

const SITE_ORIGIN = 'https://sagegogo.com';

export const SITE_LINKS = {
  topup: `${SITE_ORIGIN}/pages/billing.html#credits`,
  plans: `${SITE_ORIGIN}/pages/pricing.html`,
  seats: `${SITE_ORIGIN}/pages/billing.html#seats`,
  paymentMethods: `${SITE_ORIGIN}/pages/billing.html#payment-methods`,
  invoice: (orderId: string) => `${SITE_ORIGIN}/pages/billing.html?${new URLSearchParams({ order: orderId })}#invoices`,
};

export function openSite(url: string) {
  window.open(url, '_blank', 'noopener');
}

interface Handoff {
  token: string;
  purpose: 'topup';
  expires_at: string;
  expires_in: number;
  redeem_path: string;
  account_id: string;
}

/** Opens the website already signed in on the current ledger; falls back to the plain page if issuing fails. */
export async function openSiteSignedIn(purpose: 'topup', fallback: string) {
  // Opened before the request so the popup still counts as a direct result of the click.
  const tab = window.open('', '_blank');
  if (tab) tab.opener = null;
  let url = fallback;
  try {
    const handoff = await request<Handoff>('POST', 'app/billing/handoff', { body: { purpose } });
    url = SITE_ORIGIN + handoff.redeem_path;
  } catch {
    url = fallback;
  }
  if (tab) tab.location.href = url;
  else openSite(url);
}
