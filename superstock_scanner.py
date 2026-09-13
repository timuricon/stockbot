import time
from datetime import datetime, timedelta
from typing import Optional

from core.config import (
    EMA_PERIOD, MIN_PRICE, SS_TOP_N, SS_SMALLCAP_EXTRA,
    EXCLUDED_TICKERS, EXCH_MAP,
)
from core.logging_setup import logger
from core.utils import calc_ema
from data_provider.bars import get_weekly_bars
from data_provider.cache import load_ticker_reference
from data_provider.polygon_client import get_grouped_daily
from data_provider.yfinance_client import (
    get_current_price, get_full_info,
    get_quarterly_financials, get_quarterly_income_stmt, YF_AVAILABLE,
)
from scanners.filters import prefilter_superstock


def _interruptible_pause(cancel_event, seconds: float) -> None:
    """Пауза, прерываемая сигналом отмены: при set() возвращаемся сразу."""
    if cancel_event is not None:
        cancel_event.wait(seconds)
    else:
        time.sleep(seconds)


def get_fundamental_ss(ticker: str) -> Optional[dict]:
    """Фундаментальные данные через yfinance."""
    if not YF_AVAILABLE:
        return None
    try:
        import yfinance as yf
        t    = yf.Ticker(ticker)
        info = t.info or {}

        result = {
            "float_shares":    info.get("floatShares"),
            "price_to_sales":  info.get("priceToSalesTrailing12Months"),
            "peg_ratio":       info.get("pegRatio"),
            "gross_margins":   info.get("grossMargins"),
            "debt_to_equity":  info.get("debtToEquity"),
            "insider_pct":     info.get("heldPercentInsiders"),
            "avg_volume":      info.get("averageVolume"),
            "consensus_tp":    info.get("targetMedianPrice") or info.get("targetMeanPrice"),
            "sector":          info.get("sector", ""),
            "short_name":      info.get("shortName", ticker),
            "revenue_growth":  info.get("revenueGrowth"),
            "52w_high":        info.get("fiftyTwoWeekHigh"),
            "rev_yoy_pct":     None,
            "rev_qoq_pct":     None,
            "eps_acceleration": False,
            "eps_quarters_up":  0,
        }

        # Квартальная выручка
        try:
            qf = t.quarterly_financials
            if qf is not None and not qf.empty:
                rev_row = None
                for key in ("Total Revenue", "Revenue"):
                    if key in qf.index:
                        rev_row = qf.loc[key].dropna().sort_index()
                        break
                if rev_row is not None and len(rev_row) >= 5:
                    latest = float(rev_row.iloc[-1])
                    yoy_q  = float(rev_row.iloc[-5])
                    prev_q = float(rev_row.iloc[-2])
                    if yoy_q != 0:
                        result["rev_yoy_pct"] = round((latest - yoy_q) / abs(yoy_q) * 100, 1)
                    if prev_q != 0:
                        result["rev_qoq_pct"] = round((latest - prev_q) / abs(prev_q) * 100, 1)
                elif rev_row is not None and len(rev_row) >= 2:
                    if result["revenue_growth"]:
                        result["rev_yoy_pct"] = round(result["revenue_growth"] * 100, 1)
        except Exception:
            if result.get("revenue_growth"):
                result["rev_yoy_pct"] = round(result["revenue_growth"] * 100, 1)

        # EPS ускорение
        try:
            stmt = t.quarterly_income_stmt
            if stmt is not None and not stmt.empty:
                eps_row = None
                for key in ("Net Income", "Basic EPS", "Diluted EPS"):
                    if key in stmt.index:
                        eps_row = stmt.loc[key].dropna().sort_index()
                        break
                if eps_row is not None and len(eps_row) >= 3:
                    diffs = eps_row.diff().dropna()
                    count = 0
                    for v in reversed(diffs.values):
                        if v > 0:
                            count += 1
                        else:
                            break
                    result["eps_quarters_up"]  = count
                    result["eps_acceleration"] = (count >= 2)
        except Exception:
            pass

        return result
    except Exception as e:
        logger.debug(f"get_fundamental_ss failed for {ticker}: {e}")
        return None


