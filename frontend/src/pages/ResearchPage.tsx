import { useMemo, useState, type FormEvent, type ReactElement, type ReactNode } from "react"
import { Link } from "react-router-dom"
import { useMutation } from "@tanstack/react-query"
import {
  AlertTriangle,
  ArrowLeft,
  Brain,
  Database,
  FileText,
  Loader2,
  Newspaper,
  Send,
  Sparkles,
  TrendingUp,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { ApiError, researchAgent } from "@/lib/api"
import type { AssetType, ResearchRequest, ResearchResponse, ResearchTraceEvent } from "@/lib/types"

const examplePrompts = [
  "Give me an overall research overview.",
  "What are the current technical signals?",
  "Summarize recent news and risks.",
  "Analyze fundamentals including revenue, debt, and cash flow.",
]

const getErrorMessage = (error: unknown): string => {
  if (error instanceof ApiError) {
    return error.message
  }
  if (error instanceof Error) {
    return error.message
  }
  return "Research request failed. Please try again."
}

const validateResearchRequest = (
  ticker: string,
  question: string,
  assetType: AssetType
): string | null => {
  if (!ticker) {
    return "Ticker is required"
  }
  if (question.length < 3) {
    return "Question must be at least 3 characters"
  }
  if (assetType === "crypto" && !ticker.includes("-")) {
    return "Crypto tickers must contain a dash, e.g. BTC-USD"
  }
  if (assetType === "stock" && ticker.includes("-")) {
    return "Stock tickers should not contain a dash, e.g. AAPL"
  }
  return null
}

const formatGeneratedAt = (generatedAt: string): string => {
  const date = new Date(generatedAt)
  if (Number.isNaN(date.getTime())) {
    return generatedAt
  }

  return new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date)
}

const getStatusBadgeClassName = (status: ResearchTraceEvent["status"]): string => {
  if (status === "error") {
    return "border-red-800 bg-red-950 text-red-300"
  }
  if (status === "skipped") {
    return "border-amber-800 bg-amber-950 text-amber-300"
  }
  return "border-emerald-800 bg-emerald-950 text-emerald-300"
}

const getPhaseBadgeClassName = (phase: ResearchTraceEvent["phase"]): string => {
  if (phase === "tool") {
    return "border-sky-800 bg-sky-950 text-sky-300"
  }
  if (phase === "synthesizer") {
    return "border-indigo-800 bg-indigo-950 text-indigo-300"
  }
  if (phase === "fallback") {
    return "border-amber-800 bg-amber-950 text-amber-300"
  }
  return "border-gray-700 bg-gray-800 text-gray-200"
}

interface ResearchSectionProps {
  title: string
  icon: ReactElement
  children: ReactNode
}

function ResearchSection({ title, icon, children }: ResearchSectionProps): ReactElement {
  return (
    <Card className="border-gray-800 bg-gray-900 text-gray-100">
      <CardHeader className="pb-1">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold tracking-wide text-gray-300 uppercase">
          {icon}
          {title}
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="text-sm leading-6 whitespace-pre-line text-gray-200">{children}</div>
      </CardContent>
    </Card>
  )
}

