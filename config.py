"""
config.py — アプリ全体の設定値。ここを変えるだけで挙動を調整できます。
"""
import os


def get_secret(name: str, default: str = "") -> str:
    """APIキー等を取得する。優先順位: st.secrets → os.getenv → 空。
    Streamlit Cloud では st.secrets、ローカルでは .env / 環境変数を使う。"""
    # 1. st.secrets（Streamlit Cloud）
    try:
        import streamlit as st
        if name in st.secrets:
            v = st.secrets[name]
            if v:
                return str(v).strip()
    except Exception:
        pass
    # 2. 環境変数（.env / OS）
    v = os.getenv(name)
    if v and v.strip():
        return v.strip()
    # 3. 空
    return default


APP_TITLE = "中期投資 判断コックピット v2（教育・分析用）"
# v14.5: スマホ向けの短縮タイトル
APP_TITLE_SHORT = "投資コックピット"
APP_SUBTITLE = "今日の判断"
APP_VERSION = "v14.5"

DEFAULT_WATCHLIST = ["NVDA", "AMD", "AVGO", "PLTR", "VST", "GEV", "CEG", "XOM", "CCJ", "MRVL"]

# ----- スコア配点（合計100） -----
SCORE_WEIGHTS = {"fundamental": 25, "technical": 25, "news": 25, "market": 10, "supply_demand": 15}

# ----- 判定しきい値 -----
BUY_THRESHOLD = 70
WATCH_THRESHOLD = 52

# ----- リスク管理ルール -----
MAX_RISK_PER_TRADE_PCT = 2.0
MAX_POSITION_PCT = 20.0
DEFAULT_STOP_PCT = 8.0
# 決算前・FOMC前に減らす割合（%）
REDUCE_BEFORE_EARNINGS_PCT = 50
REDUCE_BEFORE_FOMC_PCT = 30
# 「近い」と判断する日数
EARNINGS_SOON_DAYS = 10
FOMC_SOON_DAYS = 3

# ----- 色 -----
COLORS = {"BUY": "#16a34a", "WATCH": "#eab308", "AVOID": "#dc2626", "bg_card": "#0f172a"}

# 6種類のアクション → 色・絵文字
ACTIONS = {
    "今すぐ買い":     {"color": "#16a34a", "emoji": "🟢"},
    "押し目待ち":     {"color": "#0ea5e9", "emoji": "🔵"},
    "決算後まで待ち": {"color": "#f59e0b", "emoji": "🟠"},
    "ニュース待ち":   {"color": "#a855f7", "emoji": "🟣"},
    "買わない":       {"color": "#94a3b8", "emoji": "⚪"},
    "危険":           {"color": "#dc2626", "emoji": "🔴"},
}

# ----- マクロのスナップショット（手入力。最新値が出たら更新） -----
MACRO_SNAPSHOT = {
    "fed_funds_rate": "3.50% - 3.75%",
    "last_cpi_yoy": "4.2%",
    "last_unemployment": "4.3%",
    "next_fomc": "2026-06-17",   # FOMC結果発表日（残り日数の計算に使用）
    "last_jobs_note": "雇用は底堅い（失業率 約4.3%）",
    "updated_note": "※マクロの一部は手入力です。最新値はご自身で更新してください。",
}

MACRO_TICKERS = {
    "S&P500": "^GSPC", "NASDAQ": "^IXIC", "VIX (恐怖指数)": "^VIX",
    "米10年債利回り": "^TNX", "原油 (WTI)": "CL=F",
}

# ----- 重要イベント（手入力カレンダー） -----
# type: macro / earnings。banner=画面に出す説明
EVENTS = [
    {"date": "2026-06-17", "title": "FOMC（政策金利発表）", "type": "macro",
     "note": "金利・声明で相場全体が大きく動く。前日はサイズを落とす。"},
    {"date": "2026-06-24", "title": "Micron(MU) 決算", "type": "earnings",
     "note": "半導体センチメントの先行指標。NVDA/AVGO/MRVL/AMDに波及。"},
    {"date": "2026-07-11", "title": "6月CPI（消費者物価）", "type": "macro",
     "note": "インフレ次第で利下げ期待が変動。高PER株に影響大。"},
    {"date": "2026-07-24", "title": "ExxonMobil(XOM) 決算", "type": "earnings", "note": ""},
    {"date": "2026-08-06", "title": "Vistra(VST) 決算", "type": "earnings", "note": ""},
]

# ----- セクター対応（デフォルト銘柄） -----
TICKER_SECTOR = {
    "NVDA": "半導体", "AMD": "半導体", "AVGO": "半導体", "MRVL": "半導体",
    "PLTR": "AIソフト/防衛", "VST": "電力/エネルギー", "GEV": "電力/エネルギー",
    "CEG": "電力/エネルギー", "CCJ": "原子力/資源", "XOM": "石油/エネルギー",
}

