import { useState, type FormEvent, type ReactElement } from "react"
import { Link } from "react-router-dom"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, ExternalLink, Plus, Search } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { ApiError, cryptoApi, newsApi, pricesApi, watchlistApi } from "@/lib/api"
import type { CurrentPriceResponse, NewsResponse } from "@/lib/types"

type AssetType = "stock" | "crypto"

interface SearchResult {
  ticker: string
  assetType: AssetType
  price: CurrentPriceResponse
  news: NewsResponse
}

const getErrorMessage = (error: unknown): string => {
  if (error instanceof ApiError) {
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return "Request failed. Please try again."
}

const validateTicker = (ticker: string, assetType: AssetType): string | null => {
  if (!ticker) {
    return "Ticker is required"
  }
  if (assetType === "crypto" && !ticker.includes("-")) {
    return "Use format BTC-USD"
  }
  if (assetType === "stock" && ticker.includes("-")) {
    return "Stock tickers should not contain a dash"
  }
  return null
}

const formatPrice = (price: number, currency: string): string => {
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(price)
  } catch {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency: "USD",
      maximumFractionDigits: 2,
    }).format(price)
  }
}

const fetchPreview = async (ticker: string, assetType: AssetType): Promise<SearchResult> => {
  const pricePromise =
    assetType === "crypto" ? cryptoApi.getCurrent(ticker) : pricesApi.getCurrent(ticker)

  const [price, news] = await Promise.all([pricePromise, newsApi.getNews(ticker)])

  return { ticker, assetType, price, news }
}

