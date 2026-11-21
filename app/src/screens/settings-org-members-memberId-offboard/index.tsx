import type { ScreenProps } from '../../app/router';
import { OrgMembersPage } from '../../features/settings/OrgMembers';

/** 78 办理离职 (760:2): the 75 member page with the offboarding panel on top. */
export default function Screen({ params }: ScreenProps) {
  return <OrgMembersPage offboardId={params.memberId} />;
}
