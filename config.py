import os

# ── API ───────────────────────────────────────────────────────────────────────
# ВАЖНО: ключи берутся ТОЛЬКО из переменных окружения (см. trading_bot.service).
# Не хранить ключи в коде — они утекают в git-историю и логи.
POLYGON_API_KEY   = os.getenv("POLYGON_API_KEY", "")
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_BOT_TOKEN", "")
OWNER_ID          = 331661651
BASE_URL          = "https://api.polygon.io"

# ── Files ─────────────────────────────────────────────────────────────────────
TICKER_CACHE_FILE = "tickers_cache.json"
PORTFOLIO_B_FILE  = "portfolio_b.json"

# ── Cache TTL ─────────────────────────────────────────────────────────────────
CACHE_TTL_MIN       = 60
TICKERS_CACHE_HOURS = 168   # 7 дней — reference меняется редко

# ── Scan parameters ───────────────────────────────────────────────────────────
TOP_N_BY_VOLUME  = 140
MAX_PICKS        = 25
MAX_REJECTS      = 50
MIN_UPSIDE_PCT   = 25.0
MIN_PRICE        = 5.0
EMA_PERIOD       = 30
WEEKLY_BARS      = 104
SEMAPHORE_LIMIT  = 1

# ── Momentum модуль unified скана (паттерны 1D+1W) ────────────────────────────
# Бывшие GROWTH_* параметры: используются scanners/patterns.py и data_provider/bars.py
GROWTH_VOL_PERIOD     = 10
GROWTH_WEEKLY_BARS    = 52
GROWTH_MIN_VOL_RATIO  = 1.3

# ── Superstock scan ───────────────────────────────────────────────────────────
SS_TOP_N = 1000

# ── Exchanges / types ─────────────────────────────────────────────────────────
ALLOWED_EXCHANGES = {"XNYS", "XNAS", "ARCX", "XASE"}
ALLOWED_TYPES     = {"CS", "ETF", "ETV"}
EXCH_MAP          = {"XNYS": "NYSE", "XNAS": "NASDAQ", "ARCX": "NYSE Arca", "XASE": "NYSE American"}

# ── Yield thresholds by type (Portfolio B) ────────────────────────────────────
YIELD_THRESHOLDS_DEFAULT = {
    "BDC": 7.0,
    "CEF": 6.0,
    "HY_BOND": 5.0,
    "REIT": 4.5,
    "DIVIDEND": 3.0,
}

# ── Excluded tickers ──────────────────────────────────────────────────────────
EXCLUDED_TICKERS = {
    "SPY","IVV","VOO","SPLG","RSP","QQQ","QQQM","ONEQ","DIA",
    "IWM","IWF","IWD","IWB","VTI","ITOT","SCHB","MDY","IJH","IVOO",
    "IJR","VB","SCHA","VUG","VTV","SCHG",
    "XLK","XLF","XLE","XLV","XLI","XLP","XLY","XLU","XLB","XLRE","XLC",
    "VGT","VFH","VDE","VHT","VIS","SMH","SOXX","IBB","XBI","KRE","KBE",
    "ITB","XHB",
    "EFA","IEFA","VEA","SCHF","EEM","IEMG","VWO","SCHE",
    "EWY","EWJ","EWZ","EWG","EWU","FXI","MCHI","ACWI","VT",
    "GLD","IAU","SGOL","SLV","SIVR","GDX","GDXJ","USO","UCO","BNO","DBC","PDBC",
    "BND","BNDX","BIV","BSV","BLV","VCIT","VCSH","VCLT",
    "LQD","HYG","JNK","USHY","SHYG","EMB","MBB","MUB",
    "TLT","IEF","SHY","GOVT","TIPS","STIP","SCHZ","AGG",
    "SGOV","USFR","TFLO","BIL","SHV","ICSH",
    "PFF","PFFD",
    "VXX","UVXY","SVXY",
    "AGQ","USLV","DSLV","UGLD","DGLD",
    "SPXL","SPXS","UPRO","SPXU","SDS","SSO",
    "TQQQ","SQQQ","QLD","QID","TNA","TZA","URTY","SRTY",
    "LABD","LABU","SOXL","SOXS","FAS","FAZ","DPST","FNGU","FNGD",
    "NAIL","DRN","DRV","BOIL","KOLD","UCO","SCO",
    "GLDM","GLDX","RING","SLV","SIVR","PSLV","PPLT","PALL",
    "IQMM",
}

