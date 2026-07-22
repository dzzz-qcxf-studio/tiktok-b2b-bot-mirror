import { createRouter, createWebHistory } from 'vue-router'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/login', name: 'login', component: () => import('../views/Login.vue') },
    { path: '/', redirect: '/dashboard' },
    { path: '/dashboard', name: 'dashboard', component: () => import('../views/Dashboard.vue'), meta: { requiresAuth: true } },
    { path: '/users', name: 'users', component: () => import('../views/Users.vue'), meta: { requiresAuth: true } },
    { path: '/users/:username', name: 'user-detail', component: () => import('../views/UserDetail.vue'), meta: { requiresAuth: true } },
    { path: '/leads', name: 'leads', component: () => import('../views/Leads.vue'), meta: { requiresAuth: true } },
    { path: '/pipeline', name: 'pipeline', component: () => import('../views/Pipeline.vue'), meta: { requiresAuth: true } },
    { path: '/reports', name: 'reports', component: () => import('../views/Reports.vue'), meta: { requiresAuth: true } },
    { path: '/config-accounts', name: 'config-accounts', component: () => import('../views/ConfigAccounts.vue'), meta: { requiresAuth: true } },
    { path: '/config-llm',      name: 'config-llm',      component: () => import('../views/ConfigLlm.vue'),      meta: { requiresAuth: true } },
    { path: '/config-pipeline', name: 'config-pipeline', component: () => import('../views/ConfigPipeline.vue'), meta: { requiresAuth: true } },
    { path: '/:pathMatch(.*)*', name: 'not-found', component: () => import('../views/NotFound.vue') },
  ]
})

router.beforeEach((to, _from, next) => {
  const token = localStorage.getItem('token')
  if (to.meta.requiresAuth && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/dashboard')
  } else {
    next()
  }
})

export default router