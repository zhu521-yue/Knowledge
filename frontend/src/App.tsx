import { useMemo, useState } from 'react'

type ApiUser = {
  id: string
  email: string
  display_name: string
  role: string
  is_active: boolean
}

type StepState = 'idle' | 'running' | 'ok' | 'error'

type FlowStep = {
  key: string
  label: string
  state: StepState
  detail: string
}

const capabilities = [
  ['资料归一', 'PDF、文本与网页进入统一知识流程'],
  ['主动学习', '讲解、费曼复述与可解释判题'],
  ['持续复习', '基于学习事实安排下一次练习'],
] as const

const initialSteps: FlowStep[] = [
  { key: 'bootstrap', label: '初始化首个管理员', state: 'idle', detail: '等待执行' },
  { key: 'invite', label: '管理员创建邀请码', state: 'idle', detail: '等待执行' },
  { key: 'register', label: '邀请码注册用户', state: 'idle', detail: '等待执行' },
  { key: 'login', label: '密码登录校验', state: 'idle', detail: '等待执行' },
  { key: 'invalid', label: '无效邀请码拒绝', state: 'idle', detail: '等待执行' },
  { key: 'disable', label: '账号停用后拒绝登录', state: 'idle', detail: '等待执行' },
]

const apiBaseUrl = `${window.location.protocol}//${window.location.hostname}:8000`

function uniqueIdentitySeed() {
  return Date.now().toString(36)
}

async function postJson(path: string, body: unknown, headers?: Record<string, string>) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
    body: JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  return { response, payload }
}

async function patchJson(path: string, body: unknown, headers?: Record<string, string>) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'PATCH',
    headers: {
      'Content-Type': 'application/json',
      ...headers,
    },
    body: JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  return { response, payload }
}