# ── Superstock small/mid-cap extra watchlist ──────────────────────────────────
SS_SMALLCAP_EXTRA = [
    "AAOI","NBIS","COHR","IREN","RKLB","KTOS","ONDS","AVAV","LUNR","RDW",
    "SPIR","ASTS","POET","AEVA","OUST","LAZR","LIDR","VUZI","MVIS","FORM",
    "MTSI","IIVI","ACMR","ONTO","ICHR","KLIC","NOVT","UCTT","CAMT","LSCC",
    "MCHP","SWKS","QRVO","SITM","PSEM","ALAB","TSEM","PDFS","PLAB","DIOD",
    "RXRX","BEAM","CRSP","NTLA","EDIT","TGTX","IMVT","AXSM","VERA","RDVT",
    "PRME","SNDX","KYMR","ARDX","ALLO","ABOS","ARQT","FULC","NRIX","PMVP",
    "ACVA","CGEM","DICE","ETNB","FATE","FGEN","HOOK","IKNA","JANX","KROS",
    "LNTH","MGTX","NUVL","ORIC","PGNY","RAPT","RCUS","RVNC","SAGE","SEER",
    "STRO","TARS","VRTX","VYGR","XNCR","YMAB","ZNTL","PRAX","PTGX","AGIO",
    "ALEC","ALGS","AMRC","ANAB","APGE","ARMO","ASMB","ATEX","BBIO","BCAB",
    "LOAR","CW","FTAI","HALO","CACI","MOGA","HEICO","ACHR","JOBY","EVTL",
    "MNTS","VORB","SPCE","RKLB","ATRO","BWXT","DRS","ESLT","HII","KTOS",
    "LHX","MOOG","NOC","PLTR","RCAT","RPVT","SARCOS","SFLY","SKYW","SWBI",
    "TDG","TXT","VSEC","VVX","WKSP","WWD","AXON","DFEN","AVAV","UAVS",
    "MARA","RIOT","HUT","BITF","CLSK","CIFR","WULF","GREE","CRCL","IREN",
    "BTBT","CORZ","MSTR","COIN","HOOD","SQ","AFRM","UPST","LC","DAVE",
    "SOFI","OPEN","OPFI","MQ","PAYC","WEX","PAYO","FLYW","RELY","RPAY",
    "PLUG","BE","BLNK","CHPT","EVGO","NKLA","HYZN","FCEL","BLDP","HYLN",
    "CEG","VST","NRG","SMR","OKLO","NNE","UUUU","DNN","UEC","CCJ",
    "ENPH","SEDG","ARRY","FSLR","NOVA","RUN","SPWR","MAXN","CSIQ","JKS",
    "NARI","INSP","TMDX","SWAV","IRTC","ATRC","GKOS","NVCR","HIMS","LNTH",
    "AMR","ARCH","CEIX","HCC","METC","ARLP","SM","FANG","OVV","CIVI",
    "MTDR","ESTE","BATL","TALO","SBOW","GRNT","VTLE","CRGY","MGY","CPE",
    "NOG","PTEN","NR","PUMP","WTTR","NINE","KLXE","RNGR","BKR","HAL",
    "SLB","FTI","NCSM","LBRT","USWS","FTIV","DTOL","BORR","SDRL","RIG",
    "FRSH","GTLB","DDOG","ZS","CRWD","PANW","S","QLYS","TENB","VRNS",
    "CYBR","SAIL","RBRK","INTA","BRZE","PCVX","CFLT","MDB","ESTC","NEBL",
    "SMAR","NCNO","TOST","RELY","SEMR","APPF","HUBS","PCOR","JAMF","DOCU",
    # Из ручных примеров точек входа (early trend паттерны)
    "AHRT","ARGX","GEO","WERN",
]

