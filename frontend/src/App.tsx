import type { ReactElement } from "react"
import { Navigate, Route, Routes } from "react-router-dom"

import { ProtectedRoute } from "@/components/ProtectedRoute"
import { DashboardPage } from "@/pages/DashboardPage"
import { LoginPage } from "@/pages/LoginPage"
import { RegisterPage } from "@/pages/RegisterPage"
import { ResearchPage } from "@/pages/ResearchPage"
import { TickerDetailPage } from "@/pages/TickerDetailPage"

function App(): ReactElement {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/register" element={<RegisterPage />} />
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute>
            <DashboardPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/ticker/:ticker"
        element={
          <ProtectedRoute>
            <TickerDetailPage />
          </ProtectedRoute>
        }
      />
      <Route
        path="/research"
        element={
          <ProtectedRoute>
            <ResearchPage />
          </ProtectedRoute>
        }
      />
    </Routes>
  )
}

export default App
