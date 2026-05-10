import { useMemo, useState, type FormEvent, type ReactElement } from "react"
import { Link, useNavigate } from "react-router-dom"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Brain, LogOut, Plus, Search } from "lucide-react"

import { ApiError, watchlistApi } from "@/lib/api"
import type { WatchlistItem } from "@/lib/types"
import { WatchlistCard } from "@/components/WatchlistCard"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { useAuthStore } from "@/store/authStore"

type AssetType = "stock" | "crypto"

const getErrorMessage = (error: unknown): string => {
  if (error instanceof ApiError) {
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return "Request failed. Please try again."
}

const shouldRetryDashboardRequest = (failureCount: number, error: unknown): boolean => {
  if (error instanceof ApiError && error.status === 429) {
    return false
  }
  return failureCount < 1
}

export function DashboardPage(): ReactElement {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const clearAuth = useAuthStore((state) => state.clearAuth)
  const user = useAuthStore((state) => state.user)

  const [tickerInput, setTickerInput] = useState("")
  const [assetType, setAssetType] = useState<AssetType>("stock")
  const [formError, setFormError] = useState<string | null>(null)
  const [removeError, setRemoveError] = useState<string | null>(null)
  const [removingId, setRemovingId] = useState<number | null>(null)

  const {
    data: watchlist,
    isPending: isWatchlistPending,
    isError: isWatchlistError,
    error: watchlistError,
  } = useQuery<WatchlistItem[]>({
    queryKey: ["watchlist"],
    queryFn: () => watchlistApi.getAll(),
    staleTime: 60_000,
    gcTime: 10 * 60_000,
    refetchOnWindowFocus: false,
    retry: shouldRetryDashboardRequest,
  })

  const addMutation = useMutation({
    mutationFn: ({ ticker, type }: { ticker: string; type: AssetType }) =>
      watchlistApi.add(ticker, type),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["watchlist"] })
    },
  })

  const removeMutation = useMutation({
    mutationFn: (id: number) => watchlistApi.remove(id),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["watchlist"] })
    },
  })

  const handleSignOut = (): void => {
    clearAuth()
    navigate("/login", { replace: true })
  }

  const handleAddTicker = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setFormError(null)

    const ticker = tickerInput.trim().toUpperCase()
    if (!ticker) {
      setFormError("Ticker is required")
      return
    }

    try {
      await addMutation.mutateAsync({ ticker, type: assetType })
      setTickerInput("")
    } catch (error: unknown) {
      if (error instanceof ApiError && error.status === 409) {
        setFormError("Ticker already in watchlist")
        return
      }
      setFormError(getErrorMessage(error))
    }
  }

  const handleRemoveTicker = async (id: number): Promise<void> => {
    setRemoveError(null)
    setRemovingId(id)

    try {
      await removeMutation.mutateAsync(id)
    } catch (error: unknown) {
      setRemoveError(getErrorMessage(error))
    } finally {
      setRemovingId(null)
    }
  }

  const watchlistItems = watchlist ?? []

  const welcomeEmail = useMemo(() => user?.email ?? "Signed in user", [user?.email])

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <header className="sticky top-0 z-10 border-b border-gray-800 bg-gray-950/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-7xl items-center justify-between px-6 py-4">
          <div className="text-xl font-semibold tracking-tight">tradeflow.ai</div>
          <div className="flex items-center gap-4">
            <Button asChild size="sm" variant="ghost">
              <Link to="/search">
                <Search className="mr-2 size-4" />
                Search markets
              </Link>
            </Button>
            <Button asChild size="sm" variant="ghost">
              <Link to="/research">
                <Brain className="mr-2 size-4" />
                Research
              </Link>
            </Button>
            <span className="text-sm text-gray-300">{welcomeEmail}</span>
            <Button onClick={handleSignOut} size="sm" variant="outline">
              <LogOut className="mr-2 size-4" />
              Sign out
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-7xl space-y-6 px-6 py-8">
        <Card className="border-gray-800 bg-gray-900 text-gray-100">
          <CardContent>
            <form className="space-y-3" onSubmit={handleAddTicker}>
              <div className="flex flex-col gap-3 md:flex-row">
                <div className="relative flex-1">
                  <Search className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-gray-500" />
                  <Input
                    className="border-gray-700 bg-gray-950 pl-9"
                    placeholder="Add ticker (e.g. AAPL or BTC-USD)"
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
                <Button disabled={addMutation.isPending} type="submit">
                  <Plus className="mr-2 size-4" />
                  {addMutation.isPending ? "Adding..." : "Add"}
                </Button>
              </div>

              {formError ? <p className="text-sm text-red-400">{formError}</p> : null}
            </form>
          </CardContent>
        </Card>

        <Separator className="bg-gray-800" />

        {isWatchlistPending ? (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 3 }).map((_, index) => (
              <Card key={`loading-${index}`} className="border-gray-800 bg-gray-900">
                <CardContent className="space-y-4">
                  <div className="h-7 w-24 animate-pulse rounded bg-gray-800" />
                  <div className="h-6 w-32 animate-pulse rounded bg-gray-800" />
                  <div className="h-4 w-full animate-pulse rounded bg-gray-800" />
                  <div className="h-4 w-11/12 animate-pulse rounded bg-gray-800" />
                </CardContent>
              </Card>
            ))}
          </div>
        ) : null}

        {isWatchlistError ? (
          <Card className="border-red-800 bg-gray-900">
            <CardContent>
              <p className="text-sm text-red-400">{getErrorMessage(watchlistError)}</p>
            </CardContent>
          </Card>
        ) : null}

        {!isWatchlistPending && !isWatchlistError && watchlistItems.length === 0 ? (
          <Card className="border-gray-800 bg-gray-900">
            <CardContent className="py-12 text-center">
              <p className="text-lg font-medium text-gray-200">Your watchlist is empty</p>
              <p className="mt-2 text-sm text-gray-400">
                Add a stock or crypto ticker above to start tracking prices and news.
              </p>
            </CardContent>
          </Card>
        ) : null}

        {!isWatchlistPending && !isWatchlistError && watchlistItems.length > 0 ? (
          <div className="space-y-3">
            {removeError ? <p className="text-sm text-red-400">{removeError}</p> : null}
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
              {watchlistItems.map((item) => (
                <WatchlistCard
                  key={item.id}
                  item={item}
                  onRemove={handleRemoveTicker}
                  isRemoving={removingId === item.id}
                />
              ))}
            </div>
          </div>
        ) : null}
      </main>
    </div>
  )
}