# 相場モード別の有利/不利セクター・推奨比率
REGIME_PLAYBOOK = {
    "強気": {"stance": "攻め", "new_buy_pct": 70, "cash_pct": 30,
            "good": ["半導体", "AIソフト/防衛", "グロース"],
            "bad": ["ディフェンシブ", "公益"]},
    "中立": {"stance": "やや守り", "new_buy_pct": 50, "cash_pct": 50,
            "good": ["電力/エネルギー", "石油/エネルギー", "原子力/資源", "バリュー"],
            "bad": ["高PERグロース", "投機株"]},
    "弱気": {"stance": "守り", "new_buy_pct": 30, "cash_pct": 70,
            "good": ["石油/エネルギー", "ディフェンシブ", "公益"],
            "bad": ["半導体", "高PERグロース", "小型株"]},
    "危険": {"stance": "退避", "new_buy_pct": 10, "cash_pct": 90,
            "good": ["現金", "石油/エネルギー(ヘッジ)"],
            "bad": ["ほぼ全て（特に高ベータ）"]},
}

PRICE_PERIOD = "1y"
PRICE_INTERVAL = "1d"
CACHE_TTL_SECONDS = 900

# ----- 保存ファイル -----
SETTINGS_PATH = "user_data/settings.json"

# ============================================================
#  v3 追加設定（ポジション管理・テーマ分類・アラート・保存先）
# ============================================================

# ----- 保存ファイル（v3） -----
POSITIONS_PATH = "user_data/positions.json"
TRADES_PATH = "user_data/trades.json"
PORTFOLIO_PATH = "user_data/portfolio.json"  # 現金など

# ----- 銘柄→テーマタグ（既知マッピング） -----
THEME_MAP = {
    "NVDA": ["AI", "半導体", "高PERグロース"],
    "AMD":  ["AI", "半導体"],
    "AVGO": ["AI", "半導体", "インフラ"],
    "PLTR": ["AI", "防衛", "ソフトウェア", "高PERグロース"],
    "VST":  ["AI電力", "公益", "データセンター"],
    "GEV":  ["AI電力", "発電設備"],
    "CEG":  ["原子力", "AI電力"],
    "XOM":  ["エネルギー", "原油ヘッジ"],
    "CCJ":  ["ウラン", "原子力"],
    "MRVL": ["AI", "半導体", "通信半導体"],
}

# テーマ → 集計グループ（ポートフォリオ比率用）。タグがどれかに該当すれば計上。
THEME_GROUPS = {
    "AI関連":           ["AI", "AI電力", "AI半導体"],
    "半導体":           ["半導体", "AI半導体", "通信半導体"],
    "エネルギー":       ["エネルギー", "原油ヘッジ", "AI電力", "公益", "原子力", "ウラン", "発電設備"],
    "防衛/地政学ヘッジ": ["防衛", "原油ヘッジ", "地政学"],
    "高PERグロース":    ["高PERグロース"],
}

# ----- ポートフォリオ危険判定のしきい値 -----
RISK_LIMITS = {
    "single_name_pct": 25.0,     # 1銘柄が総資産の25%以上→集中警告
    "ai_semi_pct": 60.0,         # AI+半導体が60%以上→テーマ集中警告
    "earnings_soon_count": 2,    # 決算2週間以内の銘柄がこの数以上→イベント警告
    "earnings_soon_days": 14,
    "min_cash_pct": 10.0,        # 現金比率がこの未満→逃げ場なし警告
}

# ----- アラート用：イベント前の日数しきい値 -----
ALERT_EARNINGS_DAYS = [14, 7]
ALERT_PRICE_NEAR_PCT = 3.0       # 損切り/利確に対し3%以内で「接近」アラート

# ===================== v3 追加設定 =====================
POSITIONS_PATH = "user_data/positions.json"   # ポジション台帳
HISTORY_PATH = "user_data/history.csv"         # 売買履歴

# ポートフォリオ危険判定のしきい値
CONCENTRATION_PCT = 25.0      # 1銘柄がこの%以上 → 集中警告
THEME_CONCENTRATION_PCT = 60.0  # AI/半導体がこの%以上 → テーマ集中警告
MIN_CASH_PCT = 10.0           # 現金比率がこの%未満 → 逃げ場なし警告
EARNINGS_RISK_DAYS = 14       # この日数以内に決算 → イベントリスク

# 既知ティッカーのテーマ分類（未知はファンダ/ニュースから推測→不明なら「未分類」）
TICKER_THEMES = {
    "NVDA": ["AI", "半導体", "高PERグロース"],
    "AMD":  ["AI", "半導体"],
    "AVGO": ["AI", "半導体", "インフラ"],
    "PLTR": ["AI", "防衛", "ソフトウェア", "高PERグロース"],
    "VST":  ["AI電力", "公益", "データセンター"],
    "GEV":  ["AI電力", "発電設備"],
    "CEG":  ["原子力", "AI電力"],
    "XOM":  ["エネルギー", "原油ヘッジ"],
    "CCJ":  ["ウラン", "原子力"],
    "MRVL": ["AI半導体", "通信半導体"],
    "MSFT": ["AI", "ソフトウェア", "インフラ"],
    "GOOGL": ["AI", "ソフトウェア"],
    "META": ["AI", "ソフトウェア"],
    "TSM":  ["半導体", "ファウンドリ"],
    "LMT":  ["防衛", "地政学ヘッジ"],
    "RTX":  ["防衛", "地政学ヘッジ"],
}

