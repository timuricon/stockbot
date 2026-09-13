from datetime import datetime
from typing import Optional

from core.config import TICKER_ALTERNATIVES
from core.logging_setup import logger
from data_provider.yfinance_client import get_dividend_yield, get_full_info, YF_AVAILABLE


def fetch_yield(ticker: str) -> Optional[float]:
    """Wrapper для получения yield."""
    return get_dividend_yield(ticker)


def analyze_portfolio(data: dict) -> tuple[str, list[dict]]:
    """
    Основная функция ребалансировки Портфеля B.
    Возвращает (text_report, problem_tickers).
    """
    holdings   = data.get("holdings", [])
    total_inv  = data.get("total_invested", 0)
    tolerance  = data.get("rebalance_tolerance_pct", 3.0)
    tgt_yield  = data.get("target_annual_yield_pct", 8.0)
    thresholds = data.get("yield_thresholds", {})
    updated    = data.get("last_updated", "—")
    currency   = data.get("currency", "USD")

    if not holdings or total_inv <= 0:
        return "❌ Файл портфеля пуст или поле total_invested = 0.", []

    current_total = sum(h.get("current_value", 0) for h in holdings)
    if current_total <= 0:
        return "❌ Все current_value = 0. Обновите данные перед ребалансировкой.", []

    lines = []
    lines.append("💼 <b>ПОРТФЕЛЬ B — РЕБАЛАНСИРОВКА</b>")
    lines.append(f"📅 Данные от: <b>{updated}</b>")
    lines.append(f"💵 Текущая стоимость: <b>{current_total:,.0f} {currency}</b>")
    lines.append(f"🎯 Целевой yield: <b>{tgt_yield}%</b>  |  Допуск: ±{tolerance}%\n")

    actions_needed  = []
    yield_alerts    = []
    problem_tickers = []
    weighted_yield  = 0.0

    for h in holdings:
        ticker       = h.get("ticker", "?")
        target_pct   = h.get("target_pct", 0)
        current_val  = h.get("current_value", 0)
        htype        = h.get("type", "")
        payout_ok    = h.get("payout_ok", True)
        manual_yield = h.get("current_yield_pct", 0)

        live_yield   = fetch_yield(ticker)
        actual_yield = live_yield if (live_yield and live_yield > 0) else manual_yield
        yield_src    = "live" if (live_yield and live_yield > 0) else "manual"

        current_pct    = (current_val / current_total) * 100
        diff_pct       = current_pct - target_pct
        weighted_yield += (current_pct / 100) * actual_yield

        min_yield = thresholds.get(htype, 4.0)
        if actual_yield < min_yield:
            yield_alerts.append(
                f"⚠️ <b>{ticker}</b>: yield {actual_yield:.1f}% ниже порога "
                f"{min_yield}% для {htype}"
            )
            if htype in TICKER_ALTERNATIVES:
                problem_tickers.append({"ticker": ticker, "type": htype, "reason": "low_yield"})

        if not payout_ok:
            yield_alerts.append(
                f"🚨 <b>{ticker}</b>: <b>payout_ok = false</b> — были пропуски выплат"
            )
            if htype in TICKER_ALTERNATIVES and not any(p["ticker"] == ticker for p in problem_tickers):
                problem_tickers.append({"ticker": ticker, "type": htype, "reason": "payout_fail"})

        if abs(diff_pct) >= tolerance:
            target_val = (target_pct / 100) * current_total
            delta_val  = target_val - current_val
            action     = "📈 Докупить" if delta_val > 0 else "📉 Продать"
            actions_needed.append({
                "ticker": ticker, "action": action,
                "delta_val": delta_val, "current_pct": current_pct,
                "target_pct": target_pct, "actual_yield": actual_yield,
                "yield_src": yield_src,
            })

    yield_emoji = "✅" if weighted_yield >= tgt_yield else "🔴"
    lines.append(f"{yield_emoji} <b>Взвешенный yield: {weighted_yield:.1f}%</b>")
    if weighted_yield < tgt_yield:
        lines.append(f"   ↳ Не достигает цели {tgt_yield}% — нужно усилить высокодоходные позиции\n")
    else:
        lines.append("   ↳ Цель достигнута ✓\n")

    if actions_needed:
        lines.append(f"⚖️ <b>Требуется ребалансировка ({len(actions_needed)} позиций):</b>")
        for a in sorted(actions_needed, key=lambda x: abs(x["delta_val"]), reverse=True):
            sign = "+" if a["delta_val"] > 0 else ""
            lines.append(
                f"  {a['action']} <b>{a['ticker']}</b>: "
                f"{sign}{a['delta_val']:,.0f} {currency} "
                f"({a['current_pct']:.1f}% → {a['target_pct']}%) "
                f"| yield {a['actual_yield']:.1f}% [{a['yield_src']}]"
            )
        lines.append("")
    else:
        lines.append(
            f"✅ <b>Ребалансировка не нужна</b> — все в допуске ±{tolerance:.0f}%\n"
        )

    if yield_alerts:
        lines.append("🔔 <b>Алерты по выплатам:</b>")
        lines.extend(yield_alerts)
        lines.append("")

    lines.append("<i>⚠️ Не является инвестиционным советом. DYOR.</i>")
    return "\n".join(lines), problem_tickers


def format_portfolio_details(data: dict) -> str:
    """Детальный вид каждой позиции портфеля."""
    holdings      = data.get("holdings", [])
    currency      = data.get("currency", "USD")
    current_total = sum(h.get("current_value", 0) for h in holdings)

    TYPE_EMOJI = {
        "BDC": "🟣", "CEF": "🔴", "HY_BOND": "🟡",
        "REIT": "🔵", "DIVIDEND": "🟢",
    }

    lines = ["📋 <b>ДЕТАЛИ ПОРТФЕЛЯ B</b>\n"]
    for h in holdings:
        ticker     = h.get("ticker", "?")
        htype      = h.get("type", "")
        cur_val    = h.get("current_value", 0)
        target_pct = h.get("target_pct", 0)
        cur_pct    = (cur_val / current_total * 100) if current_total > 0 else 0
        yld        = h.get("current_yield_pct", 0)
        payout     = "✅" if h.get("payout_ok", True) else "🚨"
        notes      = h.get("notes", "")
        emoji      = TYPE_EMOJI.get(htype, "⚪")

        lines.append(
            f"{emoji} <b>{ticker}</b> [{htype}] {payout}\n"
            f"   💵 {cur_val:,.0f} {currency} | {cur_pct:.1f}% / цель {target_pct}%"
            f" | yield {yld:.1f}%\n"
            f"   <i>{notes}</i>\n"
        )

    return "\n".join(lines)
