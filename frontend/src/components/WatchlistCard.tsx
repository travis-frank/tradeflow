import type { KeyboardEvent, MouseEvent, ReactElement } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { ExternalLink, Trash2 } from "lucide-react"

import { ApiError, cryptoApi, newsApi, pricesApi } from "@/lib/api"
import type { CurrentPriceResponse, NewsResponse, WatchlistItem } from "@/lib/types"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

interface WatchlistCardProps {
  item: WatchlistItem
  onRemove: (id: number) => Promise<void>
  isRemoving: boolean
}

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
})

const PRICE_STALE_TIME_MS = 2 * 60_000
const PRICE_GC_TIME_MS = 10 * 60_000
const NEWS_STALE_TIME_MS = 5 * 60_000
const NEWS_GC_TIME_MS = 30 * 60_000

const shouldRetryDashboardCardRequest = (failureCount: number, error: unknown): boolean => {
  if (error instanceof ApiError && error.status === 429) {
    return false
  }
  return failureCount < 1
}

const getPrice = async (item: WatchlistItem): Promise<CurrentPriceResponse> => {
  if (item.asset_type === "crypto") {
    return cryptoApi.getCurrent(item.ticker)
  }
  return pricesApi.getCurrent(item.ticker)
}

export function WatchlistCard({ item, onRemove, isRemoving }: WatchlistCardProps): ReactElement {
  const navigate = useNavigate()

  const {
    data: priceData,
    isPending: isPricePending,
    isError: isPriceError,
  } = useQuery({
    queryKey: ["dashboard", "price", item.asset_type, item.ticker],
    queryFn: () => getPrice(item),
    staleTime: PRICE_STALE_TIME_MS,
    gcTime: PRICE_GC_TIME_MS,
    refetchOnWindowFocus: false,
    retry: shouldRetryDashboardCardRequest,
  })

  const {
    data: newsData,
    isPending: isNewsPending,
    isError: isNewsError,
  } = useQuery<NewsResponse>({
    queryKey: ["dashboard", "news", item.ticker],
    queryFn: () => newsApi.getNews(item.ticker),
    staleTime: NEWS_STALE_TIME_MS,
    gcTime: NEWS_GC_TIME_MS,
    refetchOnWindowFocus: false,
    retry: shouldRetryDashboardCardRequest,
  })

  const headlines: string[] = (newsData?.articles ?? [])
    .slice(0, 2)
    .map((article) => article.title)
    .filter((title) => title.length > 0)

  const navigateToTicker = (): void => {
    navigate(`/ticker/${item.ticker}`)
  }

  const handleCardKeyDown = (event: KeyboardEvent<HTMLDivElement>): void => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault()
      navigateToTicker()
    }
  }

  const handleRemoveClick = async (event: MouseEvent<HTMLButtonElement>): Promise<void> => {
    event.stopPropagation()
    await onRemove(item.id)
  }

  return (
    <Card
      className="h-full cursor-pointer border-gray-800 bg-gray-900 text-gray-100 transition-colors hover:border-gray-700"
      onClick={navigateToTicker}
      onKeyDown={handleCardKeyDown}
      role="button"
      tabIndex={0}
    >
      <CardHeader className="space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <CardTitle className="text-2xl font-bold tracking-tight text-gray-100">
              {item.ticker}
            </CardTitle>
          </div>
          <Button
            aria-label={`Remove ${item.ticker} from watchlist`}
            disabled={isRemoving}
            onClick={(event) => {
              void handleRemoveClick(event)
            }}
            size="icon-sm"
            variant="ghost"
          >
            <Trash2 className="size-4" />
          </Button>
        </div>

        <Badge className="w-fit border-gray-700 bg-gray-800 text-gray-200" variant="outline">
          {item.asset_type}
        </Badge>
      </CardHeader>

      <CardContent className="space-y-4">
        <div>
          {isPricePending ? (
            <div className="h-8 w-32 animate-pulse rounded bg-gray-800" />
          ) : isPriceError ? (
            <p className="text-sm text-red-400">Price unavailable</p>
          ) : (
            <p className="text-2xl font-semibold text-gray-100">
              {currencyFormatter.format(priceData?.price ?? 0)}
            </p>
          )}
        </div>

        <div className="space-y-2">
          <p className="text-xs font-semibold tracking-wide text-gray-400 uppercase">Latest news</p>

          {isNewsPending ? (
            <div className="space-y-2">
              <div className="h-4 w-full animate-pulse rounded bg-gray-800" />
              <div className="h-4 w-11/12 animate-pulse rounded bg-gray-800" />
            </div>
          ) : isNewsError ? (
            <p className="text-sm text-red-400">News unavailable</p>
          ) : headlines.length === 0 ? (
            <p className="text-sm text-gray-500">No recent headlines</p>
          ) : (
            <ul className="space-y-1.5">
              {headlines.map((headline) => (
                <li key={headline} className="flex items-start gap-2 text-sm text-gray-300">
                  <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-gray-500" />
                  <span className="truncate" title={headline}>
                    {headline}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </CardContent>
    </Card>
  )
}
