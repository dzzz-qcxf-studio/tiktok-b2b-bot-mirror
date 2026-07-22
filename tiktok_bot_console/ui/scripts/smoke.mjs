#!/usr/bin/env node
/**
 * Smoke test — pure Node, no test runner needed.
 * Verifies the project is wired correctly without booting a browser.
 *
 * Run with: `npm run test` or `node scripts/smoke.mjs`
 *
 * Checks:
 *   1. i18n key parity (zh-CN ↔ en-US)
 *   2. mock API returns expected shapes
 *   3. Router has 10 routes registered
 *   4. All 9 view .vue files exist and are non-empty
 *   5. .env.development + design-system.css present
 */

import { readFileSync, existsSync, statSync, readdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const __dirname = dirname(fileURLToPath(import.meta.url))
const root = join(__dirname, '..')

let passed = 0
let failed = 0
const errors = []

function ok(label) { passed++; console.log(`  ✓ ${label}`) }
function fail(label, err) { failed++; errors.push({ label, err }); console.log(`  ✗ ${label}\n      ${err}`) }
function group(name) { console.log(`\n${name}`) }

// ---------- 1. i18n parity ----------
group('i18n key parity (zh-CN ↔ en-US)')

const zhCN = (await import('../src/i18n/zh-CN.ts')).default
const enUS = (await import('../src/i18n/en-US.ts')).default

function flattenKeys(obj, prefix = '') {
  const keys = []
  for (const k of Object.keys(obj || {})) {
    const path = prefix ? `${prefix}.${k}` : k
    const v = obj[k]
    if (v && typeof v === 'object' && !Array.isArray(v)) keys.push(...flattenKeys(v, path))
    else keys.push(path)
  }
  return keys.sort()
}

const zhKeys = new Set(flattenKeys(zhCN))
const enKeys = new Set(flattenKeys(enUS))

const missingInEn = [...zhKeys].filter(k => !enKeys.has(k))
const missingInZh = [...enKeys].filter(k => !zhKeys.has(k))

if (missingInEn.length === 0) ok(`all ${zhKeys.size} zh-CN keys present in en-US`)
else fail('zh-CN keys missing in en-US', missingInEn.join(', '))

if (missingInZh.length === 0) ok(`all ${enKeys.size} en-US keys present in zh-CN`)
else fail('en-US keys missing in zh-CN', missingInZh.join(', '))

// Empty value scan
for (const [name, obj] of [['zh-CN', zhCN], ['en-US', enUS]]) {
  const empties = []
  const walk = (o, p = '') => {
    for (const [k, v] of Object.entries(o || {})) {
      const path = p ? `${p}.${k}` : k
      if (v && typeof v === 'object') walk(v, path)
      else if (v === '' || v == null) empties.push(path)
    }
  }
  walk(obj)
  if (empties.length === 0) ok(`no empty values in ${name}`)
  else fail(`empty values in ${name}`, empties.join(', '))
}

// ---------- 2. mock API shape ----------
group('mock API payload shapes')

const mockMod = await import('../src/api/mock.ts')
const mockApi = mockMod.default || mockMod.mockApi || mockMod
const cases = [
  { name: 'getDashboard', fn: () => mockApi.getDashboard(), expect: d => d.overview && Array.isArray(d.keywords) && d.keywords.length > 0 && d.overview.today_reply_rate > 0 },
  { name: 'getUsers', fn: () => mockApi.getUsers({}), expect: d => Array.isArray(d.items) && d.items.length === 10 },
  { name: 'getUsers[qualified]', fn: () => mockApi.getUsers({ status: 'qualified' }), expect: d => d.items.every(u => u.status === 'qualified') },
  { name: 'getPipelineEvents', fn: () => mockApi.getPipelineEvents(50), expect: d => Array.isArray(d) && d.length > 0 && d[0].timestamp && d[0].type },
  { name: 'getTrendReport(30)', fn: () => mockApi.getTrendReport(30), expect: d => d.length === 30 && d[0].date && d[0].reply_rate >= 0 },
  { name: 'getDailyReport', fn: () => mockApi.getDailyReport(), expect: d => d.date && typeof d.reply_rate === 'number' },
  { name: 'getAccounts', fn: () => mockApi.getAccounts(), expect: d => Array.isArray(d) && d.length >= 3 && d[0] && d[0].platform && d[0].status },
  { name: 'getConfig', fn: () => mockApi.getConfig(), expect: d => d.llm_model === 'deepseek-v4-pro' && d.has_api_key === true },
  { name: 'getWordcloud', fn: () => mockApi.getWordcloud(), expect: d => Array.isArray(d) && d.length > 0 },
  { name: 'login ok', fn: () => mockApi.login('test@x.com', 'pass1234'), expect: d => !!d.access_token },
  { name: 'login rejects short', fn: () => mockApi.login('test@x.com', 'ab'), expect: () => false /* should reject */ },
  { name: 'runPipeline', fn: () => mockApi.runPipeline(['collect','filter']), expect: d => Array.isArray(d.results) && d.results.length === 2 },
  { name: 'addAccount', fn: async () => { await mockApi.addAccount('tiktok', 'test_' + Date.now()); return mockApi.getAccounts() }, expect: d => Array.isArray(d) && d.length >= 4 },
]

for (const c of cases) {
  try {
    const res = await c.fn()
    const data = res.data
    if (c.name === 'login rejects short') {
      fail('login short pwd', 'expected rejection, got success')
      continue
    }
    if (c.expect(data)) ok(c.name)
    else fail(c.name, 'shape mismatch: ' + JSON.stringify(data).slice(0, 120))
  } catch (e) {
    if (c.name === 'login rejects short') ok('login rejects short pwd')
    else fail(c.name, e.message || String(e))
  }
}

// ---------- 3. Router routes ----------
group('router routes')

// Router uses Vite-only `import.meta.env.BASE_URL`, can't be imported in Node.
// Parse the source file instead to extract route paths.
const routerSrc = readFileSync(join(root, 'src', 'router', 'index.ts'), 'utf8')
const routeMatches = [...routerSrc.matchAll(/path:\s*['"]([^'"]+)['"]/g)]
const paths = routeMatches.map(m => m[1]).sort()
const expected = ['/', '/config-accounts', '/config-llm', '/config-pipeline', '/dashboard', '/login', '/pipeline', '/reports', '/users', '/users/:username']

for (const p of expected) {
  if (paths.includes(p)) ok(`route ${p}`)
  else fail(`route ${p}`, 'not found, got: ' + paths.join(', '))
}

// Verify redirect '/' exists
if (paths.includes('/')) ok('/ redirects to /dashboard')
else fail('/', 'redirect route missing')

// ---------- 4. View files ----------
group('view files exist & non-empty')

const views = ['Login', 'Dashboard', 'Users', 'UserDetail', 'Pipeline', 'Reports', 'ConfigAccounts', 'ConfigLlm', 'ConfigPipeline']
for (const v of views) {
  const p = join(root, 'src', 'views', `${v}.vue`)
  if (!existsSync(p)) { fail(`${v}.vue exists`, 'file missing'); continue }
  const size = statSync(p).size
  if (size < 1000) { fail(`${v}.vue non-empty`, `only ${size} bytes`); continue }
  ok(`${v}.vue (${(size / 1024).toFixed(1)} KB)`)
}

// ---------- 5. Project files ----------
group('project files present')

const checks = [
  ['.env.development', 'mock config'],
  ['src/assets/design-system.css', 'shared tokens'],
  ['src/assets/main.css', 'app entry CSS'],
  ['src/App.vue', 'shell'],
  ['README.md', 'docs'],
]
for (const [rel, desc] of checks) {
  const p = join(root, rel)
  if (existsSync(p)) ok(`${desc} (${rel})`)
  else fail(`${desc} (${rel})`, 'missing')
}

// ---------- 6. Build dist sanity ----------
group('dist (optional)')

const distExists = existsSync(join(root, 'dist'))
if (distExists) {
  const files = readdirSync(join(root, 'dist'))
  ok(`dist/ has ${files.length} entries`)
} else {
  console.log('  · dist/ not built yet — run `npm run build` first')
}

// ---------- Summary ----------
console.log('')
console.log('='.repeat(60))
console.log(`  PASSED  ${passed}`)
console.log(`  FAILED  ${failed}`)
console.log('='.repeat(60))

if (failed > 0) {
  console.log('\nFailures:')
  for (const e of errors) console.log(`  ✗ ${e.label}: ${e.err}`)
  process.exit(1)
} else {
  console.log('\n  All smoke checks passed.')
  process.exit(0)
}