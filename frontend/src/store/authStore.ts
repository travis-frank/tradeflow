import { create } from "zustand"

import { clearToken, setToken } from "@/lib/api"
import type { User } from "@/lib/types"

interface AuthState {
  token: string | null
  user: User | null
  isAuthenticated: boolean
  setAuth: (token: string, user: User) => void
  clearAuth: () => void
}

export const useAuthStore = create<AuthState>((set) => ({
  token: null,
  user: null,
  isAuthenticated: false,
  setAuth: (token: string, user: User) => {
    setToken(token)
    set({ token, user, isAuthenticated: token.length > 0 })
  },
  clearAuth: () => {
    clearToken()
    set({ token: null, user: null, isAuthenticated: false })
  },
}))