def score_superstock(
    price: float, ema30_val: float, vol_chg: Optional[float],
    fund: dict, high52w: Optional[float],
) -> tuple[int, dict]:
    """Скоринг 0-100."""
    scores = {
        "fundamental": 0,
        "valuation":   0,
        "structure":   0,
        "quality":     0,
        "technical":   0,
        "catalyst":    0,
    }

    rev_yoy = fund.get("rev_yoy_pct", 0) or 0
    if rev_yoy >= 100:  scores["fundamental"] += 25
    elif rev_yoy >= 50: scores["fundamental"] += 20
    elif rev_yoy >= 25: scores["fundamental"] += 12

    eps_q = fund.get("eps_quarters_up", 0)
    if eps_q >= 3:
        scores["fundamental"] = min(25, scores["fundamental"] + 5)

    psr = fund.get("price_to_sales")
    peg = fund.get("peg_ratio")
    if psr is not None:
        if psr < 1.5:   scores["valuation"] += 8
        elif psr < 3:   scores["valuation"] += 5
        elif psr < 5:   scores["valuation"] += 2
    if peg is not None and peg > 0:
        if peg < 1.0:   scores["valuation"] += 7
        elif peg < 1.5: scores["valuation"] += 4
        elif peg < 2.0: scores["valuation"] += 2

    float_s = fund.get("float_shares")
    if float_s is not None:
        float_m = float_s / 1_000_000
        if float_m < 10:   scores["structure"] += 12
        elif float_m < 30: scores["structure"] += 9
        elif float_m < 50: scores["structure"] += 6
        else:              scores["structure"] += 3

    insider_pct = fund.get("insider_pct")
    if insider_pct is not None:
        if insider_pct > 0.15:   scores["structure"] += 8
        elif insider_pct > 0.1:  scores["structure"] += 6
        elif insider_pct > 0.05: scores["structure"] += 4
        elif insider_pct > 0.01: scores["structure"] += 2

    gm = fund.get("gross_margins")
    if gm is not None:
        if gm > 0.70:   scores["quality"] += 12
        elif gm > 0.50: scores["quality"] += 9
        elif gm > 0.40: scores["quality"] += 6
        elif gm > 0.25: scores["quality"] += 3

    priority = {"Technology", "Healthcare", "Industrials", "Energy", "Financial Services"}
    if fund.get("sector", "") in priority:
        scores["quality"] += 8

    scores["technical"] += 5  # цена > EMA — уже проверено

    if vol_chg is not None:
        if vol_chg >= 100:  scores["technical"] += 5
        elif vol_chg >= 50: scores["technical"] += 4
        elif vol_chg >= 20: scores["technical"] += 2

    if high52w and high52w > 0:
        pct_from_high = (high52w - price) / high52w * 100
        if 5 <= pct_from_high <= 25:  scores["technical"] += 5
        elif pct_from_high < 5:       scores["technical"] += 3

    cons = fund.get("consensus_tp")
    if cons and float(cons) > price:
        upside_cons = (float(cons) - price) / price * 100
        if upside_cons > 50:    scores["catalyst"] += 5
        elif upside_cons > 30:  scores["catalyst"] += 3
        elif upside_cons > 15:  scores["catalyst"] += 1

    return min(sum(scores.values()), 100), scores


def analyze_superstock_ticker(ticker_info: dict, reference: dict, cancel_event=None) -> Optional[dict]:
    """Полный анализ одного тикера для superstock скана."""
    ticker = ticker_info["ticker"]

    if ticker in EXCLUDED_TICKERS:
        return None

    ref = reference.get(ticker)
    if not ref:
        return None

    prefilter_reason = prefilter_superstock(ticker, ticker_info)
    if prefilter_reason:
        logger.debug(f"[SS pre] ❌ {ticker}: {prefilter_reason}")
        return None

    df = get_weekly_bars(ticker, cancel_event=cancel_event)
    if df is None or len(df) < 20:
        return None

    current_price = get_current_price(ticker)
    if not current_price or current_price <= 0:
        current_price = ticker_info.get("price") or 0
    if current_price < MIN_PRICE:
        return None

    closed_bars = df.iloc[:-1] if len(df) > 20 else df
    ema30       = calc_ema(closed_bars["Close"], EMA_PERIOD)
    ema30_val   = float(ema30.iloc[-1])

    if current_price <= ema30_val:
        return None

    ema_pct = (current_price - ema30_val) / ema30_val * 100
    if ema_pct > 35:
        return None

    vol_chg = None
    if len(closed_bars) >= 11:
        last_vol = float(closed_bars["Volume"].iloc[-1])
        avg_vol  = float(closed_bars["Volume"].iloc[-11:-1].mean())
        if avg_vol > 0:
            vol_chg = round((last_vol - avg_vol) / avg_vol * 100, 1)

    fund = get_fundamental_ss(ticker)
    if fund is None:
        return None

    rev_yoy = fund.get("rev_yoy_pct")
    if rev_yoy is None or rev_yoy < 25:
        return None

    float_s = fund.get("float_shares")
    if float_s is not None and (float_s / 1_000_000) > 100:
        return None

    if not fund.get("eps_acceleration"):
        return None

    avg_vol = fund.get("avg_volume") or 0
    if avg_vol > 0 and avg_vol < 100_000:
        return None

    d_to_e = fund.get("debt_to_equity")
    if d_to_e is not None and d_to_e > 300:
        return None

    consensus_tp = fund.get("consensus_tp")
    if consensus_tp and float(consensus_tp) > 0:
        cons_val = float(consensus_tp)
        if current_price >= cons_val:
            return None
        upside_to_consensus = (cons_val - current_price) / current_price * 100
        if upside_to_consensus < 25:
            logger.debug(
                f"[SS] {ticker}: consensus upside {upside_to_consensus:.1f}% < 25%"
            )
            return None

    high52w = fund.get("52w_high")
    score_total, score_detail = score_superstock(
        current_price, ema30_val, vol_chg, fund, high52w
    )

    if score_total < 55:
        return None

    if ema_pct <= 8:    ema_zone = "A"
    elif ema_pct <= 20: ema_zone = "B"
    else:               ema_zone = "C"

    if score_total >= 85:   category = "🚀 Суперсток-кандидат"
    elif score_total >= 70: category = "⭐ Сильный сетап"
    else:                   category = "👀 На радар"

    exch_label = EXCH_MAP.get(ref.get("exchange", ""), "")
    float_m    = (float_s / 1_000_000) if float_s else None

    return {
        "ticker":            ticker,
        "price":             round(current_price, 2),
        "ema30_val":         round(ema30_val, 2),
        "ema_pct":           round(ema_pct, 1),
        "ema_zone":          ema_zone,
        "volume_change_pct": vol_chg,
        "score":             score_total,
        "score_detail":      score_detail,
        "category":          category,
        "float_m":           float_m,
        "rev_yoy_pct":       fund.get("rev_yoy_pct"),
        "rev_qoq_pct":       fund.get("rev_qoq_pct"),
        "eps_quarters_up":   fund.get("eps_quarters_up", 0),
        "psr":               fund.get("price_to_sales"),
        "peg":               fund.get("peg_ratio"),
        "gross_margin":      fund.get("gross_margins"),
        "insider_pct":       fund.get("insider_pct"),
        "consensus_tp":      round(float(consensus_tp), 2) if consensus_tp else None,
        "pct_from_52w_high": round((high52w - current_price) / high52w * 100, 1) if high52w else None,
        "sector":            fund.get("sector", ""),
        "exchange":          exch_label,
        "short_name":        fund.get("short_name", ticker),
    }


