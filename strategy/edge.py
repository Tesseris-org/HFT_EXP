"""Edge calculation and trade decision logic."""

from config.constants import MIN_EDGE_THRESHOLD, DEFAULT_FEE_RATE


def calculate_edge(ensemble_prob: float, market_price: float,
                   fee_rate: float = DEFAULT_FEE_RATE) -> tuple[float, str]:
    """
    Calculate the expected edge of a trade.

    Returns (net_edge, side) where net_edge is always positive if there's an edge.
    Side is 'BUY_YES' or 'BUY_NO'.
    """
    if ensemble_prob > market_price:
        raw_edge = ensemble_prob - market_price
        side = "BUY_YES"
    else:
        raw_edge = market_price - ensemble_prob
        side = "BUY_NO"

    net_edge = raw_edge - fee_rate
    return net_edge, side


def has_sufficient_edge(net_edge: float, min_threshold: float = MIN_EDGE_THRESHOLD) -> bool:
    return net_edge > min_threshold
