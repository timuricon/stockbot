from datetime import datetime
from typing import Optional


# ── EMA zone helpers ──────────────────────────────────────────────────────────

def ema_zone_label(zone: str) -> str:
    return {
        "A": "🟢 У поддержки",
        "B": "🟡 Умеренный перегрев",
        "C": "🟠 Перегрет — ждать отката",
        "D": "🔴 Экстремальный перегрев",
    }.get(zone, "")


def vol_badge(vol_pct) -> str:
    if vol_pct is None:
        return ""
    if vol_pct >= 50:
        return f"🔥 Активность +{vol_pct:.0f}%"
    elif vol_pct > 0:
        return f"📈 Активность +{vol_pct:.0f}%"
    else:
        return f"📉 Активность {vol_pct:.0f}%"


# ── Base scan formatting ──────────────────────────────────────────────────────

def format_one_pick(p: dict, num: int) -> str:
    zone      = p.get("ema_zone", "A")
    ema_pct   = p.get("ema_pct", 0)
    price     = p["price"]
    target    = p["target"]
    upside    = p.get("upside_pct", 0)
    ema_val   = p.get("ema30_val", 0)
    consensus = p.get("consensus_tp")
    vol       = p.get("volume_change_pct")
    ticker    = p["ticker"]
    rationale = p.get("rationale", "")

    zone_label = ema_zone_label(zone)
    vol_str    = vol_badge(vol)
    act_str    = f" | {vol_str}" if vol_str else ""

    if upside > 100:   upside_mark = " ⚡🚀"
    elif upside > 60:  upside_mark = " ⚡"
    else:              upside_mark = ""

    entry_lo = round(ema_val, 2)
    entry_hi = round(ema_val * 1.08, 2)

    if consensus:
        if price >= consensus:
            cons_str = f"⛔ Выше консенсуса (TP ${consensus})"
        elif price >= consensus * 0.90:
            cons_str = f"⚠️ Почти у таргета (TP ${consensus})"
        else:
            cons_str = f"${consensus}"
    else:
        cons_str = "н/д"

    rr      = p.get("rr_ratio")
    stop    = p.get("stop_level")
    rr_str  = f"R/R {rr:.1f}" if rr else ""
    stop_str = f"🛑 Стоп: ${stop}" if stop else ""

    lines = [
        f"<b>{num}. {zone_label} ${ticker}</b>{act_str}{upside_mark}",
        f"   💵 Цена: <b>${price}</b>  →  🎯 Цель: <b>${target}</b> (<b>+{upside}%</b>)",
        f"   {stop_str}  |  📐 {rr_str}",
        f"   📊 EMA статус: Зона {zone} — {ema_pct:.1f}% над 30W EMA",
        f"   📍 Зона входа: ${entry_lo} — ${entry_hi}",
        f"   ⚡ Консенсус TP: {cons_str}",
        f"   📋 {rationale}",
    ]
    if zone == "C":
        lines.insert(5, f"   🔁 Ждать отката к: ${entry_lo} — ${entry_hi}")
    return "\n".join(lines)


def format_picks(
    picks: list[dict],
    scan_time: datetime,
    show_header: bool = True,
    start_num: int = 1,
) -> str:
    if not picks:
        return (
            "❌ <b>Ни один тикер не прошёл все фильтры.</b>\n\n"
            "Возможные причины:\n"
            "• Рынок в коррекции (большинство акций ниже 30W EMA)\n"
            "• Ограниченный апсайд до уровней сопротивления\n"
            "• Попробуй запустить скан завтра"
        )

    age_min = int((datetime.now() - scan_time).total_seconds() / 60)
    lines   = []
    if show_header:
        lines += [
            f"🎯 <b>ТОП ТРЕЙДИНГ ПИКИ</b> (лонг, 1–4 недели)\n",
            f"<i>🟢 Зона А (0–8% над EMA) • 🟡 Зона Б (8–20%) • 🟠 Зона В (20–35%)</i>\n",
            f"⏱ Данные от {scan_time.strftime('%d.%m %H:%M')} (кэш {age_min} мин)\n",
            "Фильтры: NYSE/NASDAQ • Цена > 30W EMA • Апсайд ≥ 25% • Зона Г исключена\n",
        ]

    for i, p in enumerate(picks, start_num):
        lines.append(format_one_pick(p, i))
        lines.append("")

    lines.append("⚠️ <i>Не является инвестиционным советом. Всегда используй стоп-лосс. DYOR.</i>")
    return "\n".join(lines)


