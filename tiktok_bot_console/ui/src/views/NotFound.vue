<template>
  <div class="nf">
    <div class="nf-code">404</div>
    <h1>页面不存在</h1>
    <p>你访问的路径没有匹配任何已配置的路由。</p>
    <pre class="nf-path">{{ $route.fullPath }}</pre>
    <router-link to="/dashboard" class="btn brand">{{ $t('common.goHome') }} →</router-link>
  </div>
</template>

<style scoped>
.nf { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height: calc(100vh - 200px); padding: 40px 20px; gap: 16px; text-align: center; }
.nf-code { font-family: var(--font-mono); font-size: 96px; font-weight: 700; line-height: 1; background: linear-gradient(135deg, var(--brand), var(--cyan)); -webkit-background-clip: text; background-clip: text; color: transparent; }
.nf h1 { font-size: 24px; font-weight: 700; margin: 0; }
.nf p { color: var(--muted); margin: 0; font-size: 14px; }
.nf-path { font-family: var(--font-mono); font-size: 12px; padding: 6px 12px; background: var(--bg-sub); border: 1px solid var(--border); border-radius: 6px; color: var(--fg-2); margin: 0; }
</style>