# テーマ → 集計バケツ（ポートフォリオ比率計算用）
THEME_BUCKETS = {
    "AI関連":   ["AI", "AI電力", "AI半導体"],
    "半導体":   ["半導体", "AI半導体", "通信半導体", "ファウンドリ"],
    "エネルギー": ["エネルギー", "原油ヘッジ", "AI電力", "公益", "発電設備", "原子力", "ウラン"],
    "防衛/地政学": ["防衛", "地政学ヘッジ", "原油ヘッジ"],
    "高PERグロース": ["高PERグロース"],
}


# ===================== v4 設定 =====================
# v4ではニュース/AIを第一級サポート。キーが無くても起動はするが、
# 機能制限（キーワード分類）になる旨を画面で強く警告する。
REQUIRE_KEYS = True            # Finnhub + OpenAI を推奨（実質必須）
AI_PROVIDER_ORDER = ["openai", "anthropic"]  # v4はOpenAI優先
OPENAI_MODEL = "gpt-4o-mini"
ANTHROPIC_MODEL = "claude-sonnet-4-6"

# ===================== v5: 銘柄発掘エンジン =====================
# 発掘ユニバース（流動性の高い米国大型・中型＋主要ETF。CSVで拡張可）
DISCOVERY_UNIVERSE = [
    # AI / 半導体
    "NVDA","AMD","AVGO","MRVL","TSM","MU","QCOM","INTC","AMAT","LRCX","KLAC","ASML","ARM","SMCI","ANET","DELL",
    # ソフトウェア / AI / サイバー
    "MSFT","GOOGL","META","AMZN","PLTR","CRM","NOW","SNOW","ORCL","ADBE","PANW","CRWD","ZS","NET","DDOG","FTNT",
    # 電力 / 原子力 / データセンター
    "VST","CEG","GEV","NRG","TLN","NEE","ETR","CCJ","VRT","ETN","PWR","OKLO","SMR",
    # エネルギー
    "XOM","CVX","COP","OXY","SLB","EOG","FANG",
    # 防衛 / 地政学
    "LMT","RTX","NOC","GD","LHX","KTOS","AVAV",
    # ヘルスケア
    "LLY","UNH","ISRG","VRTX","REGN","MRK","ABBV",
    # その他グロース
    "TSLA","UBER","NFLX","AXON","GE","CAT",
    # 主要ETF
    "SPY","QQQ","IWM","SMH","XLK","XLE","ITA","NLR","IGV","XLV",
]
DISCOVERY_UNIVERSE_CSV = "user_data/universe.csv"  # ここにticker列で追記すると拡張される
DISCOVERY_MAX = 50  # 1回のスキャン上限（重さ対策）

# 発掘専用スコア配点（合計100）
DISCOVERY_WEIGHTS = {"technical": 25, "news": 25, "fundamental": 20, "theme": 15, "supply": 10, "risk": 5}
DISCOVERY_BUY_STRONG = 80   # 80以上: 強いBUY候補
DISCOVERY_BUY = 70          # 70-79: BUY候補
DISCOVERY_WATCH = 55        # 55-69: WATCH / 54以下: AVOID

# 最低条件
DISCOVERY_MIN_MCAP = 1_000_000_000   # $1B
DISCOVERY_MIN_PRICE = 5.0            # $5
DISCOVERY_MIN_AVGVOL = 500_000       # 50万株/日

# 今の相場で重視するテーマ（テーマ適合スコア用・0〜1）。相場が変わったら手で調整。
CURRENT_THEMES = {
    "AI": 1.0, "AI半導体": 1.0, "AI電力": 1.0, "半導体": 1.0, "通信半導体": 0.9,
    "電力": 0.9, "データセンター": 0.9, "発電設備": 0.85, "原子力": 0.9, "ウラン": 0.8, "公益": 0.7,
    "防衛": 0.85, "地政学ヘッジ": 0.8, "原油ヘッジ": 0.8, "エネルギー": 0.8,
    "サイバーセキュリティ": 0.8, "ソフトウェア": 0.6, "インフラ": 0.6,
    "ヘルスケア": 0.5, "金利低下メリット": 0.6,
}

