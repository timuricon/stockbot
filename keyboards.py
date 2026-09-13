from telegram import InlineKeyboardButton, InlineKeyboardMarkup


MAIN_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("📊 Обзор рынка",       callback_data="market"),
        InlineKeyboardButton("🔍 Unified скан",      callback_data="unified_scan"),
    ],
    [
        InlineKeyboardButton("🚀 Superstock скан",   callback_data="superstock"),
        InlineKeyboardButton("📋 Universe тикеров",  callback_data="universe"),
    ],
    [
        InlineKeyboardButton("⚡ ET-only скан",       callback_data="et_only_scan"),
        InlineKeyboardButton("📈 Оценка сигналов",   callback_data="evaluate_signals"),
    ],
    [
        InlineKeyboardButton("ℹ️ Справка",           callback_data="help"),
    ],
    [
        InlineKeyboardButton("💼 Портфель B — Ребалансировка", callback_data="portfolio_b"),
    ],
    [
        InlineKeyboardButton("📋 Текущий состав портфеля B",   callback_data="pb_view"),
        InlineKeyboardButton("✏️ Изменить состав",             callback_data="pb_edit"),
    ],
])

BACK_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("🔙 Главное меню", callback_data="back")]
])

UNIFIED_RESULT_KEYBOARD = InlineKeyboardMarkup([
    [InlineKeyboardButton("📈 Топ-10 близких к прохождению", callback_data="unified_near_misses")],
    [InlineKeyboardButton("🔙 Главное меню", callback_data="back")],
])

UNIVERSE_KEYBOARD = InlineKeyboardMarkup([
    [
        InlineKeyboardButton("➕ Добавить тикер",   callback_data="universe_add"),
        InlineKeyboardButton("➖ Удалить тикер",    callback_data="universe_remove"),
    ],
    [
        InlineKeyboardButton("🔄 Пересобрать из Polygon", callback_data="universe_rebuild"),
    ],
    [InlineKeyboardButton("🔙 Главное меню", callback_data="back")],
])


def build_replace_keyboard(
    problem_tickers: list[dict],
    extra_buttons: list = None,
) -> InlineKeyboardMarkup:
    """Клавиатура с кнопками замены проблемных тикеров."""
    buttons = [
        [InlineKeyboardButton(
            f"🔄 Заменить {p['ticker']} "
            f"({'низкий yield' if p['reason'] == 'low_yield' else 'нет выплат'})",
            callback_data=f"pb_replace__{p['ticker']}__{p['type']}"
        )]
        for p in problem_tickers
    ]
    if extra_buttons:
        buttons.extend(extra_buttons)
    buttons += [
        [InlineKeyboardButton("📊 Детали по позициям", callback_data="pb_details")],
        [InlineKeyboardButton("🔙 Главное меню",       callback_data="back")],
    ]
    return InlineKeyboardMarkup(buttons)


def build_approval_keyboard(holdings: list) -> InlineKeyboardMarkup:
    """Клавиатура экрана согласования состава портфеля."""
    buttons = []
    for h in holdings:
        ticker = h["ticker"]
        htype  = h.get("type", "")
        label  = f"✅ {ticker} (нажми чтобы снять)" if h.get("approved") else f"⬜ {ticker}"
        buttons.append([
            InlineKeyboardButton(label, callback_data=f"pb_approve__{ticker}"),
            InlineKeyboardButton("🔄 Заменить", callback_data=f"pb_replace_approval__{ticker}__{htype}"),
        ])

    all_approved = all(h.get("approved", False) for h in holdings)
    if all_approved:
        buttons.append([
            InlineKeyboardButton("🚀 Всё одобрено — запустить портфель!", callback_data="pb_approval_done")
        ])
    else:
        pending = sum(1 for h in holdings if not h.get("approved", False))
        buttons.append([
            InlineKeyboardButton(f"⬜ Осталось одобрить: {pending}", callback_data="pb_noop")
        ])

    buttons.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back")])
    return InlineKeyboardMarkup(buttons)


def build_alternatives_keyboard(
    old_ticker: str, htype: str, alts: list[dict],
) -> InlineKeyboardMarkup:
    """Клавиатура с альтернативными тикерами для замены."""
    buttons = [
        [InlineKeyboardButton(
            f"{a['ticker']} — {a['name']} | ~{a['yield_pct']}%",
            callback_data=f"pb_confirm_replace__{old_ticker}__{a['ticker']}"
        )]
        for a in alts
    ]
    buttons.append([
        InlineKeyboardButton("🔍 Искать другой тикер",
                             callback_data=f"pb_search_start__{old_ticker}__{htype}")
    ])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data="pb_reshow_approval")])
    return InlineKeyboardMarkup(buttons)


def build_confirm_replace_keyboard(
    old_ticker: str, new_ticker: str, htype: str,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"✅ Да, заменить {old_ticker} → {new_ticker}",
            callback_data=f"pb_confirm_replace__{old_ticker}__{new_ticker}"
        )],
        [InlineKeyboardButton("🔍 Искать другой",
                              callback_data=f"pb_search_start__{old_ticker}__{htype}")],
        [InlineKeyboardButton("❌ Отмена", callback_data="pb_reshow_approval")],
    ])


def build_superstock_keyboard(has_cache: bool = False, has_history: bool = False) -> InlineKeyboardMarkup:
    buttons = []
    if has_cache:
        buttons.append([InlineKeyboardButton("🔄 Запустить новый скан", callback_data="superstock_run")])
    else:
        buttons.append([InlineKeyboardButton("🚀 Запустить скан", callback_data="superstock_run")])
    if has_history:
        buttons.append([InlineKeyboardButton("📋 Кандидаты прошлого скана", callback_data="superstock_history")])
    buttons.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back")])
    return InlineKeyboardMarkup(buttons)


def build_unified_keyboard(has_cache: bool = False, has_near_misses: bool = False) -> InlineKeyboardMarkup:
    if has_cache:
        buttons = [
            [InlineKeyboardButton("🔄 Запустить новый скан", callback_data="unified_run")],
        ]
        if has_near_misses:
            buttons.append([
                InlineKeyboardButton("📈 Топ-10 близких к прохождению", callback_data="unified_near_misses")
            ])
        buttons.append([InlineKeyboardButton("🔙 Главное меню", callback_data="back")])
        return InlineKeyboardMarkup(buttons)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Запустить скан", callback_data="unified_run")],
        [InlineKeyboardButton("🔙 Главное меню",   callback_data="back")],
    ])


def build_cancel_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    """Клавиатура ожидания скана: отмена (unified_cancel/superstock_cancel/
    et_only_cancel) + главное меню."""
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Отменить скан", callback_data=callback_data)],
        [InlineKeyboardButton("🔙 Главное меню",  callback_data="back")],
    ])