export function ResearchPage(): ReactElement {
  const [tickerInput, setTickerInput] = useState("")
  const [assetType, setAssetType] = useState<AssetType>("stock")
  const [question, setQuestion] = useState("")
  const [formError, setFormError] = useState<string | null>(null)
  const [researchResult, setResearchResult] = useState<ResearchResponse | null>(null)

  const researchMutation = useMutation({
    mutationFn: (payload: ResearchRequest) => researchAgent(payload),
    onSuccess: (response) => {
      setResearchResult(response)
    },
  })

  const generatedAt = useMemo(() => {
    if (!researchResult) {
      return null
    }
    return formatGeneratedAt(researchResult.generated_at)
  }, [researchResult])

  const traceEvents = researchResult?.trace ?? []

  const handleSubmit = async (event: FormEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault()
    setFormError(null)

    const ticker = tickerInput.trim().toUpperCase()
    const trimmedQuestion = question.trim()
    setTickerInput(ticker)
    setQuestion(trimmedQuestion)

    const validationError = validateResearchRequest(ticker, trimmedQuestion, assetType)
    if (validationError) {
      setFormError(validationError)
      return
    }

    try {
      await researchMutation.mutateAsync({
        ticker,
        asset_type: assetType,
        question: trimmedQuestion,
      })
    } catch (error: unknown) {
      setFormError(getErrorMessage(error))
    }
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <main className="mx-auto w-full max-w-6xl space-y-6 px-6 py-8">
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
                Ask market questions about tickers and get a structured research brief from the agent.
              </p>
            </div>
          </div>
        </div>

        <Card className="border-gray-800 bg-gray-900 text-gray-100">
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-3">
              <CardTitle className="text-lg text-gray-100">Research prompt</CardTitle>
              <Badge className="border-emerald-800 bg-emerald-950 text-emerald-300" variant="outline">
                Live agent endpoint
              </Badge>
            </div>
          </CardHeader>
          <CardContent>
            <form className="space-y-4" onSubmit={handleSubmit}>
              <div className="grid gap-4 md:grid-cols-[180px_1fr]">
                <div className="space-y-2">
                  <label className="text-sm font-medium text-gray-300" htmlFor="research-asset-type">
                    Asset type
                  </label>
                  <select
                    className="h-9 w-full rounded-md border border-gray-700 bg-gray-950 px-3 text-sm text-gray-100"
                    id="research-asset-type"
                    value={assetType}
                    onChange={(event) => setAssetType(event.target.value as AssetType)}
                  >
                    <option value="stock">Stock</option>
                    <option value="crypto">Crypto</option>
                  </select>
                </div>

                <div className="space-y-2">
                  <label className="text-sm font-medium text-gray-300" htmlFor="research-ticker">
                    Ticker
                  </label>
                  <Input
                    className="border-gray-700 bg-gray-950"
                    id="research-ticker"
                    placeholder={assetType === "crypto" ? "BTC-USD" : "AAPL"}
                    value={tickerInput}
                    onChange={(event) => setTickerInput(event.target.value.toUpperCase())}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <label className="text-sm font-medium text-gray-300" htmlFor="research-question">
                  Question
                </label>
                <textarea
                  className="min-h-32 w-full resize-y rounded-md border border-gray-700 bg-gray-950 px-3 py-2 text-sm text-gray-100 outline-none transition-colors placeholder:text-gray-500 focus:border-gray-600 focus:ring-2 focus:ring-gray-700 disabled:cursor-not-allowed disabled:opacity-60"
                  disabled={researchMutation.isPending}
                  id="research-question"
                  placeholder="Ask about price action, recent news, risks, fundamentals, or technical setup."
                  value={question}
                  onChange={(event) => setQuestion(event.target.value)}
                />
              </div>

              <div className="flex flex-wrap gap-2">
                {examplePrompts.map((example) => (
                  <button
                    className="rounded-full border border-gray-700 bg-gray-950 px-3 py-1.5 text-left text-xs text-gray-300 transition-colors hover:border-gray-600 hover:bg-gray-800 hover:text-gray-100"
                    key={example}
                    type="button"
                    onClick={() => setQuestion(example)}
                  >
                    {example}
                  </button>
                ))}
              </div>

              {formError ? (
                <p className="text-sm text-red-400" role="alert">
                  {formError}
                </p>
              ) : null}

              <Button disabled={researchMutation.isPending} type="submit">
                {researchMutation.isPending ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : (
                  <Send className="mr-2 size-4" />
                )}
                {researchMutation.isPending ? "Researching..." : "Run research"}
              </Button>
            </form>
          </CardContent>
        </Card>

        <Separator className="bg-gray-800" />

        {researchMutation.isPending ? (
          <Card className="border-gray-800 bg-gray-900 text-gray-100">
            <CardContent className="space-y-4 py-6">
              <div className="flex items-center gap-3 text-sm text-gray-300">
                <Loader2 className="size-4 animate-spin text-emerald-300" />
                Research agent is gathering market context...
              </div>
              <div className="grid gap-3 md:grid-cols-3">
                <div className="h-24 animate-pulse rounded-lg bg-gray-800" />
                <div className="h-24 animate-pulse rounded-lg bg-gray-800" />
                <div className="h-24 animate-pulse rounded-lg bg-gray-800" />
              </div>
            </CardContent>
          </Card>
        ) : null}

        {researchResult ? (
          <div className="space-y-4">
            <Card className="border-emerald-900/70 bg-gray-900 text-gray-100">
              <CardHeader className="space-y-3">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <CardTitle className="flex items-center gap-2 text-2xl font-bold tracking-tight text-gray-100">
                      <Sparkles className="size-5 text-emerald-300" />
                      {researchResult.ticker}
                    </CardTitle>
                    <p className="mt-2 text-sm text-gray-400">{researchResult.question}</p>
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge className="border-gray-700 bg-gray-800 text-gray-200" variant="outline">
                      {researchResult.asset_type}
                    </Badge>
                    {generatedAt ? (
                      <Badge className="border-sky-800 bg-sky-950 text-sky-300" variant="outline">
                        Generated {generatedAt}
                      </Badge>
                    ) : null}
                  </div>
                </div>
              </CardHeader>
              <CardContent>
                <p className="text-base leading-7 text-gray-200">{researchResult.summary}</p>
              </CardContent>
            </Card>

            <div className="grid gap-4 lg:grid-cols-2">
              <ResearchSection title="Price context" icon={<TrendingUp className="size-4 text-emerald-300" />}>
                {researchResult.price_context || "No price context returned."}
              </ResearchSection>

              <ResearchSection title="News summary" icon={<Newspaper className="size-4 text-sky-300" />}>
                {researchResult.news_summary || "No news summary returned."}
              </ResearchSection>

              <ResearchSection title="Technical context" icon={<Database className="size-4 text-indigo-300" />}>
                {researchResult.technical_context || "No technical context returned."}
              </ResearchSection>

              {researchResult.fundamental_context.trim().length > 0 ? (
                <ResearchSection title="Fundamental context" icon={<FileText className="size-4 text-amber-300" />}>
                  {researchResult.fundamental_context}
                </ResearchSection>
              ) : null}
            </div>

            <div className="grid gap-4 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
              <ResearchSection title="Risks" icon={<AlertTriangle className="size-4 text-amber-300" />}>
                {researchResult.risks.length > 0 ? (
                  <ul className="space-y-2">
                    {researchResult.risks.map((risk, index) => (
                      <li className="flex gap-2" key={`${risk}-${index}`}>
                        <span className="mt-2 size-1.5 shrink-0 rounded-full bg-amber-300" />
                        <span>{risk}</span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <span className="text-gray-500">No risks returned.</span>
                )}
              </ResearchSection>

              <Card className="border-gray-800 bg-gray-900 text-gray-100">
                <CardHeader className="pb-1">
                  <CardTitle className="flex items-center gap-2 text-sm font-semibold tracking-wide text-gray-300 uppercase">
                    <Database className="size-4 text-gray-300" />
                    Agent traceability
                  </CardTitle>
                  <p className="text-xs leading-5 text-gray-500 normal-case">
                    Tools, internal endpoints, and execution steps used to generate this brief.
                  </p>
                </CardHeader>
                <CardContent>
                  {traceEvents.length > 0 ? (
                    <div className="space-y-3">
                      {traceEvents.map((event) => (
                        <div
                          className="rounded-lg border border-gray-800 bg-gray-950 p-3"
                          key={`${event.step}-${event.phase}-${event.action}-${event.tool ?? "none"}`}
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="flex size-6 items-center justify-center rounded-full border border-gray-700 bg-gray-900 text-xs font-semibold text-gray-400">
                              {event.step}
                            </span>
                            <Badge className={getPhaseBadgeClassName(event.phase)} variant="outline">
                              {event.phase}
                            </Badge>
                            <Badge className={getStatusBadgeClassName(event.status)} variant="outline">
                              {event.status}
                            </Badge>
                            <span className="text-xs font-medium tracking-wide text-gray-400 uppercase">
                              {event.action.replace(/_/g, " ")}
                            </span>
                          </div>

                          <p className="mt-3 text-sm leading-6 text-gray-200">{event.message}</p>

                          <div className="mt-3 flex flex-wrap items-center gap-2">
                            {event.tool ? (
                              <Badge className="border-gray-700 bg-gray-800 text-gray-200" variant="outline">
                                {event.tool}
                              </Badge>
                            ) : null}
                            {event.endpoint ? (
                              <span className="break-all font-mono text-xs text-gray-500">
                                {event.endpoint}
                              </span>
                            ) : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <span className="text-sm text-gray-500">
                      No traceability data was returned for this brief.
                    </span>
                  )}
                </CardContent>
              </Card>
            </div>
          </div>
        ) : null}

        {!researchMutation.isPending && !researchResult ? (
          <Card className="border-gray-800 bg-gray-900 text-gray-100">
            <CardContent className="py-10 text-center">
              <p className="text-sm text-gray-400">
                Enter a ticker and question to generate a structured research brief.
              </p>
            </CardContent>
          </Card>
        ) : null}
      </main>
    </div>
  )
}
