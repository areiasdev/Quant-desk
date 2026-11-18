"""Symbol classification for the universe and the meme track.

The live exchangeInfo endpoint (which carries Binance's own underlyingType) is geo-blocked here
(HTTP 451) and must not be bypassed, so classes come from two auditable sources:

1. explicit lists below (stablecoins, Binance index contracts, known TradFi tickers), and
2. a data fingerprint for new listings (TradFi underlyings close at weekends; see below).

Anything not caught is treated as a crypto perp. classify.py writes the result, with the
fingerprint numbers, to data/symbol_classes.csv, and universe.py writes every excluded symbol
that WOULD have entered the top-10 to data/universe_excluded_hits.csv.

Classes
    crypto     - ordinary crypto-asset perpetual (eligible)
    stablecoin - stablecoin or fiat pair (excluded)
    index      - Binance index/basket contracts (excluded)
    tradfi     - equities, ETFs, commodities, FX, pre-IPO trackers (excluded as "odd products")
"""
from __future__ import annotations

STABLECOIN = {
    "USDCUSDT", "BUSDUSDT", "TUSDUSDT", "FDUSDUSDT", "USDPUSDT", "DAIUSDT", "USDEUSDT",
    "USD1USDT", "RLUSDUSDT", "PYUSDUSDT", "EURUSDT", "AEURUSDT",
    # TerraUSD Classic: a failed algorithmic stablecoin; excluded as a stablecoin pair.
    "USTCUSDT",
}

INDEX = {"BTCDOMUSDT", "DEFIUSDT", "FOOTBALLUSDT", "BLUEBIRDUSDT"}

# Commodity / precious-metal trackers (incl. gold-backed tokens: their price IS gold).
_COMMODITY = {"XAU", "XAG", "XPT", "XPD", "PAXG", "XAUT", "COPPER", "NATGAS"}
# Single stocks (US, KR, HK, JP, CN), ETFs, leveraged ETFs, pre-IPO trackers, FX.
# (Most of the 2026 stock listings are also caught by the fingerprint; this list covers the ones
# with too little weekend history for it.)
_TRADFI_TICKERS = {
    # US single stocks
    "AAOI", "AAPL", "ABBV", "ACN", "ADBE", "AKAM", "ALAB", "AMAT", "AMC", "AMD", "AMGN", "AMZN",
    "ANET", "APLD", "ARM", "ASML", "ASTS", "AVGO", "AXTI", "BABA", "BAC", "BMNR", "BRKB", "CEG",
    "CIEN", "COHR", "COIN", "COST", "CRCL", "CRDO", "CRM", "CRML", "CRWD", "CRWV", "CSCO", "CVNA",
    "DDOG", "DELL", "DIS", "DJT", "DKNG", "EBAY", "EQT", "FLNC", "FRHC", "GEV", "GLW", "GME",
    "GOOGL", "GTLB", "HIMS", "HOOD", "HPE", "HUT", "IBKR", "IBM", "INTC", "IONQ", "IREN", "JPM",
    "KLAC", "LITE", "LLY", "LRCX", "MARA", "MCD", "MDB", "MELI", "META", "MRK", "MRNA", "MRVL",
    "MSFT", "MSTR", "MU", "NBIS", "NFLX", "NKE", "NOK", "NVDA", "NVO", "OKLO", "ONDS", "ORCL",
    "OXY", "PANW", "PDD", "PFE", "PLTR", "PYPL", "QCOM", "RDDT", "RDW", "RIVN", "RKLB", "SHOP",
    "SMCI", "SMR", "SNDK", "SNOW", "SNPS", "SOFI", "STM", "TEM", "TSLA", "TSM", "TTWO", "TWST",
    "TXN", "UBER", "UNH", "USAR", "VKTX", "WDC", "WMT", "XOM",
    # ETFs and leveraged ETFs
    "BITO", "EIDO", "EWJ", "EWY", "EWZ", "EWZS", "GDX", "IWM", "QQQ", "SMH", "SOXL", "SOXS",
    "SPY", "SQQQ", "TQQQ", "TZA", "URNM", "UVXY", "XBI", "XLE", "NVDL", "TSLL", "MSTU", "TMF",
    "KODEX200", "CSOPSAMSUNG2L", "CSOPSKHYNIX2L",
    # Korea / Hong Kong / Japan / China listed names
    "HYUNDAI", "SAMSUNG", "SAMSUNGEM", "SKHYNIX", "SKHY", "LGELECTRONICS", "NAVER", "HANMI",
    "HK0625", "HK0700", "HK0992", "HK1810", "TENCENT", "MEITUAN", "KUAISHOU", "POPMART", "SONY",
    "ZHONGJI", "BYD",
    # pre-IPO trackers
    "ANTHROPIC", "OPENAI", "SPCX", "ZHIPU", "MINIMAX", "UNITREE", "CXMT",
    # FX
    "USDBRL",
}
TRADFI = {t + "USDT" for t in _COMMODITY | _TRADFI_TICKERS}

