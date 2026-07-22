import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { login as apiLogin, register as apiRegister, me as apiMe } from '../api'

export const useAuthStore = defineStore('auth', () => {
  const token = ref(localStorage.getItem('token') || '')
  const username = ref(localStorage.getItem('username') || '')
  const isAuthenticated = computed(() => !!token.value)

  async function login(u: string, p: string, _method = 'password') {
    const { data } = await apiLogin(u, p)
    token.value = data.access_token
    username.value = data.username
    localStorage.setItem('token', data.access_token)
    localStorage.setItem('username', data.username)
  }

  async function register(u: string, p: string, invite = '') {
    const { data } = await apiRegister(u, p, invite)
    return data
  }

  function logout() {
    token.value = ''; username.value = ''
    localStorage.removeItem('token'); localStorage.removeItem('username')
  }

  async function checkAuth() {
    try {
      const { data } = await apiMe()
      return data.authenticated
    } catch { return false }
  }

  return { token, username, isAuthenticated, login, register, logout, checkAuth }
})