def format_rejects(rejects: list[dict]) -> str:
    if not rejects:
        return "✅ Все просканированные тикеры прошли фильтры!"

    lines = [f"🔍 <b>ОТСЕЯННЫЕ ТИКЕРЫ</b> (топ {len(rejects)})\n"]
    for r in rejects:
        reason = r["reason_fail"][0] if r.get("reason_fail") else "Неизвестно"
        reason_safe = (reason
                       .replace("&", "&amp;")
                       .replace("<", "&lt;")
                       .replace(">", "&gt;"))
        lines.append(f"❌ <b>{r['ticker']}</b> — {reason_safe}")
    lines.append(f"\n<i>Показано {len(rejects)} тикеров</i>")
    return "\n".join(lines)


def format_radar_block(picks_radar: list[dict]) -> str:
    """Блок «На радар» — Зона В."""
    lines = [
        "🟠 <b>НА РАДАР — Зона В (перегрет, ждать отката)</b>\n",
        "<i>Акции выше EMA на 20–35%. Не входить сейчас — ждать отката к зоне входа.</i>\n",
    ]
    for p in picks_radar:
        ema_val  = p.get("ema30_val", 0)
        entry_lo = round(ema_val, 2)
        entry_hi = round(ema_val * 1.08, 2)
        cons     = p.get("consensus_tp")
        cons_str = f"${cons}" if cons else "н/д"
        lines.append(
            f"🟠 <b>${p['ticker']}</b> | Цена ${p['price']} | "
            f"+{p.get('ema_pct',0):.1f}% над EMA\n"
            f"   📍 Ждать отката к: ${entry_lo} — ${entry_hi}\n"
            f"   ⚡ Консенсус TP: {cons_str} | Апсайд: +{p.get('upside_pct',0)}%"
        )
    return "\n".join(lines)


# ── Superstock formatting ─────────────────────────────────────────────────────

def format_superstock_pick(p: dict, num: int) -> str:
    ticker   = p["ticker"]
    price    = p["price"]
    score    = p["score"]
    category = p["category"]
    sd       = p.get("score_detail", {})

    zone_labels = {"A": "🟢 У поддержки", "B": "🟡 Умер. перегрев", "C": "🟠 Перегрет"}
    zone_label  = zone_labels.get(p.get("ema_zone", "B"), "")

    vol     = p.get("volume_change_pct")
    vol_str = ""
    if vol is not None:
        vol_str = f" | 🔥 Объём +{vol:.0f}%" if vol >= 50 else (
                  f" | 📈 Объём +{vol:.0f}%" if vol > 0 else "")

    rev_yoy = p.get("rev_yoy_pct")
    rev_qoq = p.get("rev_qoq_pct")
    rev_str = f"+{rev_yoy:.0f}% YoY" if rev_yoy else "н/д"
    if rev_qoq:
        rev_str += f" / +{rev_qoq:.0f}% QoQ" if rev_qoq > 0 else f" / {rev_qoq:.0f}% QoQ"

    eps_q   = p.get("eps_quarters_up", 0)
    psr     = p.get("psr")
    peg     = p.get("peg")
    psr_str = f"PSR {psr:.1f}" if psr else "PSR н/д"
    peg_str = f"PEG {peg:.1f}" if peg and peg > 0 else "PEG н/д"

    gm      = p.get("gross_margin")
    gm_str  = f"Маржа {gm*100:.0f}%" if gm else ""

    float_m    = p.get("float_m")
    float_warn = " ⚠️ риск манипуляции" if float_m and float_m < 10 else ""
    float_str  = f"{float_m:.1f}M{float_warn}" if float_m else "н/д"

    ins     = p.get("insider_pct")
    ins_str = f"{ins*100:.1f}%" if ins else "н/д"

    cons = p.get("consensus_tp")
    if cons:
        upside_cons = round((cons - price) / price * 100, 1)
        cons_str = f"${cons} (+{upside_cons}%)"
    else:
        cons_str = "н/д"

    pct_high = p.get("pct_from_52w_high")
    high_str = f"{pct_high:.1f}% от 52W High" if pct_high else ""

    score_str = (
        f"Ф:{sd.get('fundamental',0)} О:{sd.get('valuation',0)} "
        f"С:{sd.get('structure',0)} К:{sd.get('quality',0)} "
        f"Т:{sd.get('technical',0)} Кат:{sd.get('catalyst',0)}"
    )

    return "\n".join([
        f"<b>{num}. {category} ${ticker}</b>{vol_str}",
        f"   💵 <b>${price}</b> | {zone_label} ({p.get('ema_pct',0):.1f}% над EMA)",
        f"   🏆 Балл: <b>{score}/100</b> [{score_str}]",
        f"   📊 Выручка: {rev_str} | EPS: ✅ {eps_q} кв. ускорения",
        f"   💰 {psr_str} | {peg_str} | {gm_str}",
        f"   🏗 Float: {float_str} | Инсайдеры: {ins_str}",
        f"   ⚡ Консенсус TP: {cons_str}",
        f"   📍 {p.get('exchange','')} • {p.get('sector','')} | {high_str}",
    ])