# 発掘ユニバース向けのテーマ追加（既存 TICKER_THEMES に統合）
TICKER_THEMES.update({
    "TSM": ["半導体", "ファウンドリ"], "MU": ["半導体", "メモリ", "AI半導体"],
    "QCOM": ["半導体", "通信半導体"], "INTC": ["半導体"], "AMAT": ["半導体", "製造装置"],
    "LRCX": ["半導体", "製造装置"], "KLAC": ["半導体", "製造装置"], "ASML": ["半導体", "製造装置"],
    "ARM": ["半導体", "AI"], "SMCI": ["AI", "データセンター", "インフラ"], "ANET": ["AI", "データセンター", "インフラ"],
    "DELL": ["AI", "インフラ"],
    "MSFT": ["AI", "ソフトウェア", "インフラ"], "GOOGL": ["AI", "ソフトウェア"], "META": ["AI", "ソフトウェア"],
    "AMZN": ["AI", "ソフトウェア", "データセンター"], "CRM": ["ソフトウェア", "AI"], "NOW": ["ソフトウェア", "AI"],
    "SNOW": ["ソフトウェア", "AI", "データセンター"], "ORCL": ["ソフトウェア", "AI", "データセンター"],
    "ADBE": ["ソフトウェア", "AI"], "PANW": ["サイバーセキュリティ"], "CRWD": ["サイバーセキュリティ"],
    "ZS": ["サイバーセキュリティ"], "NET": ["サイバーセキュリティ", "インフラ"], "DDOG": ["ソフトウェア", "AI"],
    "FTNT": ["サイバーセキュリティ"],
    "NRG": ["電力", "公益"], "TLN": ["原子力", "電力", "AI電力"], "NEE": ["電力", "公益", "金利低下メリット"],
    "ETR": ["電力", "公益"], "VRT": ["データセンター", "インフラ", "発電設備"], "ETN": ["発電設備", "電力", "インフラ"],
    "PWR": ["発電設備", "インフラ"], "OKLO": ["原子力", "AI電力"], "SMR": ["原子力", "AI電力"],
    "CVX": ["エネルギー", "原油ヘッジ"], "COP": ["エネルギー", "原油ヘッジ"], "OXY": ["エネルギー", "原油ヘッジ"],
    "SLB": ["エネルギー"], "EOG": ["エネルギー"], "FANG": ["エネルギー"],
    "NOC": ["防衛", "地政学ヘッジ"], "GD": ["防衛", "地政学ヘッジ"], "LHX": ["防衛", "地政学ヘッジ"],
    "KTOS": ["防衛", "地政学ヘッジ"], "AVAV": ["防衛", "地政学ヘッジ"],
    "LLY": ["ヘルスケア"], "UNH": ["ヘルスケア"], "ISRG": ["ヘルスケア"], "VRTX": ["ヘルスケア"],
    "REGN": ["ヘルスケア"], "MRK": ["ヘルスケア"], "ABBV": ["ヘルスケア"],
    "TSLA": ["AI", "EV", "高PERグロース"], "UBER": ["ソフトウェア"], "NFLX": ["ソフトウェア"],
    "AXON": ["防衛", "ソフトウェア"], "GE": ["発電設備", "インフラ"], "CAT": ["インフラ"],
    "SPY": ["指数ETF"], "QQQ": ["指数ETF", "AI"], "IWM": ["小型株ETF"], "SMH": ["半導体", "ETF"],
    "XLK": ["ソフトウェア", "ETF"], "XLE": ["エネルギー", "ETF"], "ITA": ["防衛", "ETF"],
    "NLR": ["原子力", "ETF"], "IGV": ["ソフトウェア", "ETF"], "XLV": ["ヘルスケア", "ETF"],
})

