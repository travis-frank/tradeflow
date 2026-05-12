from __future__ import annotations

import json
import operator
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, TypedDict, cast

import structlog
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import END, StateGraph

from agents.tools import (
    CRYPTO_RESEARCH_TOOL_MAP,
    CRYPTO_RESEARCH_TOOLS,
    STOCK_RESEARCH_TOOL_MAP,
    STOCK_RESEARCH_TOOLS,
)
from core.config import get_settings

log = structlog.get_logger()

AssetType = Literal["stock", "crypto"]
MAX_TOOL_ITERATIONS = 3

PLANNER_SYSTEM_PROMPT = """You are the tradeflow.ai ReAct research planner.
Think step by step about which financial data is needed, then either call tools or stop when enough data has been gathered.

Rules:
- Only call tools relevant to the question.
- For crypto questions, skip fundamentals tools.
- For technical questions, focus on indicators and historical prices.
- For news or sentiment questions, focus on news.
- For stock questions, call get_current_price first as baseline context.
- For crypto questions, call get_crypto_price first as baseline context.
- For fundamental stock questions, choose the relevant statement tools: get_fundamentals, get_balance_sheet, and/or get_cash_flow.
- Before calling tools, briefly state why those tools are needed.
- If enough data has already been gathered, do not call more tools. State that you have enough context.
- Do not invent data. Use tools for market facts."""

SYNTHESIZER_SYSTEM_PROMPT = """You are a careful financial analyst writing for tradeflow.ai.
Given the ticker, asset type, user question, planner trace, and gathered data:
- Return only valid JSON. Do not wrap it in Markdown fences.
- Use exactly these fields:
{
  "summary": "2-3 sentence overall assessment",
  "price_context": "current price, trend, or data availability",
  "news_summary": "recent news themes or Data not available",
  "technical_context": "RSI/MACD/trend context or Data not available",
  "fundamental_context": "stock fundamentals or empty string for crypto",
  "risks": ["specific risk 1", "specific risk 2"],
  "sources": [{"tool": "get_current_price", "endpoint": "/api/prices/current/{ticker}"}]
}
- Identify 2-3 specific risks only when supported by data; otherwise state what is missing.
- For crypto assets, fundamental_context must be an empty string.
- Call out failed or missing tool data plainly.
- Do not speculate beyond the supplied data.
- This is market research, not financial advice."""


def _merge_tool_results(
    current: dict[str, Any],
    new: dict[str, Any],
) -> dict[str, Any]:
    return {**current, **new}


class ResearchState(TypedDict):
    ticker: str
    asset_type: str
    question: str
    messages: Annotated[list[BaseMessage], operator.add]
    tool_results: Annotated[dict[str, Any], _merge_tool_results]
    iterations: int
    report: str | None


def _has_llm_key() -> bool:
    api_key = get_settings().openai_api_key.strip()
    return bool(api_key) and api_key != "your-key-here"


def _llm_with_tools(asset_type: AssetType) -> Any:
    llm = ChatOpenAI(model="gpt-4o-mini", api_key=get_settings().openai_api_key)
    tools = CRYPTO_RESEARCH_TOOLS if asset_type == "crypto" else STOCK_RESEARCH_TOOLS
    return llm.bind_tools(tools)


def _plain_llm() -> ChatOpenAI:
    return ChatOpenAI(model="gpt-4o-mini", api_key=get_settings().openai_api_key)


async def _planner_node(state: ResearchState) -> dict[str, Any]:
    llm = _llm_with_tools(state["asset_type"])
    response = await llm.ainvoke(
        [
            SystemMessage(content=PLANNER_SYSTEM_PROMPT),
            HumanMessage(
                content=(
                    "Current gathered tool names: "
                    f"{', '.join(state['tool_results'].keys()) or 'none'}\n"
                    f"Tool iterations used: {state['iterations']} of {MAX_TOOL_ITERATIONS}"
                )
            ),
            *state["messages"],
        ]
    )
    return {"messages": [response]}


