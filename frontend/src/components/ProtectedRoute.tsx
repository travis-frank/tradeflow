import type { ReactNode } from "react"
import { Navigate } from "react-router-dom"

import { useAuthStore } from "@/store/authStore"

interface ProtectedRouteProps {
  children: ReactNode
}

export function ProtectedRoute({ children }: ProtectedRouteProps): ReactNode {
  const hasHydrated = useAuthStore((state) => state.hasHydrated)
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated)

  if (!hasHydrated) {
    return <div className="min-h-screen bg-gray-950" />
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />
  }

  return children
}
