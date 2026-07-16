import { useMemo, useState } from 'react'

type ApiUser = {
  id: string
  email: string
  display_name: string
  role: string
  is_active: boolean
}

type RetrievalEvidence = {
  child_chunk_id: string
  text: string
  page_start: number
  page_end: number
  dense_rank: number | null
  sparse_rank: number | null
  rrf_score: number
}

type RetrievalParent = {
  parent_chunk_id: string
  source_document_id: string
  heading_path: string[]
  page_start: number
  page_end: number
  text: string
  score: number
  evidence: RetrievalEvidence[]
}

type SourceImportResponse = {
  source: {
    id: string
    title: string
    revision_id: string
    original_url: string | null
    final_url: string | null
    fetched_at: string | null
  }
  ingestion_run: {
    id: string
    status: string
    checkpoint: string
    progress: number
  }
  repeated: boolean
}

type LifecycleSource = {
  id: string
  title: string
  input_type: string
  state: 'active' | 'archived' | 'trashed' | 'purging' | 'purged'
  active_revision_id: string | null
  version: number
  purge_after: string | null
}

type RetrievalResponse = {
  retrieval_version: string
  active_run_ids: string[]
  parents: RetrievalParent[]
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
  { key: 'health', label: '检查 API 与依赖健康状态', state: 'idle', detail: '等待执行' },
  { key: 'bootstrap', label: '初始化或登录验收管理员', state: 'idle', detail: '等待执行' },
  { key: 'invite', label: '管理员创建邀请码', state: 'idle', detail: '等待执行' },
  { key: 'register', label: '邀请码注册用户', state: 'idle', detail: '等待执行' },
  { key: 'login', label: '登录并写入安全 Cookie', state: 'idle', detail: '等待执行' },
  { key: 'me', label: '读取当前会话用户', state: 'idle', detail: '等待执行' },
  { key: 'refresh', label: '续期当前 Session', state: 'idle', detail: '等待执行' },
  { key: 'credential', label: '加密保存 Provider 凭据', state: 'idle', detail: '等待执行' },
  { key: 'logout', label: '登出并清除 Cookie', state: 'idle', detail: '等待执行' },
  { key: 'invalid', label: '无效邀请码拒绝', state: 'idle', detail: '等待执行' },
  { key: 'disable', label: '账号停用后拒绝登录', state: 'idle', detail: '等待执行' },
]

const apiBaseUrl = `${window.location.protocol}//${window.location.hostname}:8000`
const identityPassword = 'correct horse battery staple'

function uniqueIdentitySeed() {
  return Date.now().toString(36)
}

async function postJson(path: string, body?: unknown, extraHeaders: Record<string, string> = {}) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: body === undefined
      ? extraHeaders
      : { 'Content-Type': 'application/json', ...extraHeaders },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  return { response, payload }
}

async function postForm(path: string, form: FormData, extraHeaders: Record<string, string> = {}) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: extraHeaders,
    body: form,
  })
  const payload = await response.json().catch(() => ({}))
  return { response, payload }
}

async function getJson(path: string) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'GET',
    credentials: 'include',
  })
  const payload = await response.json().catch(() => ({}))
  return { response, payload }
}

async function putJson(path: string, body: unknown) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'PUT',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  return { response, payload }
}