async def _tools_node(state: ResearchState) -> dict[str, Any]:
    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", []) or []
    tool_results: dict[str, Any] = {}
    messages: list[BaseMessage] = []
    tool_map = (
        CRYPTO_RESEARCH_TOOL_MAP
        if state["asset_type"] == "crypto"
        else STOCK_RESEARCH_TOOL_MAP
    )

    for tool_call in tool_calls:
        name = str(tool_call.get("name", ""))
        args = cast(dict[str, Any], tool_call.get("args", {}))
        tool_call_id = str(tool_call.get("id", name))
        selected_tool = tool_map.get(name)

        if selected_tool is None:
            result = json.dumps({"error": f"Unknown or disallowed tool: {name}"})
        else:
            try:
                result = await selected_tool.ainvoke(args)
            except Exception as error:
                log.exception("research tool execution failed", tool=name)
                result = json.dumps({"error": "tool execution failed", "detail": str(error)})

        tool_results[name] = result
        messages.append(ToolMessage(content=str(result), tool_call_id=tool_call_id, name=name))

    return {
        "messages": messages,
        "tool_results": tool_results,
        "iterations": state["iterations"] + 1,
    }


async def _synthesizer_node(state: ResearchState) -> dict[str, Any]:
    llm = _plain_llm()
    response = await llm.ainvoke(
        [
            SystemMessage(content=SYNTHESIZER_SYSTEM_PROMPT),
            HumanMessage(
                content=json.dumps(
                    {
                        "ticker": state["ticker"],
                        "asset_type": state["asset_type"],
                        "question": state["question"],
                        "tool_results": state["tool_results"],
                        "planner_trace": [str(message.content) for message in state["messages"]],
                    },
                    default=str,
                )
            ),
        ]
    )
    report = str(response.content)
    return {"messages": [response], "report": report}


def _route_after_planner(state: ResearchState) -> str:
    if state["iterations"] >= MAX_TOOL_ITERATIONS:
        log.warning(
            "research max tool iterations reached",
            ticker=state["ticker"],
            iterations=state["iterations"],
        )
        return "synthesizer"

    last_message = state["messages"][-1]
    tool_calls = getattr(last_message, "tool_calls", []) or []
    if tool_calls:
        return "tools"
    return "synthesizer"


def _build_graph() -> Any:
    graph = StateGraph(ResearchState)
    graph.add_node("planner", _planner_node)
    graph.add_node("tools", _tools_node)
    graph.add_node("synthesizer", _synthesizer_node)
    graph.set_entry_point("planner")
    graph.add_conditional_edges(
        "planner",
        _route_after_planner,
        {"tools": "tools", "synthesizer": "synthesizer"},
    )
    graph.add_edge("tools", "planner")
    graph.add_edge("synthesizer", END)
    return graph.compile()


def _date_range(days: int = 30) -> tuple[str, str]:
    end = datetime.now(tz=UTC).date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _question_wants_news(question: str) -> bool:
    lowered = question.lower()
    return any(term in lowered for term in ("news", "sentiment", "headline", "recent", "risk"))


def _question_wants_technical(question: str) -> bool:
    lowered = question.lower()
    return any(
        term in lowered
        for term in (
            "technical",
            "indicator",
            "rsi",
            "macd",
            "momentum",
            "trend",
            "movement",
            "price",
            "last month",
        )
    )


def _question_wants_fundamentals(question: str) -> bool:
    lowered = question.lower()
    return any(
        term in lowered
        for term in (
            "balance",
            "cash flow",
            "cashflow",
            "debt",
            "earnings",
            "financial",
            "fundamental",
            "income",
            "revenue",
            "valuation",
        )
    )


def _question_is_general_research(question: str) -> bool:
    lowered = question.lower()
    return any(
        term in lowered
        for term in (
            "overview",
            "analyze",
            "analysis",
            "research",
            "look into",
            "what do you think",
            "should i",
            "summary",
            "summarize",
            "overall",
        )
    )