# ── Unified scan formatting ───────────────────────────────────────────────────

def format_unified_pick(p: dict, num: int) -> str:
    """Форматирует один результат unified скана."""
    ticker      = p["ticker"]
    price       = p["price"]
    score       = p["score"]
    n_modules   = p["n_modules"]
    combo_label = p.get("combo_label", "")

    swing = p.get("swing")
    mom   = p.get("momentum")
    et    = p.get("early_trend")

    # Заголовок с combo-меткой
    if n_modules == 3:
        header_icon = "⚡"
        combo_str   = f"[ВСЕ 3]"
    elif n_modules == 2:
        header_icon = "🔥"
        combo_str   = f"[{combo_label}]"
    else:
        header_icon = "✅"
        combo_str   = f"[{combo_label}]" if combo_label else ""

    # Объём — берём из swing или momentum
    vol_str = ""
    if swing and swing.get("volume_change_pct") is not None:
        v = swing["volume_change_pct"]
        vol_str = f" | 🔥 Объём +{v:.0f}%" if v >= 50 else (f" | 📈 +{v:.0f}%" if v > 0 else "")
    elif mom and mom.get("vol_ratio_1d") is not None:
        v = mom["vol_ratio_1d"]
        vol_str = f" | 📊 Vol ×{v:.1f}" if v >= 1.5 else ""

    lines = [
        f"{header_icon} <b>{num}. {combo_str} ${ticker}</b>{vol_str}",
        f"   💵 <b>${price}</b> | Балл: <b>{score}/100</b>"
        + (" 🔻<i>порог снижен</i>" if p.get("threshold_lowered") else ""),
    ]

    # Swing блок
    if swing:
        zone  = swing.get("ema_zone", "?")
        zone_icons = {"A": "🟢", "B": "🟡", "C": "🟠"}
        z_icon = zone_icons.get(zone, "⚪")
        lines.append(
            f"   📊 Swing: {z_icon} Зона {zone} | "
            f"Апсайд +{swing.get('upside_pct', 0)}% | "
            f"R/R {swing.get('rr_ratio', 0):.1f} | "
            f"Стоп ${swing.get('stop_level', 0)} → Цель ${swing.get('target', 0)}"
        )

    # Early Trend блок
    if et:
        et_patterns = ", ".join(et.get("patterns", [])) or "—"
        stack_icon  = "✅" if et.get("full_stack") else "⚠️"
        rsi_str = f" | RSI {et['rsi']:.0f}" if et.get("rsi") else ""
        lines.append(
            f"   ⚡ ET: EMA-стек {stack_icon} | {et_patterns}{rsi_str}"
        )

    # Momentum блок
    if mom:
        combo  = mom.get("combo", [])
        p1d    = [x for x in mom.get("patterns_1d", []) if x not in combo]
        p1w    = [x for x in mom.get("patterns_1w", []) if x not in combo]
        parts  = []
        if combo: parts.append(f"COMBO: {', '.join(combo)}")
        if p1d:   parts.append(f"1D: {', '.join(p1d)}")
        if p1w:   parts.append(f"1W: {', '.join(p1w)}")
        mom_str = " | ".join(parts) if parts else "—"
        lines.append(f"   🌱 Momentum: {mom_str}")

    # Сектор/биржа
    if swing:
        info_parts = []
        if swing.get("exchange"): info_parts.append(swing["exchange"])
        if swing.get("sector"):   info_parts.append(swing["sector"])
        if swing.get("consensus_tp"):
            info_parts.append(f"Консенсус TP ${swing['consensus_tp']}")
        if info_parts:
            lines.append(f"   📍 {' • '.join(info_parts)}")

    return "\n".join(lines)


