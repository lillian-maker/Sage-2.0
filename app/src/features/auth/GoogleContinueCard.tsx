import { Button } from '../../components/Button/Button';
import { SageLogo } from '../../components/AppSidebar/SageLogo';
import './auth.css';

export interface GoogleContinueCardProps {
  email?: string;
  onContinue?: () => void;
  /** The SSO button renders only when a handler is given (enterprise SSO is not offered in the first release, Q-X1). */
  onSSO?: () => void;
  onBack?: () => void;
  busy?: boolean;
  error?: string;
}

export function GoogleContinueCard({ email, onContinue, onSSO, onBack, busy, error }: GoogleContinueCardProps) {
  return (
    <section className="sg-authcard">
      <SageLogo />
      <h1>继续使用 Google</h1>
      <p>{email ? `将以 ${email} 在 Google 完成身份验证` : '继续后将在 Google 完成身份验证'}</p>
      {error && (
        <p className="sg-authcard__error" role="alert">
          {error}
        </p>
      )}
      {onSSO && <Button variant="secondary" size={42} block icon="contact" label="使用企业 SSO 继续" onClick={onSSO} />}
      <Button variant="primary" size={42} block label="继续" onClick={onContinue} busy={busy} />
      <Button variant="secondary" size={42} block icon="chevron-left" label="返回登录" onClick={onBack} />
    </section>
  );
}
