import { useState, type FormEvent, type ReactElement } from "react"
import { Link } from "react-router-dom"
import { ArrowLeft, Brain, CheckCircle2, Send, Sparkles } from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"

const examplePrompts = [
  "Summarize recent news for AAPL",
  "Compare BTC-USD momentum over the last month",
  "Explain NVDA's latest price movement",
  "What risks should I watch for this ticker?",
]

const plannedCapabilities = [
  "price context",
  "news summary",
  "fundamentals",
  "technical indicators",
  "watchlist-aware insights",
]

interface PreviewResponse {
  ticker: string
  question: string
}

export function ResearchPage(): ReactElement {
  const [tickerInput, setTickerInput] = useState("")
  const [prompt, setPrompt] = useState("")
  const [formError, setFormError] = useState<string | null>(null)
  const [previewResponse, setPreviewResponse] = useState<PreviewResponse | null>(null)

  const handleSubmit = (event: FormEvent<HTMLFormElement>): void => {
    event.preventDefault()
    setFormError(null)

    const ticker = tickerInput.trim().toUpperCase()
    const question = prompt.trim()

    if (!ticker) {
      setFormError("Ticker is required")
      return
    }
    if (!question) {
      setFormError("Question is required")
      return
    }

    setTickerInput(ticker)
    setPreviewResponse({ ticker, question })
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <main className="mx-auto w-full max-w-5xl space-y-6 px-6 py-8">
        <Button asChild size="sm" variant="ghost">
          <Link to="/dashboard">
            <ArrowLeft className="mr-2 size-4" />
            Back to dashboard
          </Link>
        </Button>

        <div className="space-y-2">
          <div className="flex items-center gap-3">
            <div className="flex size-10 items-center justify-center rounded-lg border border-gray-800 bg-gray-900">
              <Brain className="size-5 text-emerald-300" />
            </div>
            <div>
              <h1 className="text-3xl font-semibold tracking-tight text-gray-100">AI research</h1>
              <p className="mt-1 text-sm text-gray-400">
                Ask market questions about tickers and preview the research workflow.
              </p>
            </div>
          </div>
        </div>

        <Card className="border-gray-800 bg-gray-900 text-gray-100">
          <CardHeader>
            <CardTitle className="text-lg text-gray-100">Research prompt</CardTitle>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={handleSubmit}>
              <div className="grid gap-4 md:grid-cols-[220px_1fr]">
                <div className="space-y-2">
                  <label className="text-sm font-medium text-gray-300" htmlFor="research-ticker">
                    Ticker
                  </label>
                  <Input
                    className="border-gray-700 bg-gray-950"
                    id="research-ticker"
                    placeholder="AAPL or BTC-USD"
                    value={tickerInput}
                    onChange={(event) => setTickerInput(event.target.value)}
                  />
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium text-gray-300" htmlFor="research-prompt">
                    Question
                  </label>
                  <textarea
                    className="min-h-28 w-full resize-y rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100 outline-none transition-colors placeholder:text-gray-500 focus:border-gray-600 focus:ring-2 focus:ring-gray-700"
                    id="research-prompt"
                    placeholder="Ask about price action, recent news, risks, fundamentals, or technical setup."
                    value={prompt}
                    onChange={(event) => setPrompt(event.target.value)}
                  />
                </div>
              </div>

              <div className="flex flex-wrap gap-2">
                {examplePrompts.map((example) => (
                  <button
                    className="rounded-full border border-gray-700 bg-gray-950 px-3 py-1.5 text-left text-xs text-gray-300 transition-colors hover:border-gray-600 hover:bg-gray-800 hover:text-gray-100"
                    key={example}
                    type="button"
                    onClick={() => setPrompt(example)}
                  >
                    {example}
                  </button>
                ))}
              </div>

              {formError ? <p className="text-sm text-amber-300">{formError}</p> : null}

              <Button type="submit">
                <Send className="mr-2 size-4" />
                Submit
              </Button>
            </form>
          </CardContent>
        </Card>

        <Separator className="bg-gray-800" />

        {previewResponse ? (
          <Card className="border-emerald-900/70 bg-gray-900 text-gray-100">
            <CardHeader className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-3">
                <CardTitle className="flex items-center gap-2 text-xl text-gray-100">
                  <Sparkles className="size-5 text-emerald-300" />
                  Research preview
                </CardTitle>
                <Badge className="border-emerald-800 bg-emerald-950 text-emerald-300" variant="outline">
                  Research agent coming soon
                </Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="grid gap-3 md:grid-cols-[180px_1fr]">
                <div className="rounded-lg border border-gray-800 bg-gray-950 p-3">
                  <p className="text-xs font-semibold tracking-wide text-gray-500 uppercase">Ticker</p>
                  <p className="mt-2 text-2xl font-semibold text-gray-100">{previewResponse.ticker}</p>
                </div>
                <div className="rounded-lg border border-gray-800 bg-gray-950 p-3">
                  <p className="text-xs font-semibold tracking-wide text-gray-500 uppercase">Question</p>
                  <p className="mt-2 text-sm leading-6 text-gray-200">{previewResponse.question}</p>
                </div>
              </div>

              <div className="rounded-lg border border-gray-800 bg-gray-950 p-4">
                <p className="text-sm text-gray-300">
                  This preview shows where the LangGraph research response will appear once the
                  backend agent is connected.
                </p>
                <div className="mt-4 grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                  {plannedCapabilities.map((capability) => (
                    <div className="flex items-center gap-2 text-sm text-gray-300" key={capability}>
                      <CheckCircle2 className="size-4 text-emerald-300" />
                      {capability}
                    </div>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>
        ) : (
          <Card className="border-gray-800 bg-gray-900 text-gray-100">
            <CardContent className="py-10 text-center">
              <p className="text-sm text-gray-400">
                Submit a ticker and question to preview the research response layout.
              </p>
            </CardContent>
          </Card>
        )}
      </main>
    </div>
  )
}