def _missing_modules_note(nm: dict) -> str:
    """
    Определяет какие модули не сработали и почему — для near_miss диагностики.
    Возвращает короткую строку типа "не хватило: Swing (below_ema), Momentum".
    """
    missing = []

    swing = nm.get("swing")
    mom   = nm.get("momentum")
    et    = nm.get("early_trend")

    if swing is None:
        missing.append("Swing")
    if mom is None:
        missing.append("Momentum")
    if et is None:
        missing.append("ET")

    if not missing:
        return ""
    return f"   🔻 Не хватило: {', '.join(missing)}"


def format_near_miss(nm: dict, num: int) -> str:
    """Форматирует один near_miss для топ-10."""
    ticker      = str(nm.get("ticker", "—"))
    price       = float(nm.get("price", 0))
    score       = int(nm.get("score", 0))
    n_modules   = int(nm.get("n_modules", 0))
    active      = nm.get("active_modules", []) or []

    # Иконка по количеству модулей
    if n_modules == 3:
        icon = "🔥"
    elif n_modules == 2:
        icon = "📈"
    else:
        icon = "⚪"

    combo_str = f"[{'+'.join(active)}]" if active else ""

    lines = [
        f"{icon} <b>{num}. {combo_str} ${ticker}</b>",
        f"   💵 <b>${price:.2f}</b> | Балл: <b>{score}/100</b>",
    ]

    missing_note = _missing_modules_note(nm)
    if missing_note:
        lines.append(missing_note)

    # Кратко показать swing, если есть
    swing = nm.get("swing")
    if swing and isinstance(swing, dict):
        zone  = swing.get("ema_zone", "?")
        zone_icons = {"A": "🟢", "B": "🟡", "C": "🟠"}
        z_icon = zone_icons.get(zone, "⚪")
        upside = float(swing.get("upside_pct", 0))
        lines.append(f"   📊 Swing: {z_icon} Зона {zone} | Апсайд +{upside:.0f}%")

    # Momentum кратко
    mom = nm.get("momentum")
    if mom and isinstance(mom, dict):
        combo = mom.get("combo", []) or []
        if combo:
            combo_str_mom = ", ".join([str(x) for x in combo])
            lines.append(f"   🌱 Momentum: COMBO {combo_str_mom}")

    # ET кратко
    et = nm.get("early_trend")
    if et and isinstance(et, dict):
        patterns = et.get("patterns", []) or []
        if patterns:
            patterns_str = ", ".join([str(x) for x in patterns])
            lines.append(f"   ⚡ ET: {patterns_str}")

    return "\n".join(lines)


# ── Market overview ───────────────────────────────────────────────────────────

