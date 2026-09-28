"""Writes spec.json. Kept as Python so the shared ticker/window wording lives in one place."""

import json
from pathlib import Path

TICKERS = {
    "SPY": "SPY, the S&P 500 ETF, 'the index', 'the market' as a single instrument",
    "NVDA": "NVDA, Nvidia",
    "AMD": "AMD, Advanced Micro Devices",
    "AAPL": "AAPL, Apple",
    "MSFT": "MSFT, Microsoft",
    "TSLA": "TSLA, Tesla",
}
WINDOW = {
    "question": "How far back does the user want to look?",
    "stated": "Does the user say how far back to look, such as today, this week, a month, or a quarter?",
    "options": {
        "1d": "today, the current session, the last day",
        "1w": "this week, the past week, the last five sessions",
        "1mo": "the past month, the last thirty days, about four weeks",
        "3mo": "the past three months, this quarter, the longest history available",
    },
}


def symbol(question: str) -> dict:
    return {
        "question": question,
        "stated": "Does the user name a specific stock, ETF, or company, either by name or by ticker symbol such as SPY or NVDA?",
        "options": TICKERS,
        "other": "a company, ticker, or instrument that is not one of the six listed",
    }


SPEC = {
    "route": "What is the user asking the trading assistant to do?",
    "functions": {
        "list_symbols": {
            "description": "say which tickers or instruments the assistant covers",
            "arguments": {},
        },
        "market_summary": {
            "description": "give an overview of how the whole market or all tickers did, a board of prices and returns",
            "arguments": {"window": WINDOW},
        },
        "plot_price": {
            "description": "draw a price chart of one stock, as a line or candles, optionally with volume or a moving average",
            "arguments": {
                "symbol": symbol("Which stock does the user want charted?"),
                "style": {
                    "question": "Does the user want a plain line or candles?",
                    "stated": "Does the user say how the chart should be drawn, such as a line, candles, or OHLC bars?",
                    "options": {
                        "line": "a simple line through the closing prices",
                        "candles": "a candlestick or OHLC chart, showing each bar's open, high, low and close",
                    },
                },
                "resolution": {
                    "question": "How long should each bar on the chart be?",
                    "stated": "Does the user say how long each bar or candle should be, such as one minute, hourly, or daily?",
                    "options": {
                        "1m": "one-minute bars, the finest detail",
                        "5m": "five-minute bars",
                        "15m": "fifteen-minute bars",
                        "1h": "hourly bars",
                        "1d": "daily bars, one per session",
                    },
                },
                "window": WINDOW,
                "include_volume": {
                    "question": "Does the user ask to see trading volume on the chart?"
                },
                "moving_average": {
                    "question": "How many bars should the moving average cover - nine, twenty, or fifty?",
                    "stated": "Does the user ask for a moving average or a smoothed line over the candles?",
                    "options": {
                        "9": "a nine-bar moving average, a fast one",
                        "20": "a twenty-bar moving average",
                        "50": "a fifty-bar moving average, a slow one",
                    },
                },
                "log_scale": {
                    "question": "Does the user ask for a logarithmic price axis?"
                },
            },
        },
        "intraday_pattern": {
            "description": "show how one stock behaves by time of day, which hours are busiest, most volatile, or strongest",
            "arguments": {
                "symbol": symbol("Which stock's time-of-day pattern does the user want?"),
                "window": WINDOW,
                "metric": {
                    "question": "What should be measured across the trading day?",
                    "stated": "Does the user say what to measure across the day, such as how much it trades, how far it swings, or which way it moves?",
                    "options": {
                        "volume": "how much it trades, activity, shares changing hands",
                        "range": "how far price swings within each slot, choppiness",
                        "return": "which direction price tends to move in each slot, gains or losses",
                    },
                },
            },
        },
        "compare_returns": {
            "description": "compare the performance of several stocks against each other on one chart",
            "arguments": {
                "symbols": {"question": "Does the user want {} in the comparison?"},
                "window": WINDOW,
                "normalize": {
                    "question": "Does the user ask for raw, unadjusted prices instead of performance rebased to a common start?"
                },
            },
        },
        "rolling_correlation": {
            "description": "show how closely one stock moves together with another over time, whether they track each other",
            "arguments": {
                "symbol": symbol("Which stock is the one being measured, the one named first?"),
                "benchmark": {
                    "question": "Which stock is the second one named, the yardstick the first is measured against?",
                    "stated": "Does the user name a second stock or an index to measure the first one against?",
                    "options": TICKERS,
                    "other": "a company, ticker, or instrument that is not one of the six listed",
                },
                "window": WINDOW,
                "resolution": {
                    "question": "How long should each bar be when measuring co-movement?",
                    "stated": "Does the user say what bar length to use, such as five minutes, hourly, or daily?",
                    "options": {
                        "5m": "five-minute bars",
                        "15m": "fifteen-minute bars",
                        "1h": "hourly bars",
                        "1d": "daily bars",
                    },
                },
            },
        },
        "summary_stats": {
            "description": "report headline numbers for one stock in text: last price, return, high, low, volume",
            "arguments": {
                "symbol": symbol("Which stock does the user want the numbers for?"),
                "window": WINDOW,
            },
        },
        "volatility": {
            "description": "report how volatile or risky one stock has been, how much it swings",
            "arguments": {
                "symbol": symbol("Which stock's volatility does the user want?"),
                "window": WINDOW,
                "annualized": {
                    "question": "Does the user ask for the figure scaled to a yearly, annualized number?"
                },
            },
        },
        "top_movers": {
            "description": "rank the tickers by how much they rose or fell, the biggest gainers or losers",
            "arguments": {
                "window": WINDOW,
                "direction": {
                    "question": "Is the user interested in the stocks that rose or the ones that fell?",
                    "options": {
                        "gainers": "the winners, what went up, or movers in general with no direction given",
                        "losers": "the losers, what went down, the worst performers",
                    },
                },
            },
        },
        "drawdown": {
            "description": "report the worst peak-to-trough fall of one stock, how far it dropped from its high",
            "arguments": {
                "symbol": symbol("Which stock's drawdown does the user want?"),
                "window": WINDOW,
                "plot": {"question": "Does the user ask for a chart or picture of the drawdown?"},
            },
        },
    },
}

Path(__file__).with_name("spec.json").write_text(json.dumps(SPEC, indent=2))
