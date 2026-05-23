# Research Plan - Before We Continue Trading

**Status:** Three consecutive losing runs (-$77, -$73, -$83)
**Problem:** Consistently predicting 20-30% edges that don't exist
**Date:** 2026-03-20

---

## Critical Questions We Need to Answer

### 1. Are Prediction Markets Even Exploitable?

**Research Needed:**
- [ ] Academic papers on prediction market efficiency
- [ ] What edges actually exist? (liquidity, information asymmetry, behavioral biases)
- [ ] Who makes money trading prediction markets? (market makers vs speculators)
- [ ] What's the historical Sharpe ratio of prediction market strategies?

**Key Papers to Read:**
- "Information Aggregation in Prediction Markets" (Arrow et al.)
- "Prediction Market Efficiency" studies
- Metaculus/Good Judgment Project research on forecaster accuracy
- Polymarket volume/liquidity analysis

**Questions:**
- Are these markets too efficient for small traders?
- What's the typical bid-ask spread? (fee drag)
- How much capital do successful traders deploy?

---

### 2. Do LLMs Actually Work for Forecasting?

**Research Needed:**
- [ ] LLM forecasting accuracy benchmarks
- [ ] Calibration studies (Brier scores, log loss)
- [ ] Comparison: LLM vs human superforecasters vs market prices
- [ ] Known biases in LLM probability estimates

**Key Papers/Studies:**
- "Language Models as Zero-Shot Reasoners" (calibration section)
- "Can Large Language Models Forecast?" studies
- Anthropic's research on model calibration
- OpenAI's forecasting eval results

**Our Evidence So Far:**
- Predicting 20-30% edges consistently
- All positions go negative immediately
- Both Sonnet 4 and 4.5 show same pattern
- Suggests fundamental miscalibration, not just noise

---

### 3. Benchmark Against Baselines

**What We Should Test:**

#### Baseline 1: Random Walk (No Edge)
- Buy random markets at market price
- Hold to resolution
- Expected P&L: -2% (fees)
- **If we're doing worse than this, our "edge" is negative**

#### Baseline 2: Market Consensus
- Just track what market prices do over time
- No trades, just observation
- Measure: Do prices move predictably?

#### Baseline 3: Simple Mean Reversion
- Buy when market moves >10% in a day
- Sell when reverts to mean
- No AI, just mechanical

#### Baseline 4: Fade Extreme Probabilities
- Buy NO on markets >80%
- Buy YES on markets <20%
- Exploit overconfidence at tails

**Compare Against Our LLM Strategy:**
If our LLM does worse than these simple strategies, the AI is adding negative value.

---

### 4. Validate Our Implementation

**Potential Bugs/Issues:**

#### Edge Calculation
- [ ] Are we calculating edge correctly?
- [ ] Test: If LLM says 70% and market is 50%, is edge really +20%?
- [ ] Are we accounting for fees properly?
- [ ] Is Kelly sizing working correctly?

#### Paper Trading Assumptions
- [ ] Are we simulating slippage realistically?
- [ ] Do we get instant fills at midpoint? (unrealistic)
- [ ] Are we updating market prices correctly?
- [ ] Is P&L calculation accurate?

#### Signal Quality
- [ ] Run validation: LLM estimates vs actual market outcomes
- [ ] Build calibration plot (predicted % vs actual %)
- [ ] Check: Are we using stale data?
- [ ] Verify: Two-stage prompting working as intended?

---

### 5. Market Microstructure Analysis

**What We Need to Understand:**

#### Liquidity
- [ ] What's the typical order book depth?
- [ ] Can we even get filled at quoted prices?
- [ ] How much can we trade without moving the market?

#### Competition
- [ ] Who else is trading these markets?
- [ ] Are we competing against:
  - Market makers with better fills?
  - Insiders with private information?
  - Quantitative funds with better models?

#### Market Selection
- [ ] Which market types are profitable? (sports, politics, entertainment)
- [ ] Are "joke markets" (Jesus vs GTA VI) tradeable? (probably not)
- [ ] Minimum volume/liquidity requirements?

---

## Proposed Investigation Steps

### Phase 1: Validation (1-2 hours)
1. **Fix analysis script** - Run on resolved markets to measure actual accuracy
2. **Build calibration plot** - LLM predicted % vs actual outcomes
3. **Check edge calculation** - Manual verification on sample trades
4. **Review losing trades** - Why did each position lose? Pattern?

