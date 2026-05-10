import { create } from "zustand"
import { createJSONStorage, persist } from "zustand/middleware"

import { clearToken, setToken } from "@/lib/api"
import type { User } from "@/lib/types"

const AUTH_STORAGE_KEY = "tradeflow-auth"

interface AuthState {
  token: string | null
  user: User | null
  isAuthenticated: boolean
  hasHydrated: boolean
  setAuth: (token: string, user: User) => void
  clearAuth: () => void
  setHasHydrated: (hasHydrated: boolean) => void
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      isAuthenticated: false,
      hasHydrated: false,
      setAuth: (token: string, user: User) => {
        setToken(token)
        set({ token, user, isAuthenticated: token.length > 0 })
      },
      clearAuth: () => {
        clearToken()
        set({ token: null, user: null, isAuthenticated: false })
        window.localStorage.removeItem(AUTH_STORAGE_KEY)
      },
      setHasHydrated: (hasHydrated: boolean) => {
        set({ hasHydrated })
      },
    }),
    {
      name: AUTH_STORAGE_KEY,
      storage: createJSONStorage(() => window.localStorage),
      partialize: (state) => ({
        token: state.token,
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
      onRehydrateStorage: () => (state) => {
        if (state?.token) {
          setToken(state.token)
        } else {
          clearToken()
        }
        state?.setHasHydrated(true)
      },
    }
  )
)