# ===================== v6: ユニバース拡張 =====================
# 注: これらは「発掘ユニバース」用の代表構成リスト（厳密な最新構成ではない）。
#     ネット接続があれば universe.py がWikipedia等から自動取得し、失敗時はこの固定リストにフォールバック。
DOW30 = [
    "AAPL","MSFT","AMZN","NVDA","JPM","V","UNH","JNJ","WMT","PG","HD","CVX","KO","MRK","CSCO",
    "MCD","CRM","IBM","AXP","GS","CAT","BA","AMGN","HON","TRV","NKE","DIS","MMM","VZ","SHW",
]
NASDAQ100 = [
    "NVDA","AAPL","MSFT","AMZN","AVGO","META","GOOGL","GOOG","TSLA","NFLX","COST","AMD","PEP","ADBE",
    "CSCO","TMUS","INTC","QCOM","INTU","AMAT","TXN","AMGN","ISRG","BKNG","HON","VRTX","ADP","REGN",
    "LRCX","MU","PANW","KLAC","SNPS","CDNS","MELI","MAR","ASML","CRWD","ABNB","ORLY","FTNT","ADI",
    "CTAS","NXPI","PYPL","MNST","PCAR","ROP","CPRT","DASH","WDAY","ODFL","ROST","KDP","IDXX","MRVL",
    "FAST","EA","GEHC","CSGP","TEAM","DDOG","VRSK","XEL","BKR","EXC","KHC","CTSH","TTWO","ON","ZS",
    "ANSS","GFS","MDB","DXCM","BIIB","ARM","SMCI","MRNA","WBD","APP","LIN","PLTR","CEG","PAYX","AEP",
]
SP500_EXTRA = [
    "BRK-B","JPM","V","MA","UNH","JNJ","WMT","PG","HD","CVX","XOM","KO","MRK","ABBV","LLY","BAC","WFC",
    "GS","MS","AXP","CAT","BA","GE","HON","RTX","LMT","NOC","GD","UPS","UNP","DE","MMM","DIS","VZ","T",
    "CMCSA","NKE","SBUX","MCD","LOW","TGT","COP","SLB","EOG","OXY","PSX","MPC","NEE","DUK","SO","D","AEP",
    "PEG","SRE","SPG","PLD","AMT","EQIX","CCI","BLK","SCHW","C","PNC","USB","COF","MET","PRU","AIG","TRV",
    "ALL","PGR","CB","CI","CVS","HUM","ELV","ABT","TMO","DHR","BMY","PFE","GILD","MDT","SYK","BSX","ZTS",
    "BDX","VST","GEV","ORCL","ACN","CRM","TXN","QCOM","ANET","PANW","ETN","PWR","CEG","TSM","CCJ","MRVL",
    "AXON","AVGO","NOW","SNOW","CRWD","ADBE","INTU","ISRG","VRTX","REGN","LLY",
]
# 主要ETFの代表構成銘柄
ETF_CONSTITUENTS = {
    "SMH": ["NVDA","TSM","AVGO","AMD","ASML","AMAT","LRCX","KLAC","MU","QCOM","ADI","NXPI","MRVL","TXN",
            "INTC","MCHP","ON","MPWR","GFS","ARM","SNPS","CDNS","TER","ENTG","SWKS","QRVO","COHR"],
    "XLK": ["AAPL","MSFT","NVDA","AVGO","CRM","ORCL","AMD","ACN","ADBE","CSCO","IBM","NOW","INTC","QCOM",
            "TXN","AMAT","PANW","ANET","LRCX","KLAC","ADI","INTU","MU","APH","CDNS","SNPS","ROP","MSI","FTNT","CRWD"],
    "XLE": ["XOM","CVX","COP","EOG","SLB","MPC","PSX","OXY","WMB","KMI","OKE","HES","FANG","DVN","HAL","BKR","VLO","TRGP","CTRA","EQT"],
    "XLU": ["NEE","DUK","SO","D","AEP","SRE","EXC","PEG","XEL","ED","WEC","VST","ETR","AEE","CEG","PCG","FE","ES","EIX","DTE"],
    "XLI": ["GE","CAT","RTX","HON","UNP","BA","DE","LMT","UPS","GEV","ETN","ADP","PH","TT","EMR","NOC","GD","CSX","ITW","MMM"],
    "XLF": ["BRK-B","JPM","V","MA","BAC","WFC","GS","MS","AXP","SPGI","BLK","C","SCHW","PGR","CB","MMC","FI","BX","PNC","USB"],
    "XLV": ["LLY","UNH","JNJ","MRK","ABBV","TMO","ABT","DHR","ISRG","AMGN","PFE","BMY","VRTX","GILD","MDT","CVS","CI","ELV","REGN","BSX"],
    "ARKK": ["TSLA","COIN","ROKU","HOOD","RBLX","PLTR","PATH","DKNG","SHOP","RKLB","TER","CRSP","TWLO","U","NTLA","BEAM","TDOC","DNA"],
    "URA": ["CCJ","CEG","NXE","UEC","DNN","UUUU","OKLO","SMR","LEU","BWXT"],
    "ITA": ["RTX","LMT","BA","GE","NOC","GD","LHX","HWM","TDG","AXON","HII","LDOS","KTOS","AVAV","CW","TXT"],
    "CIBR": ["PANW","CRWD","FTNT","ZS","NET","CSCO","OKTA","S","CYBR","QLYS","TENB","RPD","GEN","AKAM","FFIV","VRNS"],
}

# スキャンモード → 取り込み上限
SCAN_MODES = {
    "軽量(〜150)": 150,
    "標準(〜500)": 500,
    "広範囲(1000+)": 1500,
    "カスタムCSV": 99999,
}
DEFAULT_SCAN_MODE = "軽量(〜150)"
DISCOVERY_PARALLEL = False     # True で並列取得（環境により不安定なため既定はFalse）
DISCOVERY_PARALLEL_WORKERS = 8
SECTOR_ROTATION_TOPN = 6       # セクターローテーション上位表示数

# ===================== v6: ユニバース拡張 / スキャンモード =====================
UNIVERSE_ALLOW_NETWORK = True   # ライブ時は Wikipedia等から指数構成を取得（失敗時はフォールバック）

# スキャンモード → 銘柄数の上限（重さ対策）。0 は上限なし(カスタムCSV)
SCAN_MODES = {
    "軽量モード（80〜150銘柄）": 150,
    "標準モード（300〜500銘柄）": 500,
    "広範囲モード（1000銘柄以上・重い）": 1500,
    "カスタムCSVモード": 0,
}
DEFAULT_SCAN_MODE = "軽量モード（80〜150銘柄）"

def _T(s):  # 文字列→ティッカーリスト
    return [x for x in s.replace("\n", " ").split(" ") if x]

# ---- フォールバック構成銘柄（ライブ取得に失敗したとき使用） ----
DOW30_FALLBACK = _T("""AAPL MSFT AMZN JPM V WMT JNJ PG HD MRK CVX KO CSCO MCD CRM IBM
AXP GS CAT BA HON AMGN VZ NKE DIS MMM TRV UNH NVDA SHW""")