# ── Ticker alternatives for Portfolio B ───────────────────────────────────────
TICKER_ALTERNATIVES = {
    "BDC": [
        {"ticker": "ARCC", "name": "Ares Capital",         "yield_pct": 9.5},
        {"ticker": "MAIN", "name": "Main Street Capital",  "yield_pct": 7.2},
        {"ticker": "OBDC", "name": "Blue Owl Capital",     "yield_pct": 10.8},
        {"ticker": "GBDC", "name": "Golub Capital BDC",    "yield_pct": 10.1},
        {"ticker": "HTGC", "name": "Hercules Capital",     "yield_pct": 10.4},
        {"ticker": "TPVG", "name": "TriplePoint Venture",  "yield_pct": 11.2},
    ],
    "CEF": [
        {"ticker": "PDI",  "name": "PIMCO Dynamic Income", "yield_pct": 13.0},
        {"ticker": "UTF",  "name": "Cohen & Steers Infra", "yield_pct": 7.5},
        {"ticker": "ETV",  "name": "Eaton Vance Tax-Mgd",  "yield_pct": 8.2},
        {"ticker": "GOF",  "name": "Guggenheim Str Opp",   "yield_pct": 14.1},
        {"ticker": "JEPI", "name": "JPM Equity Premium",   "yield_pct": 7.4},
        {"ticker": "JEPQ", "name": "JPM Nasdaq Eq Prem",   "yield_pct": 9.3},
    ],
    "HY_BOND": [
        {"ticker": "HYG",  "name": "iShares HY Corp Bond", "yield_pct": 6.2},
        {"ticker": "BKLN", "name": "Invesco Senior Loan",  "yield_pct": 8.5},
        {"ticker": "JNK",  "name": "SPDR Blmbg HY Bond",   "yield_pct": 6.4},
        {"ticker": "USHY", "name": "iShares Broad USD HY", "yield_pct": 6.8},
        {"ticker": "FALN", "name": "iShares Fallen Angels","yield_pct": 6.1},
    ],
    "REIT": [
        {"ticker": "O",    "name": "Realty Income",        "yield_pct": 5.8},
        {"ticker": "VNQ",  "name": "Vanguard RE ETF",      "yield_pct": 4.2},
        {"ticker": "MPW",  "name": "Medical Properties",   "yield_pct": 10.0},
        {"ticker": "STAG", "name": "STAG Industrial",      "yield_pct": 4.1},
        {"ticker": "AGNC", "name": "AGNC Investment",      "yield_pct": 14.5},
        {"ticker": "NNN",  "name": "NNN REIT",             "yield_pct": 5.3},
    ],
    "DIVIDEND": [
        {"ticker": "SCHD", "name": "Schwab US Dividend",   "yield_pct": 3.7},
        {"ticker": "VIG",  "name": "Vanguard Div Apprec",  "yield_pct": 1.8},
        {"ticker": "NOBL", "name": "ProShares Aristocrats","yield_pct": 2.1},
        {"ticker": "DVY",  "name": "iShares Div Index",    "yield_pct": 4.5},
        {"ticker": "HDV",  "name": "iShares HY Div ETF",   "yield_pct": 3.9},
    ],
}

# ── Pattern scores (Momentum: скоринг паттернов, scanners/patterns.py) ────────
GROWTH_PATTERN_SCORES = {
    "Cup & Handle":    3,
    "Range Breakout":  3,
    "Double Bottom":   2,
    "Flag":            2,
    "Volume Spike":    1,
}