async def _run_tool(
    name: str,
    args: dict[str, Any],
    tool_map: dict[str, Any],
) -> str:
    selected_tool = tool_map[name]
    try:
        result = await selected_tool.ainvoke(args)
    except Exception as error:
        log.exception("deterministic research tool failed", tool=name)
        return json.dumps({"error": "tool execution failed", "detail": str(error)})
    return str(result)


async def _collect_deterministic_tool_results(
    ticker: str,
    question: str,
    asset_type: AssetType,
) -> dict[str, Any]:
    start, end = _date_range()
    is_general_research = _question_is_general_research(question)
    tool_results: dict[str, Any] = {}

    if asset_type == "crypto":
        tool_results["get_crypto_price"] = await _run_tool(
            "get_crypto_price",
            {"ticker": ticker},
            CRYPTO_RESEARCH_TOOL_MAP,
        )
        if is_general_research or _question_wants_technical(question):
            tool_results["get_crypto_historical"] = await _run_tool(
                "get_crypto_historical",
                {"ticker": ticker, "start": start, "end": end},
                CRYPTO_RESEARCH_TOOL_MAP,
            )
        if is_general_research or _question_wants_news(question):
            tool_results["get_news"] = await _run_tool(
                "get_news",
                {"ticker": ticker},
                CRYPTO_RESEARCH_TOOL_MAP,
            )
        return tool_results

    tool_results["get_current_price"] = await _run_tool(
        "get_current_price",
        {"ticker": ticker},
        STOCK_RESEARCH_TOOL_MAP,
    )
    if is_general_research or _question_wants_technical(question):
        tool_results["get_historical_prices"] = await _run_tool(
            "get_historical_prices",
            {"ticker": ticker, "start": start, "end": end},
            STOCK_RESEARCH_TOOL_MAP,
        )
        tool_results["get_indicators"] = await _run_tool(
            "get_indicators",
            {"ticker": ticker},
            STOCK_RESEARCH_TOOL_MAP,
        )
    if is_general_research or _question_wants_news(question):
        tool_results["get_news"] = await _run_tool(
            "get_news",
            {"ticker": ticker},
            STOCK_RESEARCH_TOOL_MAP,
        )
    if is_general_research or _question_wants_fundamentals(question):
        tool_results["get_fundamentals"] = await _run_tool(
            "get_fundamentals",
            {"ticker": ticker},
            STOCK_RESEARCH_TOOL_MAP,
        )
        tool_results["get_balance_sheet"] = await _run_tool(
            "get_balance_sheet",
            {"ticker": ticker},
            STOCK_RESEARCH_TOOL_MAP,
        )
        tool_results["get_cash_flow"] = await _run_tool(
            "get_cash_flow",
            {"ticker": ticker},
            STOCK_RESEARCH_TOOL_MAP,
        )

    if len(tool_results) == 1:
        tool_results["get_news"] = await _run_tool(
            "get_news",
            {"ticker": ticker},
            STOCK_RESEARCH_TOOL_MAP,
        )

    return tool_results


def _parse_tool_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {"raw": value}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"raw": value}
    if isinstance(parsed, dict):
        return parsed
    return {"raw": parsed}


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _format_currency(value: Any, currency: str = "USD") -> str:
    number = _coerce_float(value)
    if number is None:
        return "Data not available"
    return f"${number:,.2f} {currency}"


def _format_large_number(value: Any) -> str:
    number = _coerce_float(value)
    if number is None:
        return "Data not available"

    sign = "-" if number < 0 else ""
    absolute = abs(number)

    if absolute >= 1_000_000_000:
        return f"{sign}${absolute / 1_000_000_000:,.2f}B"
    if absolute >= 1_000_000:
        return f"{sign}${absolute / 1_000_000:,.2f}M"
    return f"{sign}${absolute:,.2f}"


def _format_percent_like(value: Any) -> str:
    number = _coerce_float(value)
    if number is None:
        return "Data not available"
    return f"{number:.2f}"


