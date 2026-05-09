import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react"
import { useNavigate, useParams } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import {
  ArrowLeft,
  Clock3,
  Newspaper,
  RefreshCw,
  TrendingUp,
} from "lucide-react"
import { format, formatDistanceToNow, subDays, subMonths, subYears } from "date-fns"
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts"

import {
  cryptoApi,
  fundamentalsApi,
  newsApi,
  pricesApi,
} from "@/lib/api"
import type {
  CurrentPriceResponse,
  FundamentalPeriod,
  FundamentalsResponse,
  HistoricalResponse,
  IndicatorsResponse,
  NewsResponse,
} from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"
import {
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
} from "@/components/ui/tabs"

interface MetricCardProps {
  label: string
  value: string
  valueClassName?: string
}

function MetricCard({ label, value, valueClassName }: MetricCardProps): ReactElement {
  return (
    <div className="rounded-lg border border-gray-800 bg-gray-900 p-3">
      <p className="text-xs text-gray-400">{label}</p>
      <p className={`mt-1 text-lg font-semibold ${valueClassName ?? "text-gray-100"}`}>
        {value}
      </p>
    </div>
  )
}

function formatPrice(value: number): string {
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value)
}

function formatMetricValue(value: number | string | null | undefined): string {
  if (value === null || value === undefined) {
    return "—"
  }

  if (typeof value === "string") {
    return value
  }

  const abs = Math.abs(value)
  if (abs >= 1_000_000_000) {
    return `$${(value / 1_000_000_000).toFixed(2)}B`
  }
  if (abs >= 1_000_000) {
    return `$${(value / 1_000_000).toFixed(2)}M`
  }

  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 2,
  }).format(value)
}

function getRelativeTime(iso: string | null): string {
  if (!iso) {
    return "Unknown time"
  }

  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) {
    return "Unknown time"
  }

  return formatDistanceToNow(date, { addSuffix: true })
}

function normalizeChartDate(value: string): string {
  return value.split("T")[0]
}

function toChartTime(
  value: string | number,
  mode: "intraday" | "daily"
): { time: string | UTCTimestamp; sortKey: number; dedupeKey: string } | null {
  const toUnixSeconds = (raw: number): number | null => {
    if (!Number.isFinite(raw)) {
      return null
    }
    if (raw > 1_000_000_000_000) {
      return Math.floor(raw / 1000)
    }
    if (raw > 10_000_000_000) {
      return Math.floor(raw / 1000)
    }
    return Math.floor(raw)
  }

  if (typeof value === "number") {
    const seconds = toUnixSeconds(value)
    if (seconds === null) {
      return null
    }
    if (mode === "daily") {
      const day = format(new Date(seconds * 1000), "yyyy-MM-dd")
      return {
        time: day,
        sortKey: Date.parse(`${day}T00:00:00Z`) / 1000,
        dedupeKey: `d:${day}`,
      }
    }
    return {
      time: seconds as UTCTimestamp,
      sortKey: seconds,
      dedupeKey: `ts:${seconds}`,
    }
  }

  const raw = value.trim()
  if (!raw) {
    return null
  }

  if (/^\d+(\.\d+)?$/.test(raw)) {
    const parsed = Number(raw)
    const seconds = toUnixSeconds(parsed)
    if (seconds === null) {
      return null
    }
    if (mode === "daily") {
      const day = format(new Date(seconds * 1000), "yyyy-MM-dd")
      return {
        time: day,
        sortKey: Date.parse(`${day}T00:00:00Z`) / 1000,
        dedupeKey: `d:${day}`,
      }
    }
    return {
      time: seconds as UTCTimestamp,
      sortKey: seconds,
      dedupeKey: `ts:${seconds}`,
    }
  }

  if (!raw.includes("T") && /^\d{4}-\d{2}-\d{2}$/.test(raw)) {
    const sortKey = Date.parse(`${raw}T00:00:00Z`) / 1000
    if (!Number.isFinite(sortKey)) {
      return null
    }
    if (mode === "intraday") {
      return {
        time: Math.floor(sortKey) as UTCTimestamp,
        sortKey,
        dedupeKey: `ts:${Math.floor(sortKey)}`,
      }
    }
    return {
      time: raw,
      sortKey,
      dedupeKey: `d:${raw}`,
    }
  }

  const parsedDate = new Date(raw)
  if (!Number.isNaN(parsedDate.getTime())) {
    const seconds = Math.floor(parsedDate.getTime() / 1000)
    if (mode === "daily") {
      const day = format(parsedDate, "yyyy-MM-dd")
      return {
        time: day,
        sortKey: Date.parse(`${day}T00:00:00Z`) / 1000,
        dedupeKey: `d:${day}`,
      }
    }
    return {
      time: seconds as UTCTimestamp,
      sortKey: seconds,
      dedupeKey: `ts:${seconds}`,
    }
  }

  const normalizedDate = normalizeChartDate(raw)
  if (/^\d{4}-\d{2}-\d{2}$/.test(normalizedDate)) {
    const sortKey = Date.parse(`${normalizedDate}T00:00:00Z`) / 1000
    if (!Number.isFinite(sortKey)) {
      return null
    }
    if (mode === "intraday") {
      return {
        time: Math.floor(sortKey) as UTCTimestamp,
        sortKey,
        dedupeKey: `ts:${Math.floor(sortKey)}`,
      }
    }
    return {
      time: normalizedDate,
      sortKey,
      dedupeKey: `d:${normalizedDate}`,
    }
  }

  return null
}

