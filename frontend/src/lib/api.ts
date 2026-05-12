import type {
  AuthToken,
  CurrentPriceResponse,
  FundamentalsResponse,
  HistoricalResponse,
  IndicatorsResponse,
  NewsResponse,
  ResearchRequest,
  ResearchResponse,
  User,
  WatchlistItem,
} from "@/lib/types"

const API_BASE = import.meta.env.VITE_API_URL ?? "http://localhost:8000"

let authToken: string | null = null

type QueryParam = string | number | boolean

interface RequestOptions extends Omit<RequestInit, "headers"> {
  headers?: HeadersInit
  query?: Record<string, QueryParam | undefined>
}

export class ApiError extends Error {
  readonly status: number

  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export const setToken = (token: string): void => {
  authToken = token
}

export const clearToken = (): void => {
  authToken = null
}

const buildUrl = (path: string, query?: Record<string, QueryParam | undefined>): string => {
  const url = new URL(`${API_BASE}${path}`)

  if (query) {
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) {
        url.searchParams.set(key, String(value))
      }
    }
  }

  return url.toString()
}

const parseErrorMessage = async (response: Response): Promise<string> => {
  try {
    const data: unknown = await response.json()
    if (typeof data === "object" && data !== null && "detail" in data) {
      const detail = (data as { detail: unknown }).detail
      if (typeof detail === "string") {
        return detail
      }
      if (Array.isArray(detail)) {
        const messages = detail
          .map((item) => {
            if (typeof item === "object" && item !== null && "msg" in item) {
              const message = (item as { msg: unknown }).msg
              return typeof message === "string" ? message : null
            }
            return null
          })
          .filter((message): message is string => Boolean(message))

        if (messages.length > 0) {
          return messages.join("; ")
        }
      }
    }
  } catch {
    return `Request failed with status ${response.status}`
  }

  return `Request failed with status ${response.status}`
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { query, headers, body, ...rest } = options

  const requestHeaders = new Headers(headers)
  requestHeaders.set("Accept", "application/json")

  if (body !== undefined && !requestHeaders.has("Content-Type")) {
    requestHeaders.set("Content-Type", "application/json")
  }

  if (authToken) {
    requestHeaders.set("Authorization", `Bearer ${authToken}`)
  }

  const response = await fetch(buildUrl(path, query), {
    ...rest,
    body,
    headers: requestHeaders,
  })

  if (!response.ok) {
    throw new ApiError(response.status, await parseErrorMessage(response))
  }

  if (response.status === 204) {
    return undefined as T
  }

  return (await response.json()) as T
}

export const authApi = {
  async login(email: string, password: string): Promise<AuthToken> {
    const token = await request<AuthToken>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    })
    setToken(token.access_token)
    return token
  },

  register(email: string, password: string): Promise<User> {
    return request<User>("/api/auth/register", {
      method: "POST",
      body: JSON.stringify({ email, password }),
    })
  },

  me(): Promise<User> {
    return request<User>("/api/auth/me")
  },
}

export const pricesApi = {
  getCurrent(ticker: string): Promise<CurrentPriceResponse> {
    return request<CurrentPriceResponse>(`/api/prices/current/${ticker}`)
  },

  getHistorical(
    ticker: string,
    start: string,
    end: string,
    interval: string = "1d"
  ): Promise<HistoricalResponse> {
    return request<HistoricalResponse>(`/api/prices/historical/${ticker}`, {
      query: { start, end, interval },
    })
  },

  getIndicators(ticker: string, start: string, end: string): Promise<IndicatorsResponse> {
    return request<IndicatorsResponse>(`/api/prices/${ticker}/indicators`, {
      query: { start, end },
    })
  },
}

export const cryptoApi = {
  getCurrent(ticker: string): Promise<CurrentPriceResponse> {
    return request<CurrentPriceResponse>(`/api/crypto/current/${ticker}`)
  },

  getHistorical(
    ticker: string,
    start: string,
    end: string,
    interval: string = "1d"
  ): Promise<HistoricalResponse> {
    return request<HistoricalResponse>(`/api/crypto/historical/${ticker}`, {
      query: { start, end, interval },
    })
  },
}

export const fundamentalsApi = {
  getIncomeStatement(ticker: string): Promise<FundamentalsResponse> {
    return request<FundamentalsResponse>(`/api/fundamentals/${ticker}/income-statement`)
  },

  getBalanceSheet(ticker: string): Promise<FundamentalsResponse> {
    return request<FundamentalsResponse>(`/api/fundamentals/${ticker}/balance-sheet`)
  },

  getCashFlow(ticker: string): Promise<FundamentalsResponse> {
    return request<FundamentalsResponse>(`/api/fundamentals/${ticker}/cash-flow`)
  },
}

export const newsApi = {
  getNews(ticker: string): Promise<NewsResponse> {
    return request<NewsResponse>(`/api/news/${ticker}`)
  },
}

export const researchAgent = (payload: ResearchRequest): Promise<ResearchResponse> => {
  return request<ResearchResponse>("/api/agent/research", {
    method: "POST",
    body: JSON.stringify(payload),
  })
}

export const watchlistApi = {
  getAll(): Promise<WatchlistItem[]> {
    return request<WatchlistItem[]>("/api/watchlist")
  },

  add(ticker: string, asset_type: "stock" | "crypto"): Promise<WatchlistItem> {
    return request<WatchlistItem>("/api/watchlist", {
      method: "POST",
      body: JSON.stringify({ ticker, asset_type }),
    })
  },

  async remove(id: number): Promise<void> {
    await request<void>(`/api/watchlist/${id}`, { method: "DELETE" })
  },
}