NASDAQ100_FALLBACK = _T("""AAPL MSFT AMZN NVDA AVGO META GOOGL GOOG TSLA COST NFLX AMD PEP ADBE
CSCO TMUS LIN INTC QCOM TXN AMAT INTU AMGN ISRG BKNG HON CMCSA VRTX REGN ADP PANW GILD ADI MU LRCX
MELI PYPL SBUX KLAC SNPS CDNS MAR CTAS ORLY ABNB CRWD MRVL FTNT NXPI ADSK PCAR ROP CPRT MNST PAYX
AEP KDP MCHP DXCM EXC FAST ODFL CSGP EA TTD DDOG TEAM ON GEHC BIIB ZS ANSS WBD ALGN ARM SMCI APP
MDB DASH CEG WDAY XEL TTWO ILMN CCEP""")

SP500_EXTRA_FALLBACK = _T("""JPM V WMT JNJ PG HD MRK CVX KO MCD CRM IBM AXP GS CAT BA DIS VZ NKE MMM
UNH LLY ABBV PFE TMO ABT DHR BMY AMT XOM COP SLB EOG OXY PSX MPC VLO NEE DUK SO D AEP XEL ED WEC PEG
LMT RTX NOC GD LHX HII BAC WFC C MS SCHW BLK SPGI CB PGR MMC AON ICE CME COF USB PNC TFC T LOW TJX
BKNG MAR CMG AZO ROST UPS FDX UNP NSC EMR ETN PH ROK DE GE PWR SHW APD LIN ECL NEM FCX DOW
ORCL ACN NOW ADBE INTU TXN QCOM AMD MU AMAT LRCX KLAC""")

RUSSELL1000_EXTRA_FALLBACK = _T("""DELL SMCI ANET VST CEG GEV NRG TLN CCJ VRT OKLO SMR PLTR SNOW NET
CRWD ZS DDOG PANW FTNT MDB TEAM HUBS WDAY DOCU OKTA TWLO U PATH AI SOUN AXON KTOS AVAV RKLB ASTS
CRDO ALAB NBIS APP COIN HOOD SOFI AFRM UPST DKNG RBLX UBER LYFT SHOP PYPL NU CART TOST TTWO RIVN
LCID ENPH FSLR RUN SEDG ALB CELH WING DECK ELF ANF CAVA""")

# ---- 主要ETFの代表構成銘柄（まずは静的。後で公開データに差し替え可） ----
ETF_HOLDINGS = {
    "QQQ": _T("AAPL MSFT NVDA AMZN AVGO META GOOGL TSLA COST NFLX PEP ADBE AMD"),
    "SMH": _T("NVDA TSM AVGO AMD ASML QCOM TXN AMAT LRCX KLAC MU ARM MRVL"),
    "XLK": _T("MSFT AAPL NVDA AVGO CRM ORCL AMD ACN ADBE CSCO QCOM TXN"),
    "XLE": _T("XOM CVX COP SLB EOG OXY PSX MPC VLO WMB KMI HES"),
    "XLU": _T("NEE DUK SO D AEP EXC XEL ED VST CEG PEG WEC"),
    "XLI": _T("GE CAT HON RTX UNP BA LMT DE ETN EMR NOC GD"),
    "XLF": _T("JPM V MA BAC WFC GS MS SPGI AXP C BLK CB"),
    "XLV": _T("LLY UNH JNJ MRK ABBV TMO ABT ISRG VRTX AMGN PFE BMY"),
    "ARKK": _T("TSLA COIN ROKU HOOD PLTR RBLX CRSP PATH DKNG SOFI U TWLO"),
    "URA": _T("CCJ NXE UEC DNN OKLO SMR LEU UUUU CEG NRG"),
    "ITA": _T("RTX BA LMT GD NOC LHX HII TDG AXON HWM KTOS AVAV"),
    "CIBR": _T("PANW CRWD FTNT ZS NET S CYBR OKTA QLYS RPD"),
}

# ===================== v7: 高速化・安定化 =====================
DISCOVERY_MAX_WORKERS = 8       # 並列取得スレッド数
DISCOVERY_TIMEOUT_SEC = 20      # 1銘柄あたりの取得タイムアウト
DISCOVERY_RETRY_COUNT = 1       # 失敗時のリトライ回数

DISCOVERY_CACHE_PATH = "user_data/discovery_cache.json"   # 差分キャッシュ
DISCOVERY_RUNS_PATH = "user_data/discovery_runs.csv"      # スキャンログ
DISCOVERY_CACHE_FRESH_HOURS = 24   # これ以内なら「新鮮」、超えると「古いデータ」警告
DISCOVERY_NEWS_TOPN = 40           # 高速モード: 一次通過の上位N銘柄だけニュースAI分析

ANALYSIS_MODES = ["高速モード（毎朝用）", "精密モード（週末の深掘り）"]
DEFAULT_ANALYSIS_MODE = "高速モード（毎朝用）"

