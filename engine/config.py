"""Static configuration: universe, thresholds and the regime weight matrix."""
from datetime import timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

MARKET_OPEN = (9, 15)
MORATORIUM_MIN = 15            # no automated execution for first 15 minutes
LAST_ENTRY = (15, 0)
MARKET_CLOSE = (15, 30)

# Pillar order: fundamental, price_action, institutional, macro
REGIME_WEIGHTS = {
    "TRENDING":     {"fundamental": 0.10, "price_action": 0.40, "institutional": 0.40, "macro": 0.10},
    "RANGE_BOUND":  {"fundamental": 0.25, "price_action": 0.15, "institutional": 0.50, "macro": 0.10},
    "EVENT_DRIVEN": {"fundamental": 0.50, "price_action": 0.10, "institutional": 0.30, "macro": 0.10},
    "MACRO_SHOCK":  {"fundamental": 0.05, "price_action": 0.15, "institutional": 0.20, "macro": 0.60},
    # Not in the source blueprint: neutral blend used when no regime's criteria are met.
    "TRANSITIONAL": {"fundamental": 0.20, "price_action": 0.30, "institutional": 0.40, "macro": 0.10},
}

# Event taxonomy: (tier, event, probability of sustainable momentum, regex)
TAXONOMY = [
    (1, "EARNINGS", 0.85, r"\b(q[1-4]\s*(fy\s*\d+)?|quarterly|net profit|profit (jumps|surges|rises|falls|slumps|drops)|results?|earnings|pat\b|ebitda)"),
    (1, "USFDA", 0.85, r"\b(usfda|u\.s\. fda|us fda|anda|form 483|warning letter|establishment inspection|eir\b)"),
    (1, "REGULATORY", 0.78, r"\b(sebi|rbi)\b.*\b(ban|bar|penalt|fine|order|probe|audit|restrict|risk weight|curb|action|investigat)|\bforensic audit|trading ban"),
    (2, "ORDER_WIN", 0.60, r"\b(bags?|wins?|secures?|receives?|awarded|lands?)\b.*\b(order|contract|project)"),
    (2, "PROMOTER_PLEDGE", 0.60, r"\bpledge[ds]?\b.*\b(invok|sold|sell)|\binvocation\b"),
    (3, "MOU", 0.25, r"\b(mou|memorandum of understanding|ties up|partnership|collaborat|pact)"),
]

POS_EARN = r"\b(beat|beats|surge|jump|soar|record|rise|rises|up \d+|growth)\b"
NEG_EARN = r"\b(miss|misses|slump|plunge|fall|falls|drop|drops|decline|loss|down \d+)\b"

SCORE_THRESHOLD = 0.20          # |composite| needed for a directional call
VPIN_TOXIC = 0.55
MIN_TURNOVER_CR = 75.0          # avg daily turnover proxy (INR crore) - SEBI F&O criterion
CIRCUIT_WARN_PCT = 2.0
DEFAULT_BAND_PCT = 10.0         # F&O stocks: dynamic bands; 10% default
LPP_PCT = 0.40
LPP_MIN_PREMIUM = 50.0
RISK_FREE = 0.065

# Approximate market caps (INR crore) for order-value materiality. Static: refresh periodically.
# (symbol, name, sector, sector_index, mcap_cr)
UNIVERSE = [
    ("RELIANCE", "Reliance Industries", "Energy", "^CNXENERGY", 1900000),
    ("TCS", "Tata Consultancy Services", "IT", "^CNXIT", 1350000),
    ("HDFCBANK", "HDFC Bank", "Banking", "^NSEBANK", 1450000),
    ("ICICIBANK", "ICICI Bank", "Banking", "^NSEBANK", 1000000),
    ("INFY", "Infosys", "IT", "^CNXIT", 650000),
    ("SBIN", "State Bank of India", "Banking", "^NSEBANK", 750000),
    ("AXISBANK", "Axis Bank", "Banking", "^NSEBANK", 380000),
    ("KOTAKBANK", "Kotak Mahindra Bank", "Banking", "^NSEBANK", 400000),
    ("BAJFINANCE", "Bajaj Finance", "Financials", "^NSEBANK", 560000),
    ("BHARTIARTL", "Bharti Airtel", "Telecom", "^NSEI", 1100000),
    ("ITC", "ITC", "FMCG", "^CNXFMCG", 520000),
    ("HINDUNILVR", "Hindustan Unilever", "FMCG", "^CNXFMCG", 560000),
    ("LT", "Larsen & Toubro", "Infrastructure", "^NSEI", 520000),
    ("MARUTI", "Maruti Suzuki", "Auto", "^CNXAUTO", 400000),
    ("M&M", "Mahindra & Mahindra", "Auto", "^CNXAUTO", 400000),
    ("SUNPHARMA", "Sun Pharma", "Pharma", "^CNXPHARMA", 420000),
    ("DRREDDY", "Dr. Reddy's", "Pharma", "^CNXPHARMA", 100000),
    ("CIPLA", "Cipla", "Pharma", "^CNXPHARMA", 125000),
    ("TATASTEEL", "Tata Steel", "Metals", "^CNXMETAL", 200000),
    ("JSWSTEEL", "JSW Steel", "Metals", "^CNXMETAL", 250000),
    ("HINDALCO", "Hindalco", "Metals", "^CNXMETAL", 160000),
    ("ONGC", "ONGC", "Energy", "^CNXENERGY", 300000),
    ("NTPC", "NTPC", "Power", "^NSEI", 330000),
    ("POWERGRID", "Power Grid", "Power", "^NSEI", 280000),
    ("HAL", "Hindustan Aeronautics", "Defence", "^NSEI", 300000),
    ("BEL", "Bharat Electronics", "Defence", "^NSEI", 270000),
    ("WIPRO", "Wipro", "IT", "^CNXIT", 280000),
    ("HCLTECH", "HCL Technologies", "IT", "^CNXIT", 450000),
]

INDEX_SYMBOLS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "VIX": "^INDIAVIX"}
