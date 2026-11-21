import { useEffect, useState, type FormEvent } from 'react';
import { ApiError, currentSession, loadSession, type AuthSession } from '../../api/client';
import { auth } from '../../api/endpoints';
import type { ScreenProps } from '../../app/router';
import { SageLogo } from '../../components/AppSidebar/SageLogo';
import { Button } from '../../components/Button/Button';
import { InlineNotice } from '../../components/InlineNotice/InlineNotice';
import { LoadingState } from '../../components/States/States';
import { TextField } from '../../components/TextField/TextField';
import { GoogleContinueCard } from '../../features/auth/GoogleContinueCard';
import { afterLogin, safeNext } from '../../features/auth/session';
import '../../features/auth/auth.css';

type Step = 'start' | 'code' | 'google' | 'trial';

interface Challenge {
  flowId: string;
  challengeId: string;
  resendAt: number;
  resent: boolean;
}

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const MESSAGES: Record<string, string> = {
  INVALID_IDENTIFIER: '请输入有效的邮箱地址',
  RATE_LIMITED: '操作太频繁，请稍后再试',
  DELIVERY_FAILED: '验证码暂时无法发送，请稍后重试',
  CHANNEL_UNAVAILABLE: '邮箱验证码暂时无法发送，请稍后重试',
  AUTH_FLOW_REQUIRED: '邮箱验证码登录暂未开放',
  INVALID_CODE: '验证码不正确或已过期',
  INVALID_FLOW: '验证已过期，请返回登录重新发送',
  GOOGLE_UNAVAILABLE: 'Google 登录尚未接通，请改用邮箱',
  NETWORK_ERROR: '无法连接服务器，请检查网络后重试',
};

function message(err: unknown): string {
  if (err instanceof ApiError) return MESSAGES[err.code] ?? (err.status > 0 && err.status < 500 ? err.message : '暂时无法完成，请稍后重试');
  return '暂时无法完成，请稍后重试';
}

function legalHref(value: unknown): string | undefined {
  return typeof value === 'string' && /^(https:\/\/|\/)/.test(value) ? value : undefined;
}