async function patchJson(path: string, body: unknown) {
  const response = await fetch(`${apiBaseUrl}${path}`, {
    method: 'PATCH',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const payload = await response.json().catch(() => ({}))
  return { response, payload }
}

export function App() {
  const [steps, setSteps] = useState<FlowStep[]>(initialSteps)
  const [isRunning, setIsRunning] = useState(false)
  const [adminEmail, setAdminEmail] = useState(
    () => window.localStorage.getItem('knowledge.m1.adminEmail') ?? '',
  )
  const [admin, setAdmin] = useState<ApiUser | null>(null)
  const [member, setMember] = useState<ApiUser | null>(null)
  const [invitationCode, setInvitationCode] = useState<string | null>(null)
  const [retrievalTopicId, setRetrievalTopicId] = useState('')
  const [sourceUrl, setSourceUrl] = useState('')
  const [sourceTitle, setSourceTitle] = useState('')
  const [sourceText, setSourceText] = useState('')
  const [sourcePdf, setSourcePdf] = useState<File | null>(null)
  const [sourceImport, setSourceImport] = useState<SourceImportResponse | null>(null)
  const [localSourceImport, setLocalSourceImport] = useState<SourceImportResponse | null>(null)
  const [localSourceImportError, setLocalSourceImportError] = useState('')
  const [sourceImportError, setSourceImportError] = useState('')
  const [isImportingSource, setIsImportingSource] = useState(false)
  const [lifecycleSources, setLifecycleSources] = useState<LifecycleSource[]>([])
  const [lifecycleError, setLifecycleError] = useState('')
  const [isManagingSources, setIsManagingSources] = useState(false)
  const [retrievalQuery, setRetrievalQuery] = useState('')
  const [retrievalResult, setRetrievalResult] = useState<RetrievalResponse | null>(null)
  const [retrievalError, setRetrievalError] = useState('')
  const [isRetrieving, setIsRetrieving] = useState(false)

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
    const verificationAdminEmail = adminEmail.trim().toLowerCase()
    const memberEmail = `learner-${seed}@example.test`
    const password = identityPassword
    const code = `VERIFY-${seed.toUpperCase()}`

    if (!verificationAdminEmail) {
      setStep('bootstrap', 'error', '请输入验收管理员邮箱')
      return
    }

    window.localStorage.setItem('knowledge.m1.adminEmail', verificationAdminEmail)

    setIsRunning(true)
    setSteps(initialSteps)
    setAdmin(null)
    setMember(null)
    setInvitationCode(null)

    try {
      setStep('health', 'running', '读取 API、数据库、存储和 Milvus 状态')
      const health = await getJson('/health/ready')
      const requestId = health.response.headers.get('X-Request-ID')
      if (health.response.status !== 200 || health.payload.status !== 'ok' || !requestId) {
        throw new Error(`健康检查失败：${health.payload.status ?? health.response.status}`)
      }
      const dependencies = Object.entries(health.payload.dependencies as Record<string, string>)
        .map(([name, state]) => `${name}=${state}`)
        .join('，')
      setStep('health', 'ok', `${dependencies}；request_id=${requestId}`)

      setStep('bootstrap', 'running', `初始化或登录 ${verificationAdminEmail}`)
      const bootstrap = await postJson('/auth/bootstrap-admin', {
        email: verificationAdminEmail,
        password,
        display_name: 'Local Admin',
      })
      if (![201, 409].includes(bootstrap.response.status)) {
        throw new Error(`初始化失败：${bootstrap.payload.detail ?? bootstrap.response.status}`)
      }
      const adminLogin = await postJson('/auth/login', {
        email: verificationAdminEmail,
        password,
      })
      if (adminLogin.response.status !== 200) {
        const detail = bootstrap.response.status === 409
          ? '管理员已存在，请填写首次验证时创建的管理员邮箱'
          : (adminLogin.payload.detail ?? adminLogin.response.status)
        throw new Error(`管理员登录失败：${detail}`)
      }
      const activeAdmin = adminLogin.payload.user as ApiUser
      if (activeAdmin.role !== 'admin' || !activeAdmin.is_active) {
        throw new Error('验收账号不是启用状态的管理员')
      }
      setAdmin(activeAdmin)
      setStep(
        'bootstrap',
        'ok',
        bootstrap.response.status === 201
          ? `管理员 ${activeAdmin.email} 已创建并登录`
          : `已有管理员 ${activeAdmin.email} 已登录`,
      )

      setStep('invite', 'running', `通过管理员 Session 创建邀请码 ${code}`)
      const invitation = await postJson('/auth/invitations', { code, max_uses: 1 })
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
      setStep('login', 'ok', `登录成功，HttpOnly Cookie 已由浏览器保存`)

      setStep('me', 'running', '通过 Cookie 读取当前用户')
      const me = await getJson('/auth/me')
      if (me.response.status !== 200 || me.payload.user.id !== registeredMember.id) {
        throw new Error(`会话读取失败：${me.payload.detail ?? me.response.status}`)
      }
      setStep('me', 'ok', `当前用户 ${me.payload.user.email}`)

      setStep('refresh', 'running', '续期当前 Session')
      const refreshed = await postJson('/auth/session/refresh')
      if (refreshed.response.status !== 200 || refreshed.payload.user.id !== registeredMember.id) {
        throw new Error(`续期失败：${refreshed.payload.detail ?? refreshed.response.status}`)
      }
      setStep('refresh', 'ok', 'Session 已续期')

      setStep('credential', 'running', '保存测试凭据并检查脱敏响应')
      const providerSecret = `sk-local-${seed}-4321`
      const credential = await putJson('/provider-credentials/openai', {
        secret: providerSecret,
      })
      const credentialList = await getJson('/provider-credentials')
      const maskedSecret = credential.payload.credential?.masked_secret as string | undefined
      if (
        credential.response.status !== 200 ||
        credentialList.response.status !== 200 ||
        !maskedSecret?.endsWith('4321') ||
        maskedSecret.includes(providerSecret) ||
        JSON.stringify(credentialList.payload).includes(providerSecret)
      ) {
        throw new Error('Provider 凭据未被正确加密或脱敏')
      }
      setStep('credential', 'ok', `OpenAI 凭据仅显示为 ${maskedSecret}`)

      setStep('logout', 'running', '登出并清除 Cookie')
      const loggedOut = await postJson('/auth/logout')
      const meAfterLogout = await getJson('/auth/me')
      if (loggedOut.response.status !== 204 || meAfterLogout.response.status !== 401) {
        throw new Error('登出后仍可读取当前用户')
      }
      setStep('logout', 'ok', '登出后当前用户为空')

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

      setStep('disable', 'running', `以管理员 Session 停用 ${memberEmail} 后再次登录`)
      const adminRelogin = await postJson('/auth/login', {
        email: verificationAdminEmail,
        password,
      })
      if (adminRelogin.response.status !== 200) {
        throw new Error(`管理员重新登录失败：${adminRelogin.payload.detail ?? adminRelogin.response.status}`)
      }
      const disabled = await patchJson(`/auth/users/${registeredMember.id}/status`, {
        is_active: false,
      })
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

  const runSourceImport = async () => {
    setIsImportingSource(true)
    setSourceImportError('')
    setSourceImport(null)
    try {
      const result = await postJson(
        '/sources/url',
        { topic_id: retrievalTopicId.trim(), url: sourceUrl.trim() },
        { 'Idempotency-Key': crypto.randomUUID() },
      )
      if (result.response.status !== 201) {
        const detail = typeof result.payload.detail === 'object'
          ? result.payload.detail.code
          : result.payload.detail
        throw new Error(detail ?? `HTTP ${result.response.status}`)
      }
      setSourceImport(result.payload as SourceImportResponse)
    } catch (error) {
      setSourceImportError(error instanceof Error ? error.message : '网页导入失败')
    } finally {
      setIsImportingSource(false)
    }
  }

  const runLocalSourceImport = async (kind: 'text' | 'pdf') => {
    setIsImportingSource(true)
    setLocalSourceImportError('')
    setLocalSourceImport(null)
    try {
      const result = kind === 'text'
        ? await postJson(
          '/sources/text',
          {
            topic_id: retrievalTopicId.trim(),
            title: sourceTitle.trim(),
            content: sourceText,
          },
          { 'Idempotency-Key': crypto.randomUUID() },
        )
        : await (async () => {
          const form = new FormData()
          form.set('topic_id', retrievalTopicId.trim())
          form.set('title', sourceTitle.trim())
          form.set('file', sourcePdf as File)
          return postForm('/sources/pdf', form, { 'Idempotency-Key': crypto.randomUUID() })
        })()
      if (result.response.status !== 201) {
        throw new Error(result.payload.detail ?? `HTTP ${result.response.status}`)
      }
      setLocalSourceImport(result.payload as SourceImportResponse)
    } catch (error) {
      setLocalSourceImportError(error instanceof Error ? error.message : '本地资料导入失败')
    } finally {
      setIsImportingSource(false)
    }
  }

  const loadSources = async () => {
    setIsManagingSources(true)
    setLifecycleError('')
    try {
      const result = await getJson('/sources?state=all')
      if (result.response.status !== 200) {
        throw new Error(result.payload.detail ?? `HTTP ${result.response.status}`)
      }
      setLifecycleSources(result.payload.sources as LifecycleSource[])
    } catch (error) {
      setLifecycleError(error instanceof Error ? error.message : '资料读取失败')
    } finally {
      setIsManagingSources(false)
    }
  }

  const changeSourceState = async (source: LifecycleSource, command: string) => {
    setIsManagingSources(true)
    setLifecycleError('')
    try {
      const result = await postJson(
        `/sources/${source.id}/${command}`,
        { reason: 'M2 前端验收操作' },
        { 'If-Match': `"${source.version}"`, 'Idempotency-Key': crypto.randomUUID() },
      )
      if (result.response.status !== 200) {
        throw new Error(result.payload.detail ?? `HTTP ${result.response.status}`)
      }
      const updated = result.payload.source as LifecycleSource
      setLifecycleSources((current) => current.map((item) => item.id === updated.id ? updated : item))
    } catch (error) {
      setLifecycleError(error instanceof Error ? error.message : '状态变更失败')
    } finally {
      setIsManagingSources(false)
    }
  }

  const runRetrieval = async () => {
    setIsRetrieving(true)
    setRetrievalError('')
    setRetrievalResult(null)
    try {
      const result = await postJson('/retrieval', {
        topic_id: retrievalTopicId.trim(),
        query: retrievalQuery.trim(),
      })
      if (result.response.status !== 200) {
        throw new Error(result.payload.detail ?? `HTTP ${result.response.status}`)
      }
      setRetrievalResult(result.payload.retrieval as RetrievalResponse)
    } catch (error) {
      setRetrievalError(error instanceof Error ? error.message : '检索失败')
    } finally {
      setIsRetrieving(false)
    }
  }

  const disableMember = async () => {
    if (!admin || !member) return
    setIsRunning(true)
    try {
      const adminLogin = await postJson('/auth/login', {
        email: admin.email,
        password: identityPassword,
      })
      if (adminLogin.response.status !== 200) {
        throw new Error(`管理员登录失败：${adminLogin.payload.detail ?? adminLogin.response.status}`)
      }
      const disabled = await patchJson(`/auth/users/${member.id}/status`, {
        is_active: false,
      })
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
          <p className="eyebrow">M1 FRONTEND CHECK</p>
          <h2 id="identity-title">本地身份与会话验证</h2>
          <p>
            一键验证 API 与依赖健康状态、request_id、本地身份、HttpOnly Cookie 会话、
            Provider 凭据加密与脱敏、登出清除 Cookie、无效邀请码拒绝和停用账号失效。
            这不是最终登录页，而是 M1 阶段的用户可见验收入口。
          </p>
        </div>
        <label className="verification-field">
          <span>验收管理员邮箱</span>
          <input
            type="email"
            value={adminEmail}
            onChange={(event) => setAdminEmail(event.target.value)}
            placeholder="首次运行时填写；后续使用同一邮箱"
            autoComplete="username"
            disabled={isRunning}
          />
        </label>
        <button type="button" onClick={runIdentityFlow} disabled={isRunning || !adminEmail.trim()}>
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

      <section className="retrieval-panel source-import-panel" aria-labelledby="source-import-title">
        <div className="panel-heading">
          <p className="eyebrow">M2 WEB IMPORT CHECK</p>
          <h2 id="source-import-title">导入静态网页资料</h2>
          <p>提交一次明确授权的公开网页 URL。系统会执行 SSRF 防护、受限下载和正文快照，再创建待处理的不可变资料版本。</p>
        </div>
        <div className="retrieval-form">
          <label>
            <span>Topic ID</span>
            <input value={retrievalTopicId} onChange={(event) => setRetrievalTopicId(event.target.value)} placeholder="资料所属 Topic ID" />
          </label>
          <label>
            <span>公开静态网页 URL</span>
            <input type="url" value={sourceUrl} onChange={(event) => setSourceUrl(event.target.value)} placeholder="https://example.com/article" />
          </label>
          <button type="button" onClick={runSourceImport} disabled={isImportingSource || !retrievalTopicId.trim() || !sourceUrl.trim()}>
            {isImportingSource ? '抓取中...' : '导入网页'}
          </button>
        </div>
        {sourceImportError ? <p className="retrieval-error" role="alert">{sourceImportError}</p> : null}
        {sourceImport ? (
          <div className="source-import-result" role="status">
            <strong>{sourceImport.source.title}</strong>
            <span>资料 ID：{sourceImport.source.id}</span>
            <span>Revision：{sourceImport.source.revision_id}</span>
            <span>Run：{sourceImport.ingestion_run.id} · {sourceImport.ingestion_run.status} / {sourceImport.ingestion_run.checkpoint}</span>
            {sourceImport.source.final_url ? (
              <a href={sourceImport.source.final_url} target="_blank" rel="noreferrer">打开最终来源 URL</a>
            ) : null}
          </div>
        ) : null}
      </section>

      <section className="retrieval-panel source-import-panel" aria-labelledby="local-source-import-title">
        <div className="panel-heading">
          <p className="eyebrow">M2 PDF & TEXT IMPORT CHECK</p>
          <h2 id="local-source-import-title">导入文本与 PDF 资料</h2>
          <p>粘贴文本会先标准化；PDF 必须包含可提取文本。相同 Topic 内的相同内容会复用不可变资料版本，不重复解析。</p>
        </div>
        <div className="retrieval-form">
          <label>
            <span>资料标题</span>
            <input value={sourceTitle} onChange={(event) => setSourceTitle(event.target.value)} placeholder="例如：M2 验收笔记" />
          </label>
          <label>
            <span>粘贴文本</span>
            <textarea value={sourceText} onChange={(event) => setSourceText(event.target.value)} placeholder="粘贴需要导入的正文" rows={5} />
          </label>
          <button type="button" onClick={() => runLocalSourceImport('text')} disabled={isImportingSource || !retrievalTopicId.trim() || !sourceTitle.trim() || !sourceText.trim()}>
            {isImportingSource ? '导入中...' : '导入粘贴文本'}
          </button>
          <label>
            <span>文本型 PDF</span>
            <input type="file" accept="application/pdf,.pdf" onChange={(event) => setSourcePdf(event.target.files?.[0] ?? null)} />
          </label>
          <button type="button" onClick={() => runLocalSourceImport('pdf')} disabled={isImportingSource || !retrievalTopicId.trim() || !sourceTitle.trim() || !sourcePdf}>
            {isImportingSource ? '上传中...' : '上传 PDF'}
          </button>
        </div>
        {localSourceImportError ? <p className="retrieval-error" role="alert">{localSourceImportError}</p> : null}
        {localSourceImport ? (
          <div className="source-import-result" role="status">
            <strong>{localSourceImport.source.title}</strong>
            <span>资料 ID：{localSourceImport.source.id}</span>
            <span>Revision：{localSourceImport.source.revision_id}</span>
            <span>Run：{localSourceImport.ingestion_run.id} · {localSourceImport.ingestion_run.status}</span>
            <span>{localSourceImport.repeated ? '已复用相同内容' : '已创建不可变版本'}</span>
          </div>
        ) : null}
      </section>

      <section className="retrieval-panel lifecycle-panel" aria-labelledby="lifecycle-title">
        <div className="panel-heading">
          <p className="eyebrow">M2 SOURCE LIFECYCLE CHECK</p>
          <h2 id="lifecycle-title">资料归档与回收站</h2>
          <p>归档资料会退出检索但仍可恢复；移入回收站后立即停用并进入 30 天保留期；彻底删除只提交异步清理任务。</p>
        </div>
        <button className="secondary-action" type="button" onClick={loadSources} disabled={isManagingSources}>
          {isManagingSources ? '处理中...' : '读取我的资料'}
        </button>
        {lifecycleError ? <p className="retrieval-error" role="alert">{lifecycleError}</p> : null}
        <div className="lifecycle-list">
          {lifecycleSources.map((source) => (
            <article key={source.id}>
              <div>
                <strong>{source.title}</strong>
                <span>{source.state} · v{source.version}</span>
                {source.purge_after ? <small>保留至 {new Date(source.purge_after).toLocaleString()}</small> : null}
              </div>
              <div className="lifecycle-actions">
                {source.state === 'active' ? <button onClick={() => changeSourceState(source, 'archive')}>归档</button> : null}
                {source.state === 'archived' || source.state === 'trashed' ? <button onClick={() => changeSourceState(source, 'restore')}>恢复</button> : null}
                {source.state === 'active' || source.state === 'archived' ? <button onClick={() => changeSourceState(source, 'trash')}>移入回收站</button> : null}
                {source.state === 'trashed' ? <button className="danger" onClick={() => changeSourceState(source, 'purge')}>彻底删除</button> : null}
              </div>
            </article>
          ))}
        </div>
      </section>

      <section className="retrieval-panel" aria-labelledby="retrieval-title">
        <div className="panel-heading">
          <p className="eyebrow">M2 RETRIEVAL CHECK</p>
          <h2 id="retrieval-title">Topic 内 Top 3 检索</h2>
          <p>输入已发布资料所属的 Topic ID 和问题，检查 Dense、BM25 与 RRF 融合结果。此页面只展示资料证据，不调用 LLM 生成答案。</p>
        </div>
        <div className="retrieval-form">
          <label>
            <span>Topic ID</span>
            <input value={retrievalTopicId} onChange={(event) => setRetrievalTopicId(event.target.value)} placeholder="输入 Topic ID" />
          </label>
          <label>
            <span>检索问题</span>
            <textarea value={retrievalQuery} onChange={(event) => setRetrievalQuery(event.target.value)} placeholder="例如：RRF 如何融合两路排名？" rows={3} />
          </label>
          <button type="button" onClick={runRetrieval} disabled={isRetrieving || !retrievalTopicId.trim() || !retrievalQuery.trim()}>
            {isRetrieving ? '检索中...' : '运行检索'}
          </button>
        </div>
        {retrievalError ? <p className="retrieval-error" role="alert">{retrievalError}</p> : null}
        {retrievalResult ? (
          <div className="retrieval-results" aria-live="polite">
            <div className="retrieval-meta">
              <span>{retrievalResult.retrieval_version}</span>
              <span>{retrievalResult.active_run_ids.length} 个 active Run</span>
              <span>{retrievalResult.parents.length} 个 Parent</span>
            </div>
            {retrievalResult.parents.length === 0 ? <p className="empty-result">当前 Topic 没有可检索的已发布资料。</p> : null}
            {retrievalResult.parents.map((parent, index) => (
              <article key={parent.parent_chunk_id}>
                <header>
                  <strong>#{index + 1} {parent.heading_path.join(' / ') || '未命名章节'}</strong>
                  <span>第 {parent.page_start}–{parent.page_end} 页 · RRF {parent.score.toFixed(5)}</span>
                </header>
                <p>{parent.text}</p>
                <details>
                  <summary>{parent.evidence.length} 条命中 Child 证据</summary>
                  {parent.evidence.map((evidence) => (
                    <div className="evidence" key={evidence.child_chunk_id}>
                      <span>Dense #{evidence.dense_rank ?? '—'} · BM25 #{evidence.sparse_rank ?? '—'} · {evidence.rrf_score.toFixed(5)}</span>
                      <p>{evidence.text}</p>
                    </div>
                  ))}
                </details>
              </article>
            ))}
          </div>
        ) : null}
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