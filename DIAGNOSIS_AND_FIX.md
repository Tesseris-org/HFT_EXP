# Root Cause Analysis & Fix

**Date:** 2026-03-25
**Status:** CRITICAL ISSUE IDENTIFIED

---

## The Problem in Numbers

**Performance:**
- 48 trades executed
- Total P&L: **-$251.83**
- Avg loss per trade: **-$5.25**

**Edge Predictions vs Reality:**
| Predicted Edge | Trades | Avg P&L | Total P&L |
|---------------|--------|---------|-----------|
| 8-9%          | 10     | -$3.90  | -$39.03   |
| 11%           | 21     | -$8.43  | -$177.04  |
| 22%           | 7      | -$5.11  | -$35.76   |
| 36%           | 8      | $0.00   | $0.00     |
| 43%           | 2      | $0.00   | $0.00     |

**Pattern:** Higher predicted edge = Bigger losses (until they're so extreme they don't execute properly)

---

## Root Cause: Systematic Bullish Bias

### The Smoking Gun

**BUY_YES trades (LLM is BULLISH):**
- Count: 10 trades
- LLM thinks probability is **+14.6% higher** than market
- Total P&L: **-$251.83** (ALL the losses!)
- Avg loss: **-$25.18 per trade**

**BUY_NO trades (LLM is BEARISH):**
- Count: 38 trades
- LLM thinks probability is **-22.5% lower** than market
- Total P&L: **$0.00** (no realized losses yet)

### What This Means

**When LLM is MORE optimistic than the market, it's WRONG.**

Examples:
1. **Republican House Control**
   - Market: 15.5%
   - LLM: 45.8% (thinks Republicans have much better chance)
   - Edge: +28.3% (massive!)
   - Result: **-$35.76 loss**
   - **Market was right, LLM was wrong**

2. **JD Vance 2028**
   - Market: 20.4%
   - LLM: 33-35% (thinks Vance more likely to win)
   - Edge: +12%
   - Result: **-$28.26 loss per trade** (took this trade 5 times!)
   - **Market was right, LLM was wrong**

3. **Putin out by Dec 2026**
   - Market: 12.5%
   - LLM: 22-23% (thinks more likely)
   - Edge: +8.8%
   - Result: **-$13.01 loss**
   - **Market was right, LLM was wrong**

---

## Why Is This Happening?

### Hypothesis 1: LLM Optimism Bias
- Language models tend to be optimistic about change/events
- "Things will happen" is easier to argue than "things won't happen"
- LLM generates bullish narratives convincingly, even when wrong

### Hypothesis 2: Market Efficiency
- Prediction markets aggregate real information from traders with money at stake
- LLM only has training data (pre-Jan 2025) + generic reasoning
- Market has: polls, betting patterns, insider knowledge, real-time info

### Hypothesis 3: Anchoring
- Despite two-stage prompting, LLM might still anchor to market price
- Then it "adjusts" in a predictable (bullish) direction
- Creating fake edge that doesn't exist

### Hypothesis 4: Calibration Training Mismatch
- LLM was trained on general knowledge, not prediction markets
- "70% confidence" in general reasoning ≠ "70% market price"
- Systematic miscalibration when applied to markets

---

## Why Repeated Losing Trades?

**Agent took the SAME losing trade multiple times:**
- JD Vance: 5 times
- Republican House: 2 times
- Putin out: 3 times

**Why no learning?**
1. **Positions don't resolve immediately** - No feedback signal
2. **Unrealized P&L isn't used** - Adaptive calibration only learns after close
3. **No market-specific memory** - Each cycle treats market as new

---

## The Fix Options

### Option 1: Invert the Signal (Quick Fix) ⭐
**Hypothesis:** If LLM is anti-predictive, fade it!

```python
# Instead of:
if llm_prob > market_price + threshold:
    action = "BUY_YES"

# Do:
if llm_prob < market_price - threshold:
    action = "BUY_YES"  # Fade the LLM!
```

**Logic:**
- When LLM is bullish → Market is probably right, don't buy
- When LLM is bearish → Maybe there's contrarian value
- Basically use LLM as reverse indicator

**Risk:** Might just lose money in the opposite direction

---

### Option 2: Calibration Adjustment (Medium Fix)
**Apply aggressive discounting to LLM estimates:**

```python
# LLM says 60%, market says 40%
llm_adjusted = market_price + (llm_prob - market_price) * 0.2

# Now: LLM says 44% vs market 40% (much smaller disagreement)
```

**Tuning:**
- Current: Using LLM probability directly → Big losses
- Fix: Weight LLM predictions only 20% vs market 80%
- Or: Apply systematic -10% adjustment to all LLM predictions

---

### Option 3: Filter to Bearish Trades Only (Conservative)
**Only take trades where LLM is LESS optimistic than market:**

```python
if action == "BUY_YES":
    return HOLD  # Never bet on things LLM is bullish on

if action == "BUY_NO" and edge > threshold:
    return EXECUTE  # Only bet against things LLM is bearish on
```

**Result:**
- Eliminates all the losing BUY_YES trades (-$252)
- Keeps BUY_NO trades ($0 so far, might be winners when they resolve)

---

### Option 4: Reduce LLM Weight to Near Zero (Nuclear Option)
**Just use cross-platform signals:**

```python
ensemble_weights = {
    "llm": 0.10,  # Down from 59%
    "cross_platform": 0.90,  # Up from 41%
}
```

**Or even:**
```python
ensemble_weights = {
    "llm": 0.00,  # Completely remove LLM
    "cross_platform": 1.00,
}
```

**Logic:** If LLM is net negative value, remove it

---

### Option 5: Market-Making Instead (Pivot)
**Stop directional betting entirely:**
- Place limit orders on both sides
- Earn the spread
- No forecasting needed
- Lower variance, steady profits

---

## Recommended Immediate Actions

### 1. Stop Trading (DONE)
Agent is already stopped. Don't restart until we fix this.

### 2. Test Option 3 (Simplest Fix)
```python
# In main.py decision logic:
if net_edge > 0 and side == "BUY_YES":
    return Decision(**base, action="HOLD", reason="filtered_bullish_llm")
```

### 3. Add Position Memory
```python
# Track markets we've already traded
traded_markets = set()
if market_id in traded_markets:
    return HOLD  # Don't repeat same trade
```

### 4. Validate with Backtest
- Run on historical data
- See if filtering BUY_YES trades would have been profitable
- Measure: Would we have made money with just BUY_NO trades?

---

## Key Insights

1. **Markets are efficient** - When LLM disagrees, market is usually right
2. **LLM has systematic bias** - Bullish on change/events
3. **Edge prediction is backwards** - Higher predicted edge = bigger loss
4. **Need contrarian approach** - Can't just trust LLM's confidence

---

## Next Steps

**Test Option 3 First (30 minutes):**
1. Add filter: Block all BUY_YES trades
2. Restart agent
3. Monitor for 2 hours
4. Check if BUY_NO trades are profitable

**If that works:**
- Understand WHY (is LLM bearish bias more accurate?)
- Tune threshold for BUY_NO only
- Build market memory to avoid repeats

**If that doesn't work:**
- Try Option 4 (reduce LLM weight)
- Or Option 5 (pivot to market making)
- Or conclude prediction markets aren't exploitable with this approach

---

## Bottom Line

**The LLM is not adding value - it's destroying value.**

Every dollar of predicted "edge" is actually a dollar of expected loss.

We need to either:
- Invert the signal (fade the LLM)
- Remove the LLM (use other signals)
- Pivot entirely (market making, not forecasting)

**Do NOT trade real money until this is fixed.**
