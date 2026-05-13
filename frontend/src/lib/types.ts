export type AssetType = "stock" | "crypto"
export type ResearchTracePhase = "planner" | "tool" | "synthesizer" | "fallback"
export type ResearchTraceStatus = "success" | "error" | "skipped"

export interface CurrentPriceResponse {
  ticker: string
  price: number
  currency: string
  cached: boolean
}

export interface OHLCVBar {
  time: string
  open: number
  high: number
  low: number
  close: number
  volume: number
}

export interface HistoricalResponse {
  ticker: string
  start: string
  end: string
  bars: OHLCVBar[]
  cached: boolean
}

export interface MACDPoint {
  macd: number | null
  signal: number | null
  histogram: number | null
}

export interface BollingerPoint {
  upper: number | null
  middle: number | null
  lower: number | null
}

export interface IndicatorsResponse {
  ticker: string
  start: string
  end: string
  cached: boolean
  bars: string[]
  rsi: (number | null)[]
  macd: MACDPoint[]
  bollinger: BollingerPoint[]
}

export interface NewsArticle {
  title: string
  url: string
  published_at: string | null
  source: string | null
}

export interface NewsResponse {
  ticker: string
  cached: boolean
  articles: NewsArticle[]
}

export interface FundamentalPeriod {
  period_date: string
  period_type: string
  [key: string]: number | string | null
}

export interface FundamentalsResponse {
  ticker: string
  cached: boolean
  periods: FundamentalPeriod[]
}

export interface WatchlistItem {
  id: number
  ticker: string
  asset_type: AssetType
  notes: string | null
  created_at: string
}

export interface ResearchSource {
  tool?: string
  endpoint?: string
  type?: string
  [key: string]: unknown
}

export interface ResearchTraceEvent {
  step: number
  phase: ResearchTracePhase
  action: string
  tool?: string | null
  endpoint?: string | null
  status: ResearchTraceStatus
  message: string
  metadata: Record<string, unknown>
}

export interface ResearchRequest {
  ticker: string
  asset_type: AssetType
  question: string
}

export interface ResearchResponse {
  ticker: string
  asset_type: AssetType
  question: string
  summary: string
  price_context: string
  news_summary: string
  technical_context: string
  fundamental_context: string
  risks: string[]
  sources: ResearchSource[]
  trace: ResearchTraceEvent[]
  generated_at: string
}

export interface AuthToken {
  access_token: string
  token_type: string
}

export interface User {
  id: number
  email: string
  is_active: boolean
  is_verified: boolean
}