def _humanize_metric_name(name: str) -> str:
    return name.replace("_", " ").capitalize()


def _format_price_context(tool_results: dict[str, Any], asset_type: AssetType) -> str:
    key = "get_crypto_price" if asset_type == "crypto" else "get_current_price"
    data = _parse_tool_json(tool_results.get(key))
    if "error" in data:
        return f"Price data unavailable: {data.get('detail', data['error'])}"
    price = data.get("price")
    currency = data.get("currency", "USD")
    ticker = data.get("ticker")
    if price is None:
        return "Price data unavailable."
    return f"{ticker} last traded at {_format_currency(price, str(currency))}."


def _format_news_summary(tool_results: dict[str, Any]) -> str:
    data = _parse_tool_json(tool_results.get("get_news"))
    if "error" in data:
        return f"News unavailable: {data.get('detail', data['error'])}"
    articles = data.get("articles")
    if not isinstance(articles, list) or not articles:
        return ""

    headlines: list[str] = []
    for article in articles[:3]:
        if isinstance(article, dict) and article.get("title"):
            source = article.get("source")
            suffix = f" ({source})" if source else ""
            headlines.append(f"{article['title']}{suffix}")
    if not headlines:
        return ""
    return f"Recent headlines include: {'; '.join(headlines)}."


def _latest_non_null(values: list[Any]) -> Any | None:
    for value in reversed(values):
        if value is not None:
            return value
    return None


def _format_technical_context(tool_results: dict[str, Any], asset_type: AssetType) -> str:
    if asset_type == "crypto":
        data = _parse_tool_json(tool_results.get("get_crypto_historical"))
        bars = data.get("bars")
        if isinstance(bars, list) and bars:
            return (
                f"Historical crypto context includes {len(bars)} recent bars "
                "for the selected lookback window."
            )
        return ""

    data = _parse_tool_json(tool_results.get("get_indicators"))
    if "error" in data:
        return f"Technical indicators unavailable: {data.get('detail', data['error'])}"
    bars = data.get("bars")
    rsi = data.get("rsi")
    macd = data.get("macd")

    parts: list[str] = []
    if isinstance(bars, list):
        parts.append(f"{len(bars)} indicator observations available.")
    if isinstance(rsi, list):
        latest_rsi = _latest_non_null(rsi)
        if latest_rsi is not None:
            parts.append(f"Latest RSI: {_format_percent_like(latest_rsi)}.")
        else:
            parts.append("RSI data is not available for the latest observation.")
    if isinstance(macd, list) and macd:
        latest_macd = next((point for point in reversed(macd) if isinstance(point, dict)), None)
        if latest_macd is not None:
            macd_value = latest_macd.get("macd")
            if macd_value is None:
                parts.append("MACD data is not available for the latest observation.")
            else:
                parts.append(f"Latest MACD: {_format_percent_like(macd_value)}.")
    elif "get_indicators" in tool_results:
        parts.append("MACD data is not available.")

    return " ".join(parts)


def _statement_metrics(statement_type: str, latest: dict[str, Any]) -> list[tuple[str, Any]]:
    preferred_metrics: dict[str, list[str]] = {
        "get_fundamentals": ["revenue", "gross_profit", "operating_income"],
        "get_balance_sheet": ["total_assets", "total_liabilities", "total_equity"],
        "get_cash_flow": [
            "operating_cash_flow",
            "investing_cash_flow",
            "financing_cash_flow",
        ],
    }
    preferred = [
        (name, latest[name])
        for name in preferred_metrics.get(statement_type, [])
        if latest.get(name) is not None
    ]
    if preferred:
        return preferred

    return [
        (key, value)
        for key, value in latest.items()
        if key not in {"period_date", "period_type"} and value is not None
    ][:3]