# ------------------------------------------------------------------------------------------
# TradFi fingerprint. Underlying stock/commodity markets close at weekends, so a TradFi perp's
# weekend volume collapses. Measured on 1d bars since FINGERPRINT_FROM:
#   weekend_volume_ratio = median weekend daily quote volume / median weekday daily quote volume
# Calibration (run of 2026-11-18): listed TradFi perps median 0.15 (max ~0.56, pre-IPO
# trackers); crypto perps listed before 2025-11 are all >= 0.456 (BTC is the lowest); new
# listings split cleanly between <= 0.47 and >= 0.53. So: a symbol first listed in or after
# FINGERPRINT_NEW_LISTING_FROM with >= FINGERPRINT_MIN_WEEKEND_DAYS weekend days and a ratio
# below FINGERPRINT_MAX_WEEKEND_VOLUME_RATIO is classed tradfi. Older symbols are never
# re-classed by the fingerprint.
# ------------------------------------------------------------------------------------------
FINGERPRINT_FROM = "2025-06-01"
FINGERPRINT_NEW_LISTING_FROM = "2025-11"
FINGERPRINT_MIN_WEEKEND_DAYS = 8
FINGERPRINT_MAX_WEEKEND_VOLUME_RATIO = 0.5

# ------------------------------------------------------------------------------------------
# Meme coins (rule and borderline decisions: research/radar/meme_universe.md).
# Rule: the token's main value proposition is a joke, mascot, animal, internet meme, celebrity
# or political figure, and there is no protocol/product whose usage the token captures.
# A meme must ALSO be classed crypto (so a stock perp sharing a meme's ticker is never a meme).
# ------------------------------------------------------------------------------------------
MEME = {
    # dog / cat / frog / animal / mascot memes
    "DOGEUSDT", "1000SHIBUSDT", "1000PEPEUSDT", "1000FLOKIUSDT", "1000BONKUSDT", "WIFUSDT",
    "1MBABYDOGEUSDT", "MYROUSDT", "BOMEUSDT", "SLERFUSDT", "POPCATUSDT", "MEWUSDT",
    "BRETTUSDT", "PONKEUSDT", "TURBOUSDT", "NEIROUSDT", "NEIROETHUSDT", "1000CATUSDT",
    "MOODENGUSDT", "PNUTUSDT", "HIPPOUSDT", "CHILLGUYUSDT", "1000CHEEMSUSDT", "1000WHYUSDT",
    "1000000MOGUSDT", "1000000BOBUSDT", "DOGSUSDT", "PENGUUSDT", "KOMAUSDT", "TOSHIUSDT",
    "BANUSDT", "MEMEUSDT",
    # BRC-20 meme tokens
    "1000SATSUSDT", "1000RATSUSDT",
    # AI-themed memecoins with no product (the AI bot/story is the meme)
    "GOATUSDT", "ACTUSDT", "PIPPINUSDT", "FARTCOINUSDT",
    # celebrity / political / culture memes
    "TRUMPUSDT", "MELANIAUSDT", "VINEUSDT", "PEOPLEUSDT", "SPXUSDT", "DEGENUSDT",
    "JELLYJELLYUSDT", "USELESSUSDT",
    # BNB-chain meme wave (2025-2026)
    "TSTUSDT", "MUBARAKUSDT", "BROCCOLI714USDT", "BROCCOLIF3BUSDT", "TUTUSDT", "BANANAS31USDT",
    "GIGGLEUSDT", "哈基米USDT", "币安人生USDT", "我踏马来了USDT", "牛来USDT", "龙虾USDT",
}