def run_superstock_scan(reference: dict, cancel_event=None) -> list[dict]:
    """
    Запускает полный superstock скан.
    cancel_event — threading.Event; set() прерывает скан после текущего тикера.
    """
    logger.info(f"[SS] Fetching grouped daily (SS_TOP_N={SS_TOP_N})...")
    raw = get_grouped_daily(cancel_event=cancel_event)
    if not raw:
        return []

    all_tickers = []
    for t in raw:
        ticker = t.get("T", "")
        if not ticker or ticker not in reference:
            continue
        price  = t.get("c", 0) or 0
        volume = t.get("v", 0) or 0
        if price < 1:
            continue
        all_tickers.append({
            "ticker":        ticker,
            "price":         round(price, 2),
            "dollar_volume": price * volume,
        })

    all_tickers.sort(key=lambda x: x["dollar_volume"], reverse=True)

    seen = set()
    scan_list = []
    for t in all_tickers[:SS_TOP_N]:
        seen.add(t["ticker"])
        scan_list.append(t)

    extras_added = 0
    for extra_ticker in SS_SMALLCAP_EXTRA:
        if extra_ticker not in seen and extra_ticker in reference:
            scan_list.append({"ticker": extra_ticker, "price": 0, "dollar_volume": 0})
            seen.add(extra_ticker)
            extras_added += 1

    logger.info(f"[SS] {len(scan_list)} tickers to scan ({extras_added} extras)")

    results = []
    prefiltered = 0
    polygon_cnt = 0

    for i, tkr_info in enumerate(scan_list):
        if cancel_event is not None and cancel_event.is_set():
            logger.info(f"[SS] Cancelled at {i}/{len(scan_list)}")
            break

        ticker = tkr_info["ticker"]
        try:
            pre = prefilter_superstock(ticker, tkr_info)
            if pre:
                prefiltered += 1
                logger.debug(f"[SS {i+1}/{len(scan_list)}] ⏩ {ticker}: {pre}")
                _interruptible_pause(cancel_event, 0.2)
                continue

            polygon_cnt += 1
            result = analyze_superstock_ticker(tkr_info, reference, cancel_event)
            if result:
                results.append(result)
                logger.info(
                    f"[SS {i+1}/{len(scan_list)}] ✅ {ticker} "
                    f"Балл={result['score']} Rev={result.get('rev_yoy_pct')}%"
                )
            else:
                logger.debug(f"[SS {i+1}/{len(scan_list)}] ❌ {ticker}")
        except Exception as e:
            logger.warning(f"[SS {i+1}/{len(scan_list)}] Error {ticker}: {e}")

        _interruptible_pause(cancel_event, 2.0)

    logger.info(
        f"[SS] Done: {len(scan_list)} tickers | "
        f"prefiltered={prefiltered} | polygon={polygon_cnt} | found={len(results)}"
    )
    results.sort(key=lambda x: x["score"], reverse=True)
    return results