export function SearchPage(): ReactElement {
  const queryClient = useQueryClient()
  const [tickerInput, setTickerInput] = useState("")
  const [assetType, setAssetType] = useState<AssetType>("stock")
  const [formMessage, setFormMessage] = useState<string | null>(null)
  const [lookupError, setLookupError] = useState<string | null>(null)
  const [lookupResult, setLookupResult] = useState<SearchResult | null>(null)
  const [watchlistMessage, setWatchlistMessage] = useState<string | null>(null)
  const [addedToWatchlist, setAddedToWatchlist] = useState(false)

  const lookupMutation = useMutation({
    mutationFn: ({ ticker, type }: { ticker: string; type: AssetType }) => fetchPreview(ticker, type),
  })

  const addMutation = useMutation({
    mutationFn: ({ ticker, type }: { ticker: string; type: AssetType }) => watchlistApi.add(ticker, type),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["watchlist"] })
    },
  })

  const handleSearch = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setFormMessage(null)
    setLookupError(null)
    setWatchlistMessage(null)
    setAddedToWatchlist(false)

    const ticker = tickerInput.trim().toUpperCase()
    setTickerInput(ticker)

    const validationError = validateTicker(ticker, assetType)
    if (validationError) {
      setFormMessage(validationError)
      setLookupResult(null)
      return
    }

    try {
      const result = await lookupMutation.mutateAsync({ ticker, type: assetType })
      setLookupResult(result)
    } catch (error: unknown) {
      setLookupResult(null)
      setLookupError(getErrorMessage(error))
    }
  }

  const handleAddToWatchlist = async (): Promise<void> => {
    if (!lookupResult) {
      return
    }

    setWatchlistMessage(null)

    try {
      await addMutation.mutateAsync({ ticker: lookupResult.ticker, type: lookupResult.assetType })
      setAddedToWatchlist(true)
      setWatchlistMessage("Added to watchlist")
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 409) {
        setWatchlistMessage("Already in watchlist")
        return
      }
      setWatchlistMessage(getErrorMessage(error))
    }
  }

  const headlines = (lookupResult?.news.articles ?? [])
    .slice(0, 3)
    .filter((article) => article.title.length > 0)

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <main className="mx-auto w-full max-w-5xl space-y-6 px-6 py-8">
        <div className="flex items-center justify-between">
          <Button asChild size="sm" variant="ghost">
            <Link to="/dashboard">
              <ArrowLeft className="mr-2 size-4" />
              Back to dashboard
            </Link>
          </Button>
        </div>

        <div className="space-y-1">
          <h1 className="text-3xl font-semibold tracking-tight text-gray-100">Search markets</h1>
          <p className="text-sm text-gray-400">
            Look up a ticker, preview market data, then add it to your watchlist.
          </p>
        </div>

        <Card className="border-gray-800 bg-gray-900 text-gray-100">
          <CardContent>
            <form className="space-y-3" onSubmit={handleSearch}>
              <div className="flex flex-col gap-3 md:flex-row">
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-gray-500" />
                  <Input
                    className="border-gray-700 bg-gray-950 pl-9"
                    placeholder="Ticker symbol (AAPL or BTC-USD)"
                    value={tickerInput}
                    onChange={(event) => setTickerInput(event.target.value)}
                  />
                </div>

                <select
                  className="h-9 rounded-md border border-gray-700 bg-gray-950 px-3 text-sm text-gray-100"
                  value={assetType}
                  onChange={(event) => setAssetType(event.target.value as AssetType)}
                >
                  <option value="stock">stock</option>
                  <option value="crypto">crypto</option>
                </select>

                <Button disabled={lookupMutation.isPending} type="submit">
                  {lookupMutation.isPending ? "Searching..." : "Search"}
                </Button>
              </div>

              {formMessage ? <p className="text-sm text-amber-300">{formMessage}</p> : null}
              {lookupError ? <p className="text-sm text-red-400">{lookupError}</p> : null}
            </form>
          </CardContent>
        </Card>

        <Separator className="bg-gray-800" />

        {lookupMutation.isPending ? (
          <Card className="border-gray-800 bg-gray-900">
            <CardContent className="space-y-3">
              <div className="h-6 w-32 animate-pulse rounded bg-gray-800" />
              <div className="h-8 w-40 animate-pulse rounded bg-gray-800" />
              <div className="h-4 w-full animate-pulse rounded bg-gray-800" />
              <div className="h-4 w-11/12 animate-pulse rounded bg-gray-800" />
            </CardContent>
          </Card>
        ) : null}

        {lookupResult ? (
          <Card className="border-gray-800 bg-gray-900 text-gray-100">
            <CardHeader className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <CardTitle className="text-2xl font-bold tracking-tight text-gray-100">
                  {lookupResult.ticker}
                </CardTitle>
                <div className="flex items-center gap-2">
                  <Badge className="border-gray-700 bg-gray-800 text-gray-200" variant="outline">
                    {lookupResult.assetType}
                  </Badge>
                  <Badge
                    className={
                      lookupResult.price.cached
                        ? "border-sky-800 bg-sky-950 text-sky-300"
                        : "border-emerald-800 bg-emerald-950 text-emerald-300"
                    }
                    variant="outline"
                  >
                    {lookupResult.price.cached ? "cached" : "fresh"}
                  </Badge>
                </div>
              </div>
            </CardHeader>

            <CardContent className="space-y-5">
              <p className="text-3xl font-semibold text-gray-100">
                {formatPrice(lookupResult.price.price, lookupResult.price.currency)}
              </p>

              <div className="space-y-2">
                <p className="text-xs font-semibold tracking-wide text-gray-400 uppercase">
                  Latest news
                </p>

                {headlines.length === 0 ? (
                  <p className="text-sm text-gray-500">No recent headlines</p>
                ) : (
                  <ul className="space-y-2">
                    {headlines.map((article) => (
                      <li key={`${article.title}-${article.url}`} className="text-sm text-gray-300">
                        {article.url ? (
                          <a
                            className="inline-flex items-start gap-2 hover:text-gray-200"
                            href={article.url}
                            rel="noreferrer"
                            target="_blank"
                          >
                            <ExternalLink className="mt-0.5 size-3.5 shrink-0 text-gray-500" />
                            <span>{article.title}</span>
                          </a>
                        ) : (
                          <span>{article.title}</span>
                        )}
                      </li>
                    ))}
                  </ul>
                )}
              </div>

              <div className="flex flex-wrap gap-2">
                <Button asChild variant="outline">
                  <Link to={`/ticker/${lookupResult.ticker}`}>View details</Link>
                </Button>
                <Button disabled={addMutation.isPending || addedToWatchlist} onClick={handleAddToWatchlist}>
                  <Plus className="mr-2 size-4" />
                  {addMutation.isPending ? "Adding..." : addedToWatchlist ? "Added" : "Add to watchlist"}
                </Button>
              </div>

              {watchlistMessage ? (
                <p className={addedToWatchlist ? "text-sm text-emerald-300" : "text-sm text-amber-300"}>
                  {watchlistMessage}
                </p>
              ) : null}
            </CardContent>
          </Card>
        ) : null}
      </main>
    </div>
  )
}