function getLatestNonNullNumber(values: Array<number | null>): number | null {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const value = values[index]
    if (value !== null) {
      return value
    }
  }
  return null
}

function getLatestNonNullMacd(values: IndicatorsResponse["macd"]): number | null {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const value = values[index]?.macd
    if (value !== null && value !== undefined) {
      return value
    }
  }
  return null
}

function getLatestBollinger(values: IndicatorsResponse["bollinger"]): {
  upper: number | null
  middle: number | null
  lower: number | null
} {
  for (let index = values.length - 1; index >= 0; index -= 1) {
    const point = values[index]
    if (point && point.upper !== null && point.middle !== null && point.lower !== null) {
      return point
    }
  }

  return { upper: null, middle: null, lower: null }
}

function getFundamentalColumns(periods: FundamentalPeriod[]): string[] {
  const columns: string[] = []
  const seen = new Set<string>()

  for (const period of periods) {
    for (const key of Object.keys(period)) {
      if (key === "period_date" || key === "period_type") {
        continue
      }
      if (!seen.has(key)) {
        seen.add(key)
        columns.push(key)
      }
    }
  }

  return columns
}

function FundamentalsTable({
  data,
  isLoading,
  error,
}: {
  data: FundamentalsResponse | undefined
  isLoading: boolean
  error: unknown
}): ReactElement {
  if (isLoading) {
    return (
      <div className="space-y-2">
        <div className="h-8 w-full animate-pulse rounded bg-gray-800" />
        <div className="h-8 w-full animate-pulse rounded bg-gray-800" />
        <div className="h-8 w-full animate-pulse rounded bg-gray-800" />
      </div>
    )
  }

  if (error) {
    return <p className="text-sm text-red-400">Failed to load fundamentals.</p>
  }

  const periods = data?.periods ?? []
  if (periods.length === 0) {
    return <p className="text-sm text-gray-400">No fundamentals available.</p>
  }

  const columns = getFundamentalColumns(periods)

  return (
    <div className="overflow-x-auto">
      <table className="min-w-full text-left text-sm">
        <thead>
          <tr className="border-b border-gray-800 text-gray-400">
            <th className="px-3 py-2 font-medium">Period</th>
            {columns.map((column) => (
              <th className="px-3 py-2 font-medium" key={column}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {periods.map((period) => (
            <tr className="border-b border-gray-900" key={`${period.period_date}-${period.period_type}`}>
              <td className="px-3 py-2 text-gray-200">{period.period_date}</td>
              {columns.map((column) => (
                <td className="px-3 py-2 text-gray-300" key={`${period.period_date}-${column}`}>
                  {formatMetricValue(period[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export function TickerDetailPage(): ReactElement {
  const navigate = useNavigate()
  const params = useParams<{ ticker: string }>()

  const ticker = (params.ticker ?? "").toUpperCase()
  const isCrypto = ticker.includes("-")
  const [selectedRange, setSelectedRange] = useState<"1D" | "5D" | "1M" | "6M" | "1Y">("1Y")

  const rangeConfig = useMemo(() => {
    const now = new Date()
    const endDate = format(now, "yyyy-MM-dd")
    return {
      "1D": {
        startDate: format(subDays(now, 1), "yyyy-MM-dd"),
        endDate,
        interval: "5m",
        mode: "intraday" as const,
      },
      "5D": {
        startDate: format(subDays(now, 5), "yyyy-MM-dd"),
        endDate,
        interval: "30m",
        mode: "intraday" as const,
      },
      "1M": {
        startDate: format(subMonths(now, 1), "yyyy-MM-dd"),
        endDate,
        interval: "1d",
        mode: "daily" as const,
      },
      "6M": {
        startDate: format(subMonths(now, 6), "yyyy-MM-dd"),
        endDate,
        interval: "1d",
        mode: "daily" as const,
      },
      "1Y": {
        startDate: format(subYears(now, 1), "yyyy-MM-dd"),
        endDate,
        interval: "1d",
        mode: "daily" as const,
      },
    }
  }, [])

  const activeRange = rangeConfig[selectedRange]
  const indicatorsRange = rangeConfig["1Y"]

  const chartContainerRef = useRef<HTMLDivElement | null>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null)
  const resizeObserverRef = useRef<ResizeObserver | null>(null)

  const currentPriceQuery = useQuery<CurrentPriceResponse>({
    queryKey: ["current-price", ticker],
    queryFn: () => (isCrypto ? cryptoApi.getCurrent(ticker) : pricesApi.getCurrent(ticker)),
    staleTime: 25_000,
    enabled: ticker.length > 0,
  })

  const historicalQuery = useQuery<HistoricalResponse>({
    queryKey: [
      "historical",
      ticker,
      selectedRange,
      activeRange.interval,
      activeRange.startDate,
      activeRange.endDate,
    ],
    queryFn: () =>
      isCrypto
        ? cryptoApi.getHistorical(
            ticker,
            activeRange.startDate,
            activeRange.endDate,
            activeRange.interval
          )
        : pricesApi.getHistorical(
            ticker,
            activeRange.startDate,
            activeRange.endDate,
            activeRange.interval
          ),
    staleTime: 300_000,
    enabled: ticker.length > 0,
  })

  const indicatorsQuery = useQuery<IndicatorsResponse>({
    queryKey: [
      "indicators",
      ticker,
      indicatorsRange.startDate,
      indicatorsRange.endDate,
    ],
    queryFn: () =>
      pricesApi.getIndicators(
        ticker,
        indicatorsRange.startDate,
        indicatorsRange.endDate
      ),
    staleTime: 300_000,
    enabled: ticker.length > 0,
  })

  const newsQuery = useQuery<NewsResponse>({
    queryKey: ["news", ticker],
    queryFn: () => newsApi.getNews(ticker),
    staleTime: 25_000,
    enabled: ticker.length > 0,
  })

  const incomeQuery = useQuery<FundamentalsResponse>({
    queryKey: ["fundamentals-income", ticker],
    queryFn: () => fundamentalsApi.getIncomeStatement(ticker),
    staleTime: 300_000,
    enabled: ticker.length > 0 && !isCrypto,
  })

  const balanceQuery = useQuery<FundamentalsResponse>({
    queryKey: ["fundamentals-balance", ticker],
    queryFn: () => fundamentalsApi.getBalanceSheet(ticker),
    staleTime: 300_000,
    enabled: ticker.length > 0 && !isCrypto,
  })

  const cashFlowQuery = useQuery<FundamentalsResponse>({
    queryKey: ["fundamentals-cashflow", ticker],
    queryFn: () => fundamentalsApi.getCashFlow(ticker),
    staleTime: 300_000,
    enabled: ticker.length > 0 && !isCrypto,
  })

  const chartData = useMemo(() => {
    const bars = historicalQuery.data?.bars ?? []
    const byTime = new Map<
      string,
      {
        time: string | UTCTimestamp
        sortKey: number
        open: number
        high: number
        low: number
        close: number
      }
    >()

    for (const bar of bars) {
      const open = Number(bar.open)
      const high = Number(bar.high)
      const low = Number(bar.low)
      const close = Number(bar.close)

      if (
        !Number.isFinite(open) ||
        !Number.isFinite(high) ||
        !Number.isFinite(low) ||
        !Number.isFinite(close)
      ) {
        continue
      }

      const chartTime = toChartTime(bar.time, activeRange.mode)
      if (!chartTime) {
        continue
      }

      byTime.set(chartTime.dedupeKey, {
        time: chartTime.time,
        sortKey: chartTime.sortKey,
        open,
        high,
        low,
        close,
      })
    }

    return Array.from(byTime.values())
      .sort((a, b) => a.sortKey - b.sortKey)
      .map(({ time, open, high, low, close }) => ({ time, open, high, low, close }))
  }, [activeRange.mode, historicalQuery.data?.bars])

  useEffect(() => {
    if (!chartContainerRef.current || chartRef.current) {
      return
    }

    const container = chartContainerRef.current

    const chart = createChart(container, {
      layout: {
        background: { type: ColorType.Solid, color: "#0a0a0a" },
        textColor: "#d1d5db",
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { visible: false },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#374151" },
        horzLine: { color: "#374151" },
      },
      rightPriceScale: {
        borderVisible: false,
      },
      timeScale: {
        borderVisible: false,
      },
      width: container.clientWidth,
      height: 384,
    })

    const series = chart.addSeries(CandlestickSeries, {
      upColor: "#22c55e",
      borderUpColor: "#22c55e",
      wickUpColor: "#22c55e",
      downColor: "#ef4444",
      borderDownColor: "#ef4444",
      wickDownColor: "#ef4444",
    })

    chartRef.current = chart
    seriesRef.current = series

    const resizeObserver = new ResizeObserver((entries) => {
      const entry = entries[0]
      if (!entry || !chartRef.current) {
        return
      }
      chartRef.current.applyOptions({ width: entry.contentRect.width })
    })

    resizeObserver.observe(container)
    resizeObserverRef.current = resizeObserver

    return () => {
      resizeObserverRef.current?.disconnect()
      resizeObserverRef.current = null
      chartRef.current?.remove()
      chartRef.current = null
      seriesRef.current = null
    }
  }, [])

  useEffect(() => {
    if (!seriesRef.current) {
      return
    }

    seriesRef.current.setData(chartData)
    chartRef.current?.timeScale().fitContent()
  }, [chartData])

  const latestRsi = getLatestNonNullNumber(indicatorsQuery.data?.rsi ?? [])
  const latestMacd = getLatestNonNullMacd(indicatorsQuery.data?.macd ?? [])
  const latestBollinger = getLatestBollinger(indicatorsQuery.data?.bollinger ?? [])

  const rsiColorClass =
    latestRsi === null
      ? "text-gray-300"
      : latestRsi > 70
        ? "text-green-400"
        : latestRsi < 30
          ? "text-red-400"
          : "text-gray-300"

  return (
    <div className="min-h-screen bg-gray-950 px-4 py-6 text-gray-100 md:px-6">
      <div className="mx-auto w-full max-w-7xl space-y-6">
        <Button onClick={() => navigate("/dashboard")} variant="ghost">
          <ArrowLeft className="mr-2 size-4" />
          Back to dashboard
        </Button>

        <Card className="border-gray-800 bg-gray-900">
          <CardContent className="flex flex-col gap-4 pt-4 md:flex-row md:items-center md:justify-between">
            <div className="space-y-1">
              <h1 className="text-3xl font-bold tracking-tight">{ticker || "Unknown ticker"}</h1>
              <div className="flex items-center gap-2">
                <Badge variant="outline">{isCrypto ? "crypto" : "stock"}</Badge>
                <Badge variant={currentPriceQuery.data?.cached ? "secondary" : "default"}>
                  {currentPriceQuery.data?.cached ? "cached" : "fresh"}
                </Badge>
              </div>
            </div>
            <div className="text-right">
              {currentPriceQuery.isPending ? (
                <div className="h-9 w-32 animate-pulse rounded bg-gray-800" />
              ) : currentPriceQuery.isError ? (
                <p className="text-sm text-red-400">Failed to load current price</p>
              ) : (
                <p className="text-3xl font-semibold">
                  {formatPrice(currentPriceQuery.data?.price ?? 0)}
                </p>
              )}
            </div>
          </CardContent>
        </Card>

        <div className="grid grid-cols-1 gap-6 xl:grid-cols-3">
          <div className="space-y-6 xl:col-span-2">
            <Card className="border-gray-800 bg-gray-900">
              <CardHeader>
                <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
                  <CardTitle>Candlestick chart</CardTitle>
                  <div className="flex items-center gap-1 rounded-lg border border-gray-800 bg-gray-950 p-1">
                    {(["1D", "5D", "1M", "6M", "1Y"] as const).map((range) => (
                      <Button
                        key={range}
                        onClick={() => setSelectedRange(range)}
                        size="xs"
                        variant={selectedRange === range ? "default" : "ghost"}
                      >
                        {range}
                      </Button>
                    ))}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                {historicalQuery.isError ? (
                  <p className="text-sm text-red-400">Failed to load historical price data.</p>
                ) : (
                  <div className="h-96 w-full" ref={chartContainerRef} />
                )}
              </CardContent>
            </Card>

            <Card className="border-gray-800 bg-gray-900">
              <CardHeader>
                <CardTitle>Indicators</CardTitle>
              </CardHeader>
              <CardContent>
                {indicatorsQuery.isPending ? (
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                    {Array.from({ length: 4 }).map((_, index) => (
                      <div key={index} className="h-20 animate-pulse rounded bg-gray-800" />
                    ))}
                  </div>
                ) : indicatorsQuery.isError ? (
                  <p className="text-sm text-red-400">Failed to load indicators.</p>
                ) : (
                  <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">
                    <MetricCard
                      label="RSI"
                      value={latestRsi === null ? "—" : latestRsi.toFixed(2)}
                      valueClassName={rsiColorClass}
                    />
                    <MetricCard
                      label="MACD"
                      value={latestMacd === null ? "—" : latestMacd.toFixed(4)}
                    />
                    <MetricCard
                      label="Bollinger Upper"
                      value={
                        latestBollinger.upper === null
                          ? "—"
                          : formatPrice(latestBollinger.upper)
                      }
                    />
                    <MetricCard
                      label="Bollinger Middle / Lower"
                      value={
                        latestBollinger.middle === null || latestBollinger.lower === null
                          ? "—"
                          : `${formatPrice(latestBollinger.middle)} / ${formatPrice(
                              latestBollinger.lower
                            )}`
                      }
                    />
                  </div>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="space-y-6">
            <Card className="border-gray-800 bg-gray-900">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Newspaper className="size-4" />
                  Latest news
                </CardTitle>
              </CardHeader>
              <CardContent>
                {newsQuery.isPending ? (
                  <div className="space-y-3">
                    {Array.from({ length: 3 }).map((_, index) => (
                      <div className="space-y-2" key={index}>
                        <div className="h-4 w-full animate-pulse rounded bg-gray-800" />
                        <div className="h-3 w-2/3 animate-pulse rounded bg-gray-800" />
                      </div>
                    ))}
                  </div>
                ) : newsQuery.isError ? (
                  <p className="text-sm text-red-400">Failed to load news.</p>
                ) : (
                  <div className="space-y-4">
                    {(newsQuery.data?.articles ?? []).slice(0, 10).map((article) => (
                      <article className="space-y-1" key={`${article.url}-${article.title}`}>
                        <a
                          className="line-clamp-2 text-sm font-medium text-gray-100 hover:underline"
                          href={article.url}
                          rel="noreferrer"
                          target="_blank"
                        >
                          {article.title}
                        </a>
                        <div className="flex items-center gap-2 text-xs text-gray-400">
                          <span>{article.source ?? "Unknown source"}</span>
                          <Separator className="h-3 bg-gray-700" orientation="vertical" />
                          <span className="inline-flex items-center gap-1">
                            <Clock3 className="size-3" />
                            {getRelativeTime(article.published_at)}
                          </span>
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card className="border-gray-800 bg-gray-900">
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <TrendingUp className="size-4" />
                  Fundamentals
                </CardTitle>
              </CardHeader>
              <CardContent>
                {isCrypto ? (
                  <p className="text-sm text-gray-400">
                    Fundamentals not available for crypto assets.
                  </p>
                ) : (
                  <Tabs defaultValue="income">
                    <TabsList className="w-full justify-start" variant="line">
                      <TabsTrigger value="income">Income Statement</TabsTrigger>
                      <TabsTrigger value="balance">Balance Sheet</TabsTrigger>
                      <TabsTrigger value="cash">Cash Flow</TabsTrigger>
                    </TabsList>

                    <TabsContent value="income">
                      <FundamentalsTable
                        data={incomeQuery.data}
                        error={incomeQuery.error}
                        isLoading={incomeQuery.isPending}
                      />
                    </TabsContent>
                    <TabsContent value="balance">
                      <FundamentalsTable
                        data={balanceQuery.data}
                        error={balanceQuery.error}
                        isLoading={balanceQuery.isPending}
                      />
                    </TabsContent>
                    <TabsContent value="cash">
                      <FundamentalsTable
                        data={cashFlowQuery.data}
                        error={cashFlowQuery.error}
                        isLoading={cashFlowQuery.isPending}
                      />
                    </TabsContent>
                  </Tabs>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      </div>

      {(historicalQuery.isFetching || indicatorsQuery.isFetching || newsQuery.isFetching) && (
        <div className="fixed right-4 bottom-4 inline-flex items-center gap-2 rounded-md border border-gray-800 bg-gray-900 px-3 py-2 text-xs text-gray-400">
          <RefreshCw className="size-3.5 animate-spin" />
          Refreshing data
        </div>
      )}
    </div>
  )
}
