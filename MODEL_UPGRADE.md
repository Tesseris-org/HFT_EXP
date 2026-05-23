# Model Upgrade to Claude Sonnet 4.5

**Date:** 2026-03-20 17:02 UTC
**Status:** Successfully Deployed

---

## Upgrade Details

### Previous Model
- **Model:** Claude Sonnet 4
- **ID:** `global.anthropic.claude-sonnet-4-20250514-v1:0`
- **Release:** May 2025

### New Model
- **Model:** Claude Sonnet 4.5
- **ID:** `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- **Release:** September 2025

---

## Why Sonnet 4.5?

**Improvements over Sonnet 4:**
1. **Better Reasoning** - Enhanced chain-of-thought and structured reasoning
2. **Improved Calibration** - More accurate probability estimates
3. **Context Understanding** - Better at weighing complex evidence
4. **Instruction Following** - More reliable JSON output formatting
5. **Knowledge Cutoff** - More recent training data (January 2025 vs August 2024)

**Expected Impact on Trading:**
- More accurate probability estimates
- Better understanding of complex political/economic scenarios
- Improved calibration (when model says 70%, it happens ~70% of time)
- Fewer parsing errors with JSON responses

---

## Verification

✅ Tested on Bedrock: Working
✅ Simple inference test: Passed
✅ Agent restarted: Running
✅ No configuration changes needed: Model ID updated only

---

## Cost

Same pricing tier as Sonnet 4:
- **Input:** ~$3 per million tokens
- **Output:** ~$15 per million tokens
- **Coverage:** AWS credits (no out-of-pocket cost)

---

## Rollback Plan

If Sonnet 4.5 performs worse:
1. Edit `config/constants.py`
2. Change `BEDROCK_SONNET` back to: `global.anthropic.claude-sonnet-4-20250514-v1:0`
3. Restart agent: `kill <pid> && nohup .venv/bin/python main.py --mode paper &`

---

## Next Steps

1. Monitor performance for 2-4 hours
2. Compare calibration vs Sonnet 4 (when data available)
3. Check if predictions are more accurate
4. Verify adaptive calibration system works with new model

---

**Note:** All other components remain unchanged. Only the LLM forecaster uses the upgraded model.