export function App() {
  const [steps, setSteps] = useState<FlowStep[]>(initialSteps)
  const [isRunning, setIsRunning] = useState(false)
  const [admin, setAdmin] = useState<ApiUser | null>(null)
  const [member, setMember] = useState<ApiUser | null>(null)
  const [invitationCode, setInvitationCode] = useState<string | null>(null)

  const finishedCount = useMemo(
    () => steps.filter((step) => step.state === 'ok').length,
    [steps],
  )

  const setStep = (key: string, state: StepState, detail: string) => {
    setSteps((current) =>
      current.map((step) => (step.key === key ? { ...step, state, detail } : step)),
    )
  }

  const runIdentityFlow = async () => {
    const seed = uniqueIdentitySeed()
    const adminEmail = `admin-${seed}@example.test`
    const memberEmail = `learner-${seed}@example.test`
    const password = 'correct horse battery staple'
    const code = `VERIFY-${seed.toUpperCase()}`

    setIsRunning(true)
    setSteps(initialSteps)
    setAdmin(null)
    setMember(null)
    setInvitationCode(null)

    try {
      setStep('bootstrap', 'running', `请求 ${adminEmail}`)
      const bootstrap = await postJson('/auth/bootstrap-admin', {
        email: adminEmail,
        password,
        display_name: 'Local Admin',
      })
      if (![201, 409].includes(bootstrap.response.status)) {
        throw new Error(`初始化失败：${bootstrap.payload.detail ?? bootstrap.response.status}`)
      }
      const activeAdmin = bootstrap.payload.user as ApiUser | undefined
      if (!activeAdmin) {
        throw new Error('当前数据库已有管理员，请清空本地数据卷后重新验证初始化流程')
      }
      setAdmin(activeAdmin)
      setStep('bootstrap', 'ok', `管理员 ${activeAdmin.email} 已创建`)

      setStep('invite', 'running', `创建邀请码 ${code}`)
      const invitation = await postJson(
        '/auth/invitations',
        { code, max_uses: 1 },
        { 'X-Actor-User-Id': activeAdmin.id },
      )
      if (invitation.response.status !== 201) {
        throw new Error(`邀请码创建失败：${invitation.payload.detail ?? invitation.response.status}`)
      }
      const issuedCode = invitation.payload.invitation.code as string
      setInvitationCode(issuedCode)
      setStep('invite', 'ok', `邀请码 ${issuedCode} 已创建`)

      setStep('register', 'running', `注册 ${memberEmail}`)
      const registration = await postJson('/auth/register', {
        email: memberEmail,
        password,
        display_name: 'Learner',
        invitation_code: issuedCode,
      })
      if (registration.response.status !== 201) {
        throw new Error(`注册失败：${registration.payload.detail ?? registration.response.status}`)
      }
      const registeredMember = registration.payload.user as ApiUser
      setMember(registeredMember)
      setStep('register', 'ok', `用户 ${registeredMember.email} 已注册`)

      setStep('login', 'running', `登录 ${memberEmail}`)
      const login = await postJson('/auth/login', { email: memberEmail, password })
      if (login.response.status !== 200) {
        throw new Error(`登录失败：${login.payload.detail ?? login.response.status}`)
      }
      setStep('login', 'ok', `登录成功，角色 ${login.payload.user.role}`)

      setStep('invalid', 'running', '尝试使用无效邀请码注册')
      const invalid = await postJson('/auth/register', {
        email: `invalid-${seed}@example.test`,
        password,
        display_name: 'Invalid Invite',
        invitation_code: 'MISSING-CODE',
      })
      if (invalid.response.status !== 404 || invalid.payload.detail !== 'invitation_not_found') {
        throw new Error('无效邀请码没有被正确拒绝')
      }
      setStep('invalid', 'ok', '无效邀请码已按预期拒绝')

      setStep('disable', 'running', `停用 ${memberEmail} 后再次登录`)
      const disabled = await patchJson(
        `/auth/users/${registeredMember.id}/status`,
        { is_active: false },
        { 'X-Actor-User-Id': activeAdmin.id },
      )
      if (disabled.response.status !== 200) {
        throw new Error(`停用失败：${disabled.payload.detail ?? disabled.response.status}`)
      }
      const disabledLogin = await postJson('/auth/login', { email: memberEmail, password })
      if (disabledLogin.response.status !== 403 || disabledLogin.payload.detail !== 'user_disabled') {
        throw new Error('停用账号仍然可以登录')
      }
      setMember(disabled.payload.user as ApiUser)
      setStep('disable', 'ok', '停用账号已按预期拒绝登录')
    } catch (error) {
      const message = error instanceof Error ? error.message : '未知错误'
      setSteps((current) =>
        current.map((step) =>
          step.state === 'running' ? { ...step, state: 'error', detail: message } : step,
        ),
      )
    } finally {
      setIsRunning(false)
    }
  }

  const disableMember = async () => {
    if (!admin || !member) return
    setIsRunning(true)
    try {
      const disabled = await patchJson(
        `/auth/users/${member.id}/status`,
        { is_active: false },
        { 'X-Actor-User-Id': admin.id },
      )
      if (disabled.response.status !== 200) {
        throw new Error(`停用失败：${disabled.payload.detail ?? disabled.response.status}`)
      }
      setMember(disabled.payload.user as ApiUser)
    } finally {
      setIsRunning(false)
    }
  }

  return (
    <main className="shell">
      <section className="hero" aria-labelledby="page-title">
        <p className="eyebrow">LOCAL-FIRST KNOWLEDGE WORKSPACE</p>
        <h1 id="page-title">把资料变成真正掌握的知识</h1>
        <p className="lead">
          Knowledge 将导入、理解、练习和复习收束为一条可追踪的学习闭环。
        </p>
        <div className="status" role="status">
          <span aria-hidden="true" />
          M1 身份边界正在建立
        </div>
      </section>

      <section className="identity-panel" aria-labelledby="identity-title">
        <div>
          <p className="eyebrow">LEA-19 FRONTEND CHECK</p>
          <h2 id="identity-title">本地身份初始化验证</h2>
          <p>
            一键验证首个管理员初始化、邀请码注册、密码登录和无效邀请码拒绝。
            这不是最终登录页，而是 M1 阶段的用户可见验收入口。
          </p>
        </div>
        <button type="button" onClick={runIdentityFlow} disabled={isRunning}>
          {isRunning ? '验证中...' : '运行身份流程验证'}
        </button>
        <ol className="flow-steps">
          {steps.map((step) => (
            <li key={step.key} data-state={step.state}>
              <strong>{step.label}</strong>
              <span>{step.detail}</span>
            </li>
          ))}
        </ol>
        <div className="identity-result" role="status">
          <span>完成进度：{finishedCount} / {steps.length}</span>
          {invitationCode ? <span>邀请码：{invitationCode}</span> : null}
          {member ? (
            <span>
              注册用户：{member.email}，状态：{member.is_active ? '启用' : '停用'}
            </span>
          ) : null}
          <button type="button" onClick={disableMember} disabled={!admin || !member || isRunning}>
            停用注册用户
          </button>
        </div>
      </section>

      <section className="capabilities" aria-label="核心能力">
        {capabilities.map(([title, detail], index) => (
          <article key={title}>
            <small>0{index + 1}</small>
            <h2>{title}</h2>
            <p>{detail}</p>
          </article>
        ))}
      </section>
    </main>
  )
}