# Comprehensive Research: Prediction Market Trading with LLMs

**Date:** 2026-03-25
**Goal:** Understand what actually works before rebuilding

---

## Research Questions

### 1. Academic Foundation

**Key Papers to Analyze:**
- [ ] "Prediction Markets: Theory and Practice" (Arrow et al.)
- [ ] "Information Aggregation in Betting Markets"
- [ ] "LLM Forecasting Accuracy" benchmarks
- [ ] "Wisdom of Crowds vs AI Predictions"
- [ ] Polymarket white papers on market mechanics

**Questions:**
- What edge opportunities exist in prediction markets?
- How efficient are these markets historically?
- What's the typical profit margin for successful traders?

---

### 2. Successful LLM Trading Approaches

**What We Need to Find:**
- Papers/case studies of profitable LLM trading
- How do quantitative firms use AI for forecasting?
- What's the state-of-the-art approach?

**Known Approaches:**
1. **Ensemble Methods** - Combine multiple models
2. **Calibration Layers** - Post-process raw predictions
3. **Market Microstructure** - Trade on order flow, not forecasts
4. **Hybrid Systems** - LLM + statistical models

---

### 3. Why We're Failing

**Current Issues:**
- LLM bullish bias → All BUY_YES trades lose
- Predicts 20-30% edges that don't exist
- No correlation between predicted edge and actual P&L

**Potential Root Causes:**
1. **Training Data Mismatch**
   - LLMs trained on historical text, not market prices
   - Don't understand "market-implied probability"

2. **Overconfidence**
   - LLMs generate confident narratives
   - Confidence != accuracy

3. **Missing Real-Time Data**
   - Markets have: polls, betting flow, insider knowledge
   - LLM has: training data cutoff + reasoning

4. **Wrong Task Framing**
   - We ask: "What's the probability?"
   - Should ask: "Why does market disagree with fundamentals?"

---

### 4. Alternative Architectures

**What if we DON'T use LLM for probability estimation?**

#### Architecture A: LLM as Information Processor
```
News/Events → LLM Summarizer → Impact Assessment → Trade Signal
```
- LLM extracts key facts from news
- Statistical model generates probabilities
- Trade on information edge, not forecast edge

#### Architecture B: Multi-Agent System
```
Agent 1: Bull Case (argues YES)
Agent 2: Bear Case (argues NO)
Agent 3: Judge (weighs evidence)
Agent 4: Market Maker (executes)
```
- Adversarial setup reduces overconfidence
- Each agent specialized
- More like human trader workflow

#### Architecture C: Hybrid Ensemble
```
LLM (10%) + Cross-Platform (30%) + Statistical (30%) + Flow (30%)
```
- LLM heavily downweighted
- Majority weight on data-driven signals
- LLM only for tie-breaking or filtering

#### Architecture D: Market Making
```
Don't forecast → Provide liquidity → Earn spread
```
- No directional bets
- Pure market making strategy
- Lower risk, steady returns

---

### 5. Opus 4.6 Capabilities

**Why Opus 4.6 Might Help:**
- 1M context window (can process entire market history)
- Better reasoning on complex scenarios
- Improved calibration (Anthropic claims)
- More nuanced probability estimates

**How to Use It:**
1. **Feed entire market context** (all related markets, historical prices)
2. **Multi-turn reasoning** (chain of thought over many turns)
3. **Adversarial prompting** (force it to argue both sides)
4. **Calibration validation** (test on resolved markets first)

**Cost Considerations:**
- Opus 4.6 is ~$15/1M input tokens
- With 1M context, one forecast could cost $15
- Need to be highly selective about which markets to analyze

---

### 6. Benchmark Requirements

**Before deploying ANY strategy:**

#### Test 1: Calibration on Resolved Markets
- Take 100 resolved markets
- Run LLM predictions
- Measure Brier score, log loss
- Build calibration plot (predicted vs actual)

#### Test 2: Baseline Comparisons
- Random trades: -2% (fees)
- Mean reversion: ?
- Fade extremes: ?
- Market consensus: 0%

**Our strategy MUST beat these baselines**

#### Test 3: Sharpe Ratio
- Measure risk-adjusted returns
- Target: Sharpe > 1.0
- Current: Probably negative Sharpe

---

### 7. Market Selection Criteria

**Not all markets are equal.** Need to filter:

#### Good Markets (Tradeable):
- High volume (>$100k/day)
- Tight spreads (<2%)
- Clear resolution criteria
- Mainstream topics (politics, sports, economics)

#### Bad Markets (Avoid):
- Low liquidity
- Joke markets ("Jesus vs GTA VI")
- Ambiguous resolution
- Insider-heavy (e.g., company internals)

---

### 8. Risk Management Lessons

**What We Learned:**
1. **Repeated trades on same market = disaster**
   - Took JD Vance trade 5 times → -$140 total
   - Need market memory

2. **No position limits**
   - Built up 38 BUY_NO positions
   - Massive correlated risk

3. **No learning from unrealized P&L**
   - Positions went negative immediately
   - Agent kept taking same trades

**Fix Requirements:**
- Max 1 position per market
- Track unrealized P&L in real-time
- Circuit breakers on drawdown
- Correlation limits across positions

---

## Research Plan (Next 4-6 Hours)

### Phase 1: Literature Review (2 hours)
- [ ] Find academic papers on prediction market efficiency
- [ ] Study LLM forecasting benchmarks
- [ ] Read Polymarket/trading case studies
- [ ] Document key insights

### Phase 2: Architecture Design (2 hours)
- [ ] Design 3 alternative architectures
- [ ] Estimate compute costs for each
- [ ] Define success metrics
- [ ] Choose best approach

### Phase 3: Validation Framework (1 hour)
- [ ] Build backtesting harness
- [ ] Create calibration test suite
- [ ] Define baseline strategies
- [ ] Set go/no-go criteria

### Phase 4: Opus 4.6 Integration (1 hour)
- [ ] Test Opus 4.6 with long context
- [ ] Compare vs Sonnet 4.5
- [ ] Measure calibration improvement
- [ ] Decide if cost is worth it

---

## Key Decisions to Make

### Decision 1: Continue with LLM Forecasting?
**Options:**
- A) Fix current approach (calibration, filtering)
- B) Use LLM differently (info processing, not forecasting)
- C) Abandon LLM for forecasting entirely

### Decision 2: Which Architecture?
**Criteria:**
- Proven track record
- Cost-effective
- Technically feasible
- Edge opportunity exists

### Decision 3: Opus 4.6 Worth It?
**Cost-Benefit:**
- $15 per forecast (with 1M context)
- Need ~95% win rate to break even at $100/trade
- Only worth it if materially better than Sonnet 4.5

---

## Success Criteria

**Before ANY live trading:**
1. ✓ Backtest shows positive expected value
2. ✓ Sharpe ratio > 1.0
3. ✓ Beats baseline strategies
4. ✓ Calibration plot shows accuracy
5. ✓ Understand WHY it works

**If we can't meet these, DON'T TRADE.**

---

## Next Actions

1. Get Opus 4.6 model ID from user
2. Start literature review
3. Design validation framework
4. Test on resolved markets
5. Make architecture decision

**STOP trading until research complete.**