# ===================== v8: 発掘バックテスト =====================
BACKTEST_MAX_TICKERS = 50      # 検証対象の上限（重さ対策）
BACKTEST_SAMPLE_EVERY = 5      # 検証日を何営業日ごとにするか（5=週1回）
BACKTEST_MAX_PICKS = 20        # 1検証日あたりの最大採用数
BACKTEST_STOP_PCT = 8.0        # 損切り幅(%)
BACKTEST_TP1_PCT = 10.0        # 利確1(%)
BACKTEST_TP2_PCT = 20.0        # 利確2(%)
BACKTEST_TRAIL_PCT = 8.0       # トレーリング幅(%)
# v18: 取引コスト（片道・%）。往復 = 2 × (手数料 + スリッページ)
BACKTEST_FEE_PCT = 0.05        # 手数料（片道, %）
BACKTEST_SLIPPAGE_PCT = 0.05   # スリッページ（片道, %）
BACKTEST_CACHE_PATH = "user_data/backtest_cache.json"
BACKTEST_TRADES_PATH = "user_data/backtest_trades.csv"

# PIT(その時点)発掘スコアの配点（ニュース/ファンダは未来情報のため除外）
BACKTEST_WEIGHTS = {"technical": 40, "relative_strength": 25, "theme": 20, "supply": 15}

BACKTEST_PERIODS = {"過去3ヶ月": 3, "過去6ヶ月": 6, "過去1年": 12, "過去2年": 24}
BACKTEST_HOLD = {"10営業日": 10, "20営業日": 20, "40営業日": 40, "60営業日": 60}
BACKTEST_ENTRY = ["翌営業日始値", "翌営業日終値", "スコア算出日の終値"]
BACKTEST_TAKE = ["利確1で半分→利確2で残り", "利確2で全売り", "保有期間終了で売り"]
BACKTEST_STOP = ["初期損切り", "トレーリングストップ", "20日線割れ撤退"]
BACKTEST_CONDITION = ["BUY以上だけ", "WATCH以上", "スコア上位TOP5", "スコア上位TOP10", "スコア上位TOP20"]

# ===================== v10: ルール最適化 =====================
OPT_RESULTS_PATH = "user_data/optimization_results.csv"
STRATEGY_SETTINGS_PATH = "user_data/strategy_settings.json"
OPT_MAX_COMBOS = 24            # グリッドの最大組み合わせ数（重さ対策）

BACKTEST_RSI_EXCLUDE = ["なし", "RSI70以上除外", "RSI75以上除外", "RSI80以上除外"]
BACKTEST_VOLUME = ["なし", "20日平均より増加", "50日平均より増加"]

# PITスコアの重みプリセット（合計100）。※過去検証は価格ベースのためニュース/ファンダは
# 相対強度・テーマ等で近似（ニュース重視＝モメンタム寄り、ファンダ重視＝テーマ寄り）。
WEIGHT_PRESETS = {
    "現在設定":       {"technical": 40, "relative_strength": 25, "theme": 20, "supply": 15},
    "テクニカル重視": {"technical": 55, "relative_strength": 20, "theme": 15, "supply": 10},
    "ニュース重視":   {"technical": 30, "relative_strength": 40, "theme": 20, "supply": 10},
    "ファンダ重視":   {"technical": 30, "relative_strength": 20, "theme": 35, "supply": 15},
    "テーマ重視":     {"technical": 25, "relative_strength": 20, "theme": 45, "supply": 10},
    "リスク低減重視": {"technical": 35, "relative_strength": 20, "theme": 15, "supply": 30},
}

# ===================== v11: ニュース精密解析 =====================
# 現在のマクロ状況（mid-June 2026 想定・手で更新可。VIX/10年債/原油はライブ値で上書き）
MACRO_FACTORS = {
    "CPI":            {"value": "4.2%",  "bias": "高止まり",   "note": "インフレ高め→利下げに慎重（グロースに逆風）"},
    "FOMC":           {"value": "6/17",  "bias": "据え置き見込", "note": "金利据え置き〜慎重スタンス"},
    "雇用統計":        {"value": "底堅い", "bias": "中立",       "note": "労働市場は堅調"},
    "米10年債利回り":  {"value": "4.45%", "bias": "やや高",     "note": "高金利は高PER・グロースに逆風、公益にも逆風"},
    "原油(WTI)":      {"value": "$86",   "bias": "高止まり",   "note": "地政学で高値→エネルギーに追い風、輸送/消費に逆風"},
    "ドル指数(DXY)":  {"value": "やや強", "bias": "中立",       "note": "ドル高は多国籍・半導体の海外売上に逆風"},
    "地政学":          {"value": "緊張",   "bias": "リスク",     "note": "中東情勢→防衛/エネルギーに追い風、全体にはリスク"},
    "VIX":            {"value": "20.5",  "bias": "やや高",     "note": "変動リスクやや高。急変時は新規を控えめに"},
}

# テーマ → 効くマクロ要因（銘柄別マクロ連動の判定に使用）
THEME_MACRO = {
    "半導体": ["米10年債利回り", "ドル指数(DXY)", "VIX", "FOMC"],
    "AI": ["米10年債利回り", "VIX", "FOMC"],
    "高PERグロース": ["米10年債利回り", "FOMC", "VIX"],
    "ソフトウェア": ["米10年債利回り", "FOMC"],
    "金利低下メリット": ["米10年債利回り", "FOMC"],
    "公益": ["米10年債利回り"], "電力": ["米10年債利回り", "原油(WTI)"],
    "原子力": ["米10年債利回り", "地政学"], "ウラン": ["地政学"],
    "エネルギー": ["原油(WTI)", "地政学"], "原油ヘッジ": ["原油(WTI)", "地政学"],
    "防衛": ["地政学", "VIX"], "地政学ヘッジ": ["地政学", "VIX"],
    "ヘルスケア": ["FOMC", "VIX"], "サイバーセキュリティ": ["米10年債利回り"],
}