### Phase 2: Benchmarking (2-3 hours)
1. **Backtest simple strategies** - Mean reversion, fade extremes, random
2. **Compare Sharpe ratios** - LLM vs baselines
3. **Measure market efficiency** - How predictable are price movements?
4. **Test on different market types** - Sports vs politics vs entertainment

### Phase 3: Research (2-4 hours)
1. **Read key papers** - Prediction market efficiency, LLM forecasting
2. **Study successful strategies** - What do winning traders do?
3. **Analyze Polymarket structure** - Fees, liquidity, competition
4. **Review calibration techniques** - How to fix overconfident predictions?

### Phase 4: Strategy Redesign (4-6 hours)
Based on findings, either:
- **Fix current approach** (if fundamentally sound but miscalibrated)
- **Pivot to different strategy** (if LLM edge doesn't exist)
- **Stop trading** (if markets are too efficient)

---

## Red Flags We've Seen

1. **Consistent losses** - Three runs, all negative
2. **Massive predicted edges** - 20-30% average (unrealistic)
3. **Immediate drawdowns** - Positions lose money instantly after entry
4. **No improvement with better model** - Sonnet 4.5 did no better than Sonnet 4
5. **Adaptive learning didn't help** - Raising threshold to 12% still lost money
6. **Extreme predictions** - Up to 82% edge on single trade (absurd)

**Hypothesis:** The LLM is fundamentally miscalibrated for prediction markets. It doesn't understand the difference between:
- "I think this is 70% likely" (personal belief)
- "The market should price this at 70%" (profitable edge)

---

## Key Questions to Answer

**Before continuing:**

1. **Is there ANY evidence our LLM has an edge?**
   - Not just predicted edge (which is wrong)
   - Actual outperformance vs market/baseline

2. **What's causing the miscalibration?**
   - Anchoring on market prices?
   - Overconfidence in reasoning?
   - Lack of domain knowledge?
   - Training data mismatch?

3. **Can this be fixed?**
   - Better prompting?
   - Different model?
   - Ensemble with other signals?
   - Aggressive calibration curve?

4. **Should we even use LLMs for this?**
   - Maybe prediction markets are solved by markets themselves
   - Maybe we need different data sources (news, sentiment, flow)
   - Maybe it's a market-making play, not a directional play

---

## Alternative Approaches to Consider

If LLM forecasting doesn't work:

### 1. Pure Market Making
- Provide liquidity, earn spread
- No directional bets
- Lower risk, lower reward

### 2. News/Event Trading
- Trade on breaking news before market reprices
- Requires ultra-low latency
- More software engineering than AI

### 3. Flow Analysis
- Track large orders, smart money
- Follow the whales
- Requires order book access

### 4. Statistical Arbitrage
- Find correlated markets
- Trade spreads, not directions
- More math, less forecasting

### 5. Hybrid Approach
- LLM for filtering (not prediction)
- Use LLM to find interesting markets
- Use simpler models for actual trading

---

## Success Criteria

**Before deploying real capital, we must show:**

1. **Positive expected value** - Backtest on resolved markets shows profit
2. **Statistical significance** - Not just lucky on small sample
3. **Sharpe ratio > 1** - Risk-adjusted returns beat baseline
4. **Understand why it works** - Not just "AI magic"
5. **Robust to market conditions** - Works across different types of questions

**If we can't meet these criteria:**
→ Abandon trading strategy
→ Pivot to market making or different approach
→ Or conclude prediction markets aren't exploitable with our resources

---

## Timeline

- **Now:** Stop all trading, analyze data
- **Today:** Run validation scripts, build calibration plots
- **Tomorrow:** Read key research, test baselines
- **End of week:** Decision point - continue, pivot, or stop

---

## Resources Needed

1. **Historical data** - Resolved markets with outcomes
2. **Research papers** - Academic + industry
3. **Benchmark code** - Simple baseline strategies
4. **Analysis tools** - Calibration plots, Sharpe ratios
5. **Domain expertise** - Talk to people who actually make money trading prediction markets

---

**Bottom Line:** We've been "flying blind" - tweaking parameters without understanding if the fundamental approach works. Time to be rigorous.