def _format_fundamental_context(tool_results: dict[str, Any], asset_type: AssetType) -> str:
    if asset_type == "crypto":
        return ""

    statement_labels: dict[str, str] = {
        "get_fundamentals": "Income statement",
        "get_balance_sheet": "Balance sheet",
        "get_cash_flow": "Cash flow",
    }
    contexts: list[str] = []

    for tool_name, label in statement_labels.items():
        if tool_name not in tool_results:
            continue

        data = _parse_tool_json(tool_results.get(tool_name))
        if "error" in data:
            contexts.append(f"{label} unavailable: {data.get('detail', data['error'])}")
            continue

        periods = data.get("periods")
        if not isinstance(periods, list) or not periods:
            continue

        latest = periods[0]
        if not isinstance(latest, dict):
            continue

        period = latest.get("period_date", "latest period")
        metrics = [
            f"{_humanize_metric_name(key)} {_format_large_number(value)}"
            for key, value in _statement_metrics(tool_name, latest)
        ]
        contexts.append(f"{label}, {period}: {', '.join(metrics)}." if metrics else f"{label}, {period}.")

    return "\n".join(contexts)


def _format_deterministic_summary(
    ticker: str,
    question: str,
    asset_type: AssetType,
) -> str:
    if asset_type == "crypto":
        if _question_is_general_research(question):
            return (
                f"Deterministic research brief for {ticker} using current price, "
                "recent historical data, and news."
            )
        if _question_wants_technical(question):
            return (
                f"Deterministic technical brief for {ticker} using current price "
                "and recent historical data."
            )
        return f"Deterministic research brief for {ticker} using available market data."

    if _question_is_general_research(question):
        return (
            f"Deterministic research brief for {ticker} using current price, recent news, "
            "technical indicators, and fundamental statements."
        )
    if _question_wants_fundamentals(question):
        return (
            f"Deterministic fundamentals brief for {ticker} using income statement, "
            "balance sheet, and cash flow data."
        )
    if _question_wants_technical(question):
        return (
            f"Deterministic technical brief for {ticker} using current price, "
            "recent historical data, and indicators."
        )
    return f"Deterministic research brief for {ticker} using available market data."


def _build_sources(tool_results: dict[str, Any]) -> list[dict[str, Any]]:
    source_paths: dict[str, str] = {
        "get_current_price": "/api/prices/current/{ticker}",
        "get_historical_prices": "/api/prices/historical/{ticker}",
        "get_indicators": "/api/prices/{ticker}/indicators",
        "get_news": "/api/news/{ticker}",
        "get_fundamentals": "/api/fundamentals/{ticker}/income",
        "get_balance_sheet": "/api/fundamentals/{ticker}/balance-sheet",
        "get_cash_flow": "/api/fundamentals/{ticker}/cash-flow",
        "get_crypto_price": "/api/crypto/current/{ticker}",
        "get_crypto_historical": "/api/crypto/historical/{ticker}",
    }
    return [
        {"tool": name, "endpoint": source_paths.get(name, "")}
        for name in tool_results
        if name in source_paths
    ]


def _strip_json_fence(report: str) -> str:
    stripped = report.strip()
    if not stripped.startswith("```"):
        return stripped

    lines = stripped.splitlines()
    if len(lines) >= 2 and lines[0].startswith("```"):
        if lines[-1].strip() == "```":
            return "\n".join(lines[1:-1]).strip()
        return "\n".join(lines[1:]).strip()
    return stripped


def _parse_report_json(report: str | None) -> dict[str, Any] | None:
    if report is None:
        return None

    try:
        parsed = json.loads(_strip_json_fence(report))
    except json.JSONDecodeError:
        log.warning("failed to parse research synthesizer json")
        return None

    if not isinstance(parsed, dict):
        log.warning("research synthesizer json was not an object")
        return None

    return parsed


def _string_from_report(
    parsed_report: dict[str, Any] | None,
    field_name: str,
    fallback: str,
) -> str:
    if parsed_report is None:
        return fallback
    value = parsed_report.get(field_name)
    if isinstance(value, str):
        return value
    return fallback