def format_market_overview(
    indices_data: dict,
    uvxy_data: Optional[dict],
) -> str:
    from datetime import datetime
    lines = ["📊 <b>MARKET OVERVIEW</b>",
             f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}\n"]

    for name, info in indices_data.items():
        if info:
            chg   = info["chg_pct"]
            price = info["price"]
            arrow = "🟢" if chg >= 0 else "🔴"
            lines.append(f"{arrow} <b>{name}</b>: ${price:,.2f} ({chg:+.2f}%)")
        else:
            lines.append(f"⚪ {name}: нет данных")

    lines.append("")
    if uvxy_data:
        chg = uvxy_data["chg_pct"]
        if chg > 5:   mood = "🚨 Повышенный страх — осторожно"
        elif chg > 0: mood = "😬 Небольшое напряжение"
        else:         mood = "😌 Рынок спокоен"
        lines.append(f"🌡 <b>Волатильность</b> (UVXY {chg:+.1f}%): {mood}")

    import datetime as _dt
    month = _dt.datetime.now().month
    lines.append("\n📅 <b>Ключевые события:</b>")
    if month in (1, 4, 7, 10):
        lines.append("• 📢 <b>Сезон отчётности</b> — ожидай повышенной волатильности")
    if month in (1, 3, 5, 6, 7, 9, 11, 12):
        lines.append("• 🏦 <b>Возможно заседание FOMC</b> в этом месяце")
    lines.append(
        "• 📌 Точные даты: "
        "<a href='https://www.federalreserve.gov/monetarypolicy/fomccalendar.htm'>Fed Calendar</a>"
    )

    return "\n".join(lines)


# ── Portfolio approval screen text ────────────────────────────────────────────

def format_approval_screen(holdings: list) -> str:
    TYPE_EMOJI = {"BDC": "🟣", "CEF": "🔴", "HY_BOND": "🟡", "REIT": "🔵", "DIVIDEND": "🟢"}
    lines = [
        "💼 <b>Портфель B — согласование состава</b>",
        "",
        "Проверьте тикеры перед первым запуском.",
        "Если тикер не устраивает — нажмите 🔄 рядом с ним.\n",
    ]
    for h in holdings:
        emoji  = TYPE_EMOJI.get(h.get("type", ""), "⚪")
        status = "✅" if h.get("approved", False) else "⬜"
        lines.append(
            f"{status} {emoji} <b>{h['ticker']}</b> [{h.get('type','')}] "
            f"— {h.get('name','')} | yield ~{h.get('current_yield_pct',0)}%"
        )
    return "\n".join(lines)


# ── ET-only formatting ────────────────────────────────────────────────────────

def format_et_only_pick(p: dict, num: int) -> str:
    """Форматирует один результат ET-only скана."""
    ticker    = p["ticker"]
    price     = p["price"]
    et_score  = p["et_score"]
    patterns  = p.get("patterns", [])
    full_stack = p.get("full_stack", False)
    rsi       = p.get("rsi")
    pct_high  = p.get("pct_from_52w_high")
    emas      = p.get("emas", {})
    swing     = p.get("swing")

    stack_icon = "✅" if full_stack else "⚠️"
    patterns_str = ", ".join(patterns) if patterns else "—"

    rsi_str  = f" | RSI {rsi:.0f}" if rsi else ""
    high_str = f" | {pct_high:.1f}% от 52W High" if pct_high else ""

    lines = [
        f"⚡ <b>{num}. ${ticker}</b> | ET балл: <b>{et_score}/30</b>",
        f"   💵 <b>${price}</b>{rsi_str}{high_str}",
        f"   📐 {stack_icon} EMA-стек | {patterns_str}",
    ]

    # EMA значения
    if emas:
        lines.append(
            f"   📊 EMA10: ${emas.get('ema10', 0):.2f} | "
            f"EMA50: ${emas.get('ema50', 0):.2f} | "
            f"EMA200: ${emas.get('ema200', 0):.2f}"
        )

    # Swing контекст если прошёл
    if swing:
        zone_icons = {"A": "🟢", "B": "🟡", "C": "🟠"}
        z_icon = zone_icons.get(swing.get("ema_zone", ""), "⚪")
        lines.append(
            f"   {z_icon} Swing: Зона {swing.get('ema_zone', '?')} | "
            f"Апсайд +{swing.get('upside_pct', 0)}% | "
            f"R/R {swing.get('rr_ratio', 0):.1f}"
        )
    else:
        lines.append("   ⚪ Swing: не прошёл фильтры (только ET сигнал)")

    return "\n".join(lines)
