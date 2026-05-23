# Polymarket API URLs
GAMMA_API_URL = "https://gamma-api.polymarket.com"
CLOB_API_URL = "https://clob.polymarket.com"

# Polygon
POLYGON_CHAIN_ID = 137

# Contract addresses (Polygon mainnet)
USDC_ADDRESS = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
CTF_EXCHANGE_ADDRESS = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEGRISK_EXCHANGE_ADDRESS = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
NEGRISK_ADAPTER_ADDRESS = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

# Bedrock model IDs (verified working with current account access)
BEDROCK_HAIKU = "us.anthropic.claude-3-5-haiku-20241022-v1:0"
BEDROCK_SONNET = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"  # Upgraded to Sonnet 4.5
BEDROCK_OPUS = "us.anthropic.claude-opus-4-6-v1"  # Opus 4.6 (1M context)

# Trading defaults
MIN_EDGE_THRESHOLD = 0.08  # Raised from 5% to 8% - be more selective
MIN_ORDER_SIZE_USD = 5.0
MAX_POSITION_SIZE_USD = 100.0
KELLY_FRACTION = 0.20  # Reduced from 0.25 to 0.20 - more conservative sizing
STOP_LOSS_PCT = 0.50  # Raised from 0.30 to 0.50 - give positions more room
ORDER_TIMEOUT_MINUTES = 15
DEFAULT_FEE_RATE = 0.02