function useNow(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

export default function Screen({ query, navigate }: ScreenProps) {
  const next = safeNext(query.get('next'));
  const [session, setSession] = useState<AuthSession | null>(currentSession);
  const [sessionFailed, setSessionFailed] = useState(false);
  const [step, setStep] = useState<Step>('start');
  const [email, setEmail] = useState('');
  const [code, setCode] = useState('');
  const [challenge, setChallenge] = useState<Challenge | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [trialNotice, setTrialNotice] = useState(false);
  const now = useNow(step === 'code' && !!challenge && challenge.resendAt > Date.now());

  const readSession = () => {
    setSessionFailed(false);
    loadSession(true)
      .then(setSession)
      .catch(() => setSessionFailed(true));
  };
  useEffect(readSession, []);

  useEffect(() => {
    if (session?.authenticated) navigate(afterLogin(session, next), { replace: true });
  }, [session, next, navigate]);

  const caps = session?.capabilities ?? {};
  const emailOn = caps.email_login === true;
  const googleOn = caps.google_login === true;

  const go = (s: Step) => {
    setError(null);
    setTrialNotice(false);
    setStep(s);
  };

  const sendCode = async (e?: FormEvent) => {
    e?.preventDefault();
    const identifier = email.trim();
    if (!EMAIL.test(identifier)) {
      setError(MESSAGES.INVALID_IDENTIFIER ?? null);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = (await auth.postAuthChallenges({ method: 'email', identifier })) as Record<string, unknown>;
      const resendAt = typeof res.resend_available_at === 'string' ? Date.parse(res.resend_available_at) : Date.now() + Number(res.resend_after ?? 60) * 1000;
      setChallenge({ flowId: String(res.flow_id ?? ''), challengeId: String(res.challenge_id ?? ''), resendAt, resent: false });
      setCode('');
      setStep('code');
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const verify = async (e?: FormEvent) => {
    e?.preventDefault();
    if (!challenge || !/^\d{6}$/.test(code)) return;
    setBusy(true);
    setError(null);
    try {
      // auth/verify replaces the session in the client; the effect above routes onward.
      await auth.postAuthVerify({ challenge_id: challenge.challengeId, code });
      setSession(await loadSession());
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const resend = async () => {
    if (!challenge) return;
    setBusy(true);
    setError(null);
    try {
      const res = (await auth.postAuthResend({ flow_id: challenge.flowId })) as Record<string, unknown>;
      const resendAt = typeof res.resend_available_at === 'string' ? Date.parse(res.resend_available_at) : Date.now() + Number(res.resend_after ?? 60) * 1000;
      setChallenge({ flowId: challenge.flowId, challengeId: String(res.challenge_id ?? challenge.challengeId), resendAt, resent: true });
    } catch (err) {
      setError(message(err));
    } finally {
      setBusy(false);
    }
  };

  const backToStart = () => {
    if (challenge) void auth.postAuthCancel({ flow_id: challenge.flowId }).catch(() => undefined);
    setChallenge(null);
    setCode('');
    go('start');
  };

  const startGoogle = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = (await auth.postAuthGoogleStart({ intent: 'login', return_to: '/login?next=' + encodeURIComponent(next), language: 'zh' })) as Record<string, unknown>;
      if (typeof res.authorization_url !== 'string') throw new ApiError(0, 'BAD_RESPONSE', '');
      window.location.assign(res.authorization_url);
    } catch (err) {
      setError(message(err));
      setBusy(false);
    }
  };

  let card;
  if (!session && !sessionFailed) {
    card = (
      <section className="sg-authcard">
        <LoadingState rows={2} />
      </section>
    );
  } else if (step === 'google') {
    card = <GoogleContinueCard onContinue={startGoogle} onBack={() => go('start')} busy={busy} {...(error ? { error } : {})} />;
  } else if (step === 'trial') {
    card = (
      <section className="sg-authcard">
        <SageLogo />
        <h1>本机试用</h1>
        <p>数据仅保存在本机</p>
        {trialNotice && <InlineNotice icon="info" tone="muted" text="本机试用需要桌面版的本机存储，当前环境暂不支持，请先登录" />}
        <Button variant="primary" size={42} block label="进入本机试用" onClick={() => setTrialNotice(true)} />
        <Button variant="secondary" size={42} block label="返回登录" onClick={() => go('start')} />
      </section>
    );
  } else if (step === 'code' && challenge) {
    const wait = Math.max(0, Math.ceil((challenge.resendAt - now) / 1000));
    card = (
      <form className="sg-authcard" onSubmit={verify} noValidate>
        <SageLogo />
        <h1>输入邮箱验证码</h1>
        <p>{challenge.resent ? '验证码已重新发送，请查收邮箱' : '验证码已发送，请查收邮箱'}</p>
        <TextField
          autoFocus
          size={42}
          value={code}
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={6}
          aria-label="邮箱验证码"
          placeholder="6 位验证码"
          invalid={!!error}
          onValueChange={(v) => setCode(v.replace(/\D/g, '').slice(0, 6))}
        />
        {error && <p className="sg-authcard__error" role="alert">{error}</p>}
        <Button type="submit" variant="primary" size={42} block label="验证并继续" busy={busy} disabled={code.length !== 6} />
        <Button variant="secondary" size={42} block label="返回登录" onClick={backToStart} />
        <p className="sg-authcard__fine">
          {challenge.resent ? (
            '没有收到？请检查垃圾邮件'
          ) : wait > 0 ? (
            `没有收到？${wait} 秒后可重新发送`
          ) : (
            <button type="button" className="sg-authcard__link" onClick={resend} disabled={busy}>
              没有收到？重新发送
            </button>
          )}
        </p>
      </form>
    );
  } else {
    const terms = legalHref(caps.registration_terms_url);
    const privacy = legalHref(caps.registration_privacy_url);
    const off = [!googleOn && 'Google 登录', !emailOn && '邮箱验证码登录'].filter(Boolean).join('与');
    card = (
      <form className="sg-authcard sg-authcard--login" onSubmit={sendCode} noValidate>
        <SageLogo />
        <h1>登录 Sage</h1>
        <p>出海品牌的 AI 数字同事与多专家协作工作台</p>
        {sessionFailed && <InlineNotice icon="info" tone="error" text="无法连接服务器，请检查网络后重试" action="重试" onAction={readSession} />}
        <Button variant="secondary" size={42} block icon="globe" label="使用 Google 账号继续" disabled={!googleOn} onClick={() => go('google')} />
        <div className="sg-authcard__divider">或使用邮箱</div>
        <TextField
          size={42}
          type="email"
          value={email}
          autoComplete="email"
          aria-label="邮箱"
          placeholder="name@company.com"
          invalid={!!error}
          disabled={!emailOn}
          onValueChange={setEmail}
        />
        {error && <p className="sg-authcard__error" role="alert">{error}</p>}
        <Button type="submit" variant="primary" size={42} block label="发送登录验证码" busy={busy} disabled={!emailOn || !email.trim()} />
        {session && off && <InlineNotice icon="info" tone="muted" text={`${off}暂未开放，请稍后再试`} />}
        <p className="sg-authcard__legal">
          继续即表示同意{terms ? <a href={terms}>《服务条款》</a> : '《服务条款》'}与{privacy ? <a href={privacy}>《隐私政策》</a> : '《隐私政策》'}。
          <br />
          想先体验？
          <button type="button" className="sg-authcard__link" onClick={() => go('trial')}>
            以本机试用身份进入
          </button>
          （数据只保存在本机）
        </p>
      </form>
    );
  }

  return <div className="sg-authpage">{card}</div>;
}