# Borderline tokens reviewed and NOT counted as meme (documented in meme_universe.md).
MEME_BORDERLINE_EXCLUDED = {
    "ORDIUSDT": "BRC-20 standard/infrastructure token, not a mascot meme",
    "NOTUSDT": "Notcoin: tap-to-earn game token",
    "HMSTRUSDT": "Hamster Kombat: tap-to-earn game token",
    "CATIUSDT": "Catizen: game platform token",
    "MEMEFIUSDT": "MemeFi: tap-to-earn game token despite the name",
    "1000XUSDT": "X Empire: tap-to-earn game token",
    "AI16ZUSDT": "ai16z/ElizaOS: AI-agent framework with a product",
    "AIXBTUSDT": "aixbt: AI analytics agent with a product",
    "ZEREBROUSDT": "Zerebro: AI agent project",
    "GRIFFAINUSDT": "Griffain: AI agent platform",
    "SWARMSUSDT": "Swarms: AI agent framework",
    "VIRTUALUSDT": "Virtuals Protocol: AI agent launchpad",
    "SIRENUSDT": "AI-agent token",
    "PUMPUSDT": "pump.fun platform token (meme launchpad, not a meme itself)",
    "CLANKERUSDT": "Clanker token-launchpad token",
    "WLFIUSDT": "World Liberty Financial: DeFi governance token",
    "LUNA2USDT": "Terra 2.0 L1 token",
    "1000LUNCUSDT": "Terra Classic L1 token",
    "DOODUSDT": "Doodles NFT brand token",
    "BIRBUSDT": "Moonbirds NFT brand token (PENGU is included: it is widely categorised as meme)",
    "ANIMEUSDT": "Animecoin (Azuki): culture/IP token",
    "ZORAUSDT": "Zora creator-coin platform token",
    "BOBUSDT": "listed 2025-11 at ~$0.02: most likely BOB (Build on Bitcoin) L2, not the BOB meme",
    "BUSDT": "B / BUILDon (BNB chain): possibly a meme, not verified - excluded pending review",
    # Same ticker as a meme, but the archive contract is a stock perp (TradFi fingerprint):
    "WENUSDT": "listed 2026-07 with stock-like weekend fingerprint: Wendy's, not the WEN meme",
    "PENGUSDT": "listed 2026-07 with stock-like weekend fingerprint: Penguin Solutions",
    "CATUSDT": "listed 2026-07 with stock-like weekend fingerprint: Caterpillar",
}


def static_class(symbol: str) -> str:
    if symbol in STABLECOIN:
        return "stablecoin"
    if symbol in INDEX:
        return "index"
    if symbol in TRADFI:
        return "tradfi"
    return "crypto"


def fingerprint_is_tradfi(first_month: str, weekend_days: float, weekend_volume_ratio: float) -> bool:
    return (bool(first_month) and first_month >= FINGERPRINT_NEW_LISTING_FROM
            and weekend_days >= FINGERPRINT_MIN_WEEKEND_DAYS
            and weekend_volume_ratio < FINGERPRINT_MAX_WEEKEND_VOLUME_RATIO)