def _risks_from_report(
    parsed_report: dict[str, Any] | None,
    fallback: list[str],
) -> list[str]:
    if parsed_report is None:
        return fallback
    risks = parsed_report.get("risks")
    if not isinstance(risks, list):
        return fallback
    return [str(risk) for risk in risks if str(risk).strip()] or fallback


def _sources_from_report(
    parsed_report: dict[str, Any] | None,
    fallback: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if parsed_report is None:
        return fallback
    sources = parsed_report.get("sources")
    if not isinstance(sources, list):
        return fallback
    valid_sources = [source for source in sources if isinstance(source, dict)]
    return valid_sources or fallback


def _build_response_payload(
    ticker: str,
    question: str,
    asset_type: AssetType,
    tool_results: dict[str, Any],
    report: str | None,
    llm_available: bool,
) -> dict[str, Any]:
    price_context = _format_price_context(tool_results, asset_type)
    news_summary = _format_news_summary(tool_results)
    technical_context = _format_technical_context(tool_results, asset_type)
    fundamental_context = _format_fundamental_context(tool_results, asset_type)
    sources = _build_sources(tool_results)

    parsed_report = _parse_report_json(report) if llm_available else None
    summary = _string_from_report(
        parsed_report,
        "summary",
        _format_deterministic_summary(ticker, question, asset_type),
    )
    price_context = _string_from_report(parsed_report, "price_context", price_context)
    news_summary = _string_from_report(parsed_report, "news_summary", news_summary)
    technical_context = _string_from_report(
        parsed_report,
        "technical_context",
        technical_context,
    )
    fundamental_context = _string_from_report(
        parsed_report,
        "fundamental_context",
        fundamental_context,
    )
    if asset_type == "crypto":
        fundamental_context = ""

    fallback_risks = (
        []
        if llm_available
        else ["Analysis generated in deterministic mode; LLM synthesis is disabled."]
    )
    risks = _risks_from_report(parsed_report, fallback_risks)
    sources = _sources_from_report(parsed_report, sources)

    return {
        "ticker": ticker,
        "asset_type": asset_type,
        "question": question,
        "summary": summary,
        "price_context": price_context,
        "news_summary": news_summary,
        "technical_context": technical_context,
        "fundamental_context": fundamental_context,
        "risks": risks,
        "sources": sources,
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "tool_results": tool_results,
        "report": report,
    }


async def run_research(ticker: str, question: str, asset_type: str) -> dict[str, Any]:
    normalized_ticker = ticker.strip().upper()
    normalized_asset_type = cast(AssetType, asset_type)
    llm_available = _has_llm_key()

    if not llm_available:
        tool_results = await _collect_deterministic_tool_results(
            normalized_ticker,
            question,
            normalized_asset_type,
        )
        return _build_response_payload(
            normalized_ticker,
            question,
            normalized_asset_type,
            tool_results,
            report=None,
            llm_available=False,
        )

    graph = _build_graph()
    initial_state: ResearchState = {
        "ticker": normalized_ticker,
        "asset_type": normalized_asset_type,
        "question": question,
        "messages": [
            HumanMessage(
                content=(
                    f"Ticker: {normalized_ticker}\n"
                    f"Asset type: {normalized_asset_type}\n"
                    f"Question: {question}"
                )
            )
        ],
        "tool_results": {},
        "iterations": 0,
        "report": None,
    }

    try:
        final_state = await graph.ainvoke(initial_state, config={"recursion_limit": 8})
    except Exception:
        log.exception("research graph failed", ticker=normalized_ticker)
        tool_results = await _collect_deterministic_tool_results(
            normalized_ticker,
            question,
            normalized_asset_type,
        )
        return _build_response_payload(
            normalized_ticker,
            question,
            normalized_asset_type,
            tool_results,
            report=None,
            llm_available=False,
        )

    return _build_response_payload(
        normalized_ticker,
        question,
        normalized_asset_type,
        dict(final_state.get("tool_results", {})),
        report=final_state.get("report"),
        llm_available=True,
    )
