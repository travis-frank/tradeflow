from __future__ import annotations

from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def _ema_array(values: np.ndarray, period: int) -> np.ndarray:
    length: int = values.shape[0]
    ema: np.ndarray = np.full(length, np.nan, dtype=float)

    if period <= 0 or length < period:
        return ema

    alpha: float = 2.0 / (period + 1.0)
    start_index: int = period - 1
    ema[start_index] = float(np.mean(values[:period]))

    for idx in range(start_index + 1, length):
        ema[idx] = (values[idx] - ema[idx - 1]) * alpha + ema[idx - 1]

    return ema


def calculate_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    if not closes:
        return []

    prices: np.ndarray = np.asarray(closes, dtype=float)
    length: int = prices.shape[0]
    rsi: np.ndarray = np.full(length, np.nan, dtype=float)

    if period <= 0 or length <= period:
        return [None] * length

    deltas: np.ndarray = np.diff(prices)
    gains: np.ndarray = np.where(deltas > 0.0, deltas, 0.0)
    losses: np.ndarray = np.where(deltas < 0.0, -deltas, 0.0)

    avg_gain: float = float(np.mean(gains[:period]))
    avg_loss: float = float(np.mean(losses[:period]))

    def _rsi_from_averages(current_avg_gain: float, current_avg_loss: float) -> float:
        if current_avg_loss == 0.0:
            if current_avg_gain == 0.0:
                return 50.0
            return 100.0
        rs: float = current_avg_gain / current_avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    rsi[period] = _rsi_from_averages(avg_gain, avg_loss)

    for delta_index in range(period, deltas.shape[0]):
        avg_gain = ((avg_gain * (period - 1)) + gains[delta_index]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[delta_index]) / period
        rsi[delta_index + 1] = _rsi_from_averages(avg_gain, avg_loss)

    result: list[float | None] = []
    for value in rsi:
        result.append(None if np.isnan(value) else float(value))
    return result


def calculate_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> list[dict[str, float | None]]:
    if not closes:
        return []

    prices: np.ndarray = np.asarray(closes, dtype=float)
    length: int = prices.shape[0]

    if fast <= 0 or slow <= 0 or signal <= 0:
        return [{"macd": None, "signal": None, "histogram": None} for _ in range(length)]

    fast_ema: np.ndarray = _ema_array(prices, fast)
    slow_ema: np.ndarray = _ema_array(prices, slow)

    macd_line: np.ndarray = fast_ema - slow_ema
    macd_line[np.isnan(fast_ema) | np.isnan(slow_ema)] = np.nan

    signal_line: np.ndarray = np.full(length, np.nan, dtype=float)
    valid_macd_indices: np.ndarray = np.where(~np.isnan(macd_line))[0]
    if valid_macd_indices.size >= signal:
        start: int = int(valid_macd_indices[0])
        signal_segment: np.ndarray = _ema_array(macd_line[start:], signal)
        signal_line[start:] = signal_segment

    histogram: np.ndarray = macd_line - signal_line

    output: list[dict[str, float | None]] = []
    for idx in range(length):
        macd_value: float | None = None if np.isnan(macd_line[idx]) else float(macd_line[idx])
        signal_value: float | None = None if np.isnan(signal_line[idx]) else float(signal_line[idx])
        histogram_value: float | None = None if np.isnan(histogram[idx]) else float(histogram[idx])
        output.append(
            {
                "macd": macd_value,
                "signal": signal_value,
                "histogram": histogram_value,
            }
        )

    return output


def calculate_bollinger_bands(
    closes: list[float],
    period: int = 20,
    num_std: float = 2.0,
) -> list[dict[str, float | None]]:
    if not closes:
        return []

    prices: np.ndarray = np.asarray(closes, dtype=float)
    length: int = prices.shape[0]

    upper: np.ndarray = np.full(length, np.nan, dtype=float)
    middle: np.ndarray = np.full(length, np.nan, dtype=float)
    lower: np.ndarray = np.full(length, np.nan, dtype=float)

    if period > 0 and length >= period:
        windows: np.ndarray = sliding_window_view(prices, window_shape=period)
        means: np.ndarray = np.mean(windows, axis=1)
        stds: np.ndarray = np.std(windows, axis=1, ddof=0)

        start_idx: int = period - 1
        middle[start_idx:] = means
        upper[start_idx:] = means + (num_std * stds)
        lower[start_idx:] = means - (num_std * stds)

    output: list[dict[str, float | None]] = []
    for idx in range(length):
        output.append(
            {
                "upper": None if np.isnan(upper[idx]) else float(upper[idx]),
                "middle": None if np.isnan(middle[idx]) else float(middle[idx]),
                "lower": None if np.isnan(lower[idx]) else float(lower[idx]),
            }
        )

    return output
