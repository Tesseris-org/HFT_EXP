# Adaptive Calibration Strategy

**Date:** 2026-03-20 16:31 UTC
**Reason:** Original conservative strategy still losing (-$73 in 63 min)

---

## Problem Diagnosed

After implementing conservative exit logic (STRATEGY_CHANGES.md), the strategy was STILL losing money:

- **Total P&L:** -$73.28 after 63 minutes
- **41 entries, 0 exits** (position holding working correctly)
- **Average predicted edge:** 18.9% (very high!)
- **Reality:** All positions went negative immediately after entry

**Root Cause:** LLM predictions are overconfident or miscalibrated. We think we have 18%+ edge, but positions lose money instantly.

---

## Solution: Adaptive Calibration System

Instead of full RL, implemented a **simpler feedback loop** that learns from mistakes:

### What It Does

1. **Tracks Performance**
   - Monitors every trade: predicted edge vs actual P&L
   - Computes category-level performance (which market types work)
   - Builds calibration curve (how often high-confidence predictions win)

2. **Automatic Adjustments**
   - **Dynamic Edge Threshold:** Raises when losing, lowers when winning
   - **Category Filtering:** Blocks categories that consistently lose
   - **Probability Calibration:** Adjusts overconfident predictions down

3. **Learning Rules**
   - Losing >$5/trade → Raise threshold +2%
   - Losing >$10/trade → Raise threshold +4%
   - Category loses >$10/trade on 5+ trades → Block category
   - Making >$5/trade → Lower threshold -1%

---

## Current Adaptations (From Previous Run)

Based on the losing trades from 09:48-10:51:

### Edge Threshold
- **Base:** 8.0%
- **Adaptive:** 12.0% (+4%)
- **Reason:** Avg loss of $25/trade → system raises bar significantly

### Category Filters
- **BLOCKED:** "JD Vance" markets (avg -$28/trade)
- **WATCHED:** "Republican Party" (avg -$36/trade total)
- **ALLOWED:** All other categories

### Calibration Curve
- Not enough resolved positions yet
- Will build curve as trades close
- Once >10 resolved trades: will adjust overconfident probabilities

---

## Fresh Start (Current Run)

**Started:** 2026-03-20 16:31 UTC
**Database:** Reset (backed up old to `agent_backup_conservative_20260320_163117.db`)
**Starting Balance:** $1,000

### Expected Behavior

1. **First 10 trades:** Uses base 8% threshold (no history yet)
2. **After 10 trades:** Adaptive system kicks in
3. **Every 30 min:** Recalculates optimal threshold
4. **As trades resolve:** Builds calibration curve

### Monitoring

- **Dashboard:** http://localhost:5556 (now shows $0 P&L)
- **Logs:** `tail -f agent.log`
- **Analysis:** `python analyze_performance.py` (after 2-4 hours)
- **Calibration Report:** `python test_calibration.py` (shows current adaptations)

---

## Success Criteria (4 Hour Test)

### If Adaptive System Works
- [ ] Total P&L > $0 (breakeven or better)
- [ ] Execution rate < 40% (being selective with 12%+ threshold)
- [ ] System automatically raises threshold if starts losing
- [ ] Category filters prevent repeat mistakes

### If Still Losing
Next debugging steps:
1. **Check LLM calibration** - Run signal validation on resolved markets
2. **Reduce LLM weight** - Maybe cross-platform is more accurate (currently 59% LLM, 41% xplat)
3. **Add reality check** - Compare LLM to recent market movement before trading
4. **Question selection** - Maybe we're picking inherently unpredictable markets

---

## How It Works (Technical)

### `strategy/adaptive_calibration.py`
- `AdaptiveCalibrator` class learns from history
- `get_recent_performance()` - computes metrics on last 24h
- `get_recommended_edge_threshold()` - dynamic threshold based on P&L
- `should_filter_category()` - blocks consistently losing categories
- `apply_calibration()` - adjusts overconfident probabilities
- `compute_calibration_curve()` - learns prediction accuracy by bucket

### Integration in `main.py`
- **Ensemble:** Passes `calibrator` to adjust probabilities
- **Decision:** Uses `dynamic_threshold` instead of fixed `MIN_EDGE_THRESHOLD`
- **Market Filter:** Blocks categories via `should_filter_category()`
- **Reporting:** New loop logs calibration report every 30 min

---

## Key Insight

**The problem isn't exit logic** - it's that our entry signals are wrong.

Even with perfect position holding, if we enter on false edges (think we have 18% but actually have -18%), we'll lose money. The adaptive system fixes this by:

1. Learning which predictions are trustworthy
2. Raising the bar when predictions prove wrong
3. Filtering out systematic mistakes (bad categories)
4. Calibrating overconfident estimates

---

## Next Steps

1. **Monitor 2-4 hours** - Let adaptive system collect data
2. **Check calibration report** - See what it learns
3. **If still losing:**
   - Investigate LLM calibration (why so overconfident?)
   - Consider reducing LLM weight in ensemble
   - Maybe add "sanity check" filters (e.g., no joke markets like "Jesus vs GTA VI")
4. **If profitable:**
   - Build LiveExecutor for real money
   - Test with tiny capital ($5-10/trade)
   - Deploy to EC2

---

**The beauty of this approach:** We don't need to guess what's wrong. The system tells us by tracking performance and adapting automatically.