# ===================== v12: 毎朝通知 =====================
NOTIFY_SETTINGS_PATH = "user_data/notify_settings.json"
NOTIFY_HISTORY_PATH = "user_data/notification_history.csv"
NOTIFY_TARGETS = ["未設定", "Discord Webhook", "Telegram Bot", "メール(SMTP)"]
NOTIFY_DEFAULT_TIME = "08:00"
NOTIFY_MAX_HOLDINGS = 12       # 通知で精密解析する保有の上限（コスト対策）

# ===================== v14.1: 銘柄名（検索用） =====================
TICKER_NAMES = {
    "NVDA": "NVIDIA", "AMD": "Advanced Micro Devices", "AVGO": "Broadcom", "MRVL": "Marvell",
    "TSM": "Taiwan Semiconductor", "MU": "Micron", "QCOM": "Qualcomm", "INTC": "Intel",
    "AMAT": "Applied Materials", "LRCX": "Lam Research", "KLAC": "KLA", "ASML": "ASML",
    "ARM": "Arm Holdings", "SMCI": "Super Micro Computer", "ANET": "Arista Networks", "DELL": "Dell",
    "MSFT": "Microsoft", "GOOGL": "Alphabet", "GOOG": "Alphabet", "META": "Meta Platforms",
    "AMZN": "Amazon", "PLTR": "Palantir", "CRM": "Salesforce", "NOW": "ServiceNow",
    "SNOW": "Snowflake", "ORCL": "Oracle", "ADBE": "Adobe", "PANW": "Palo Alto Networks",
    "CRWD": "CrowdStrike", "ZS": "Zscaler", "NET": "Cloudflare", "DDOG": "Datadog", "FTNT": "Fortinet",
    "VST": "Vistra", "CEG": "Constellation Energy", "GEV": "GE Vernova", "NRG": "NRG Energy",
    "TLN": "Talen Energy", "NEE": "NextEra Energy", "ETR": "Entergy", "CCJ": "Cameco",
    "VRT": "Vertiv", "ETN": "Eaton", "PWR": "Quanta Services", "OKLO": "Oklo", "SMR": "NuScale Power",
    "XOM": "Exxon Mobil", "CVX": "Chevron", "COP": "ConocoPhillips", "OXY": "Occidental",
    "SLB": "SLB (Schlumberger)", "EOG": "EOG Resources", "FANG": "Diamondback Energy",
    "LMT": "Lockheed Martin", "RTX": "RTX (Raytheon)", "NOC": "Northrop Grumman",
    "GD": "General Dynamics", "LHX": "L3Harris", "KTOS": "Kratos Defense", "AVAV": "AeroVironment",
    "LLY": "Eli Lilly", "UNH": "UnitedHealth", "ISRG": "Intuitive Surgical", "VRTX": "Vertex",
    "REGN": "Regeneron", "MRK": "Merck", "ABBV": "AbbVie", "TSLA": "Tesla", "UBER": "Uber",
    "NFLX": "Netflix", "AXON": "Axon Enterprise", "GE": "GE Aerospace", "CAT": "Caterpillar",
    "AAPL": "Apple", "JPM": "JPMorgan", "V": "Visa", "MA": "Mastercard", "WMT": "Walmart",
    "JNJ": "Johnson & Johnson", "PG": "Procter & Gamble", "HD": "Home Depot", "KO": "Coca-Cola",
    "MCD": "McDonald's", "DIS": "Disney", "BAC": "Bank of America", "WFC": "Wells Fargo",
    "GS": "Goldman Sachs", "MS": "Morgan Stanley", "COST": "Costco", "PEP": "PepsiCo",
    "TFC": "Truist Financial", "SOFI": "SoFi", "COIN": "Coinbase", "HOOD": "Robinhood",
    "SHOP": "Shopify", "ABNB": "Airbnb", "SPY": "S&P500 ETF", "QQQ": "Nasdaq100 ETF",
    "IWM": "Russell2000 ETF", "SMH": "Semiconductor ETF", "XLK": "Technology ETF",
    "XLE": "Energy ETF", "ITA": "Aerospace/Defense ETF", "NLR": "Nuclear ETF", "IGV": "Software ETF",
    "XLV": "Healthcare ETF", "URA": "Uranium ETF", "CIBR": "Cybersecurity ETF",
}

# ===================== v14.2: おすすめ監視銘柄（自動登録しない） =====================
SUGGESTED_WATCHLIST = {
    "AI/半導体": ["NVDA", "AMD", "AVGO", "MRVL"],
    "AIソフト": ["PLTR"],
    "電力/AIインフラ": ["VST", "GEV", "CEG"],
    "エネルギー/原子力": ["XOM", "CCJ"],
}
