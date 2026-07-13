const capabilities = [
  ['资料归一', 'PDF、文本与网页进入统一知识流程'],
  ['主动学习', '讲解、费曼复述与可解释判题'],
  ['持续复习', '基于学习事实安排下一次练习'],
] as const

export function App() {
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
          M1 基础设施正在建立
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