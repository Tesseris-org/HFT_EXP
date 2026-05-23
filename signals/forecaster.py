"""LLM superforecaster — two-stage prompting via Amazon Bedrock."""

import asyncio
import json
import logging
import time
from typing import Optional

import boto3

from config import settings
from config.constants import BEDROCK_SONNET, BEDROCK_HAIKU
from core.models import Market, Signal

logger = logging.getLogger(__name__)

# ── Prompt templates ─────────────────────────────────────

STAGE1_SYSTEM = """You are a superforecaster. You will be given a question about a future \
event and relevant context. Estimate the probability of the YES outcome.

You do NOT know what the market currently thinks. Form your own independent view.

Follow this structured reasoning process:
1. DECOMPOSE: Break the question into key sub-questions
2. BASE RATE: What is the historical base rate for similar events?
3. EVIDENCE FOR: List factors that increase probability
4. EVIDENCE AGAINST: List factors that decrease probability
5. SYNTHESIS: Weigh the evidence and arrive at a final probability

IMPORTANT:
- Output a precise probability between 0.01 and 0.99
- Be calibrated: when you say 70%, things should happen ~70% of the time
- Don't hedge toward 50% by default — take a real position
- Reason step by step before giving your number

OUTPUT FORMAT: Respond ONLY with valid JSON, no other text.
{
  "decomposition": "your decomposition here",
  "base_rate": "historical base rate reasoning",
  "evidence_for": ["factor 1", "factor 2"],
  "evidence_against": ["factor 1", "factor 2"],
  "synthesis": "weighing the evidence",
  "initial_probability": 0.XX
}"""

STAGE2_SYSTEM = """You are a superforecaster performing a calibration check.

You previously made an independent probability estimate for a prediction market question.
Now you will see what the market and other forecasting platforms think.

The market reflects the consensus of many participants, some of whom may have information \
you don't. But the market can also be wrong, especially:
- Right after news breaks (slow to reprice)
- On niche topics with few informed traders
- When anchored on stale information

Decide whether to adjust your estimate or keep it. Be honest — if the market knows \
something you don't, adjust toward it. If you have good reasoning the market is missing, \
stand your ground.

OUTPUT FORMAT: Respond ONLY with valid JSON, no other text.
{
  "market_delta_reasoning": "why your estimate differs from market (or doesn't)",
  "adjustment": "description of any adjustment",
  "final_probability": 0.XX,
  "confidence": 0.XX
}"""


class Forecaster:
    """Two-stage LLM forecaster using Claude via Bedrock."""

    def __init__(self, region: Optional[str] = None):
        self.region = region or settings.AWS_REGION
        self.client = boto3.client("bedrock-runtime", region_name=self.region)

    @staticmethod
    def _parse_json(text: str) -> Optional[dict]:
        """
        Robustly extract JSON from LLM response.
        Handles: markdown fences, trailing text after JSON, control chars in strings.
        """
        import re
        text = text.strip()

        # Strip markdown code fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0]
            text = text.strip()

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Extract the first complete JSON object or array
        for start_char, end_char in [('{', '}'), ('[', ']')]:
            start = text.find(start_char)
            if start == -1:
                continue
            depth = 0
            for i, ch in enumerate(text[start:], start):
                if ch == start_char:
                    depth += 1
                elif ch == end_char:
                    depth -= 1
                    if depth == 0:
                        try:
                            fragment = text[start:i + 1]
                            # Replace control characters inside the fragment
                            cleaned = re.sub(r'[\x00-\x1f\x7f]', ' ', fragment)
                            return json.loads(cleaned)
                        except json.JSONDecodeError:
                            break

        return None

    async def _invoke_async(self, model_id: str, system: str, user_message: str,
                            max_tokens: int = 2048) -> Optional[dict]:
        """Async wrapper — runs the synchronous boto3 call in a thread pool."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self._invoke(model_id, system, user_message, max_tokens)
        )

    def _invoke(self, model_id: str, system: str, user_message: str,
                max_tokens: int = 2048) -> Optional[dict]:
        """Call Bedrock Claude and parse JSON response."""
        try:
            response = self.client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": max_tokens,
                    "system": system,
                    "messages": [{"role": "user", "content": user_message}],
                }),
            )
            body = json.loads(response["body"].read())
            text = body["content"][0]["text"]

            return self._parse_json(text)

        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}\nRaw: {text[:300]}")
            return None
        except Exception as e:
            logger.error(f"Bedrock invocation failed: {e}")
            return None

    async def forecast(self, market: Market, news_context: str = "",
                       cross_platform_prices: Optional[dict] = None,
                       model_id: Optional[str] = None) -> Optional[Signal]:
        """
        Run two-stage superforecasting on a market (async — doesn't block event loop).

        Returns a Signal with the final probability estimate, or None on failure.
        """
        model = model_id or BEDROCK_SONNET
        start = time.time()

        # ── Stage 1: Blind estimate ──────────────────────
        stage1_user = self._build_stage1_prompt(market, news_context)
        stage1_result = await self._invoke_async(model, STAGE1_SYSTEM, stage1_user)

        if not stage1_result or "initial_probability" not in stage1_result:
            logger.warning(f"Stage 1 failed for market {market.market_id}")
            return None

        initial_prob = float(stage1_result["initial_probability"])
        initial_prob = max(0.01, min(0.99, initial_prob))

        # ── Stage 2: Calibration with market price ───────
        stage2_user = self._build_stage2_prompt(
            market, initial_prob, stage1_result, cross_platform_prices
        )
        stage2_result = await self._invoke_async(model, STAGE2_SYSTEM, stage2_user)

        if not stage2_result or "final_probability" not in stage2_result:
            # Fall back to stage 1 estimate if stage 2 fails
            logger.warning(f"Stage 2 failed for {market.market_id}, using stage 1 estimate")
            final_prob = initial_prob
            confidence = 0.3  # lower confidence for single-stage
            reasoning = json.dumps(stage1_result)
        else:
            final_prob = float(stage2_result["final_probability"])
            final_prob = max(0.01, min(0.99, final_prob))
            confidence = float(stage2_result.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))
            reasoning = json.dumps({
                "stage1": stage1_result,
                "stage2": stage2_result,
            })

        latency_ms = int((time.time() - start) * 1000)

        from datetime import datetime
        return Signal(
            market_id=market.market_id,
            source="llm_forecaster",
            probability=final_prob,
            confidence=confidence,
            reasoning=reasoning,
            context_json=json.dumps({
                "news_context": news_context[:500] if news_context else "",
                "cross_platform": cross_platform_prices,
                "market_price": market.yes_price,
            }),
            model=model,
            latency_ms=latency_ms,
            created_at=datetime.utcnow(),
        )

    def _build_stage1_prompt(self, market: Market, news_context: str) -> str:
        parts = [
            f"Question: {market.question}",
            f"Category: {market.category}" if market.category else "",
            f"End date: {market.end_date.strftime('%Y-%m-%d') if market.end_date else 'Not specified'}",
        ]
        if news_context:
            parts.append(f"\nRecent relevant news:\n{news_context}")
        return "\n".join(p for p in parts if p)

    def _build_stage2_prompt(self, market: Market, initial_prob: float,
                             stage1_result: dict, cross_platform_prices: Optional[dict]) -> str:
        parts = [
            f"Question: {market.question}",
            f"\nYour independent estimate: {initial_prob:.2f}",
            f"Your reasoning: {stage1_result.get('synthesis', '')}",
            f"\nCurrent market price (YES): {market.yes_price:.2f}",
        ]
        if cross_platform_prices:
            parts.append("\nCross-platform estimates:")
            for platform, data in cross_platform_prices.items():
                prob = data.get("prob", data) if isinstance(data, dict) else data
                parts.append(f"  - {platform}: {prob}")

        parts.append(
            "\nQuestions to consider:"
            "\n1. Why might the market differ from your estimate?"
            "\n2. Do you have information the market might not have priced in?"
            "\n3. Is the market more likely right, or are you?"
        )
        return "\n".join(parts)

    async def quick_relevance_check(self, news_title: str, market_questions: list[str]) -> list[int]:
        """
        Use Haiku to quickly check which markets a news item is relevant to.
        Returns indices of relevant markets. Async — doesn't block event loop.
        """
        if not market_questions:
            return []

        numbered = "\n".join(f"{i}. {q}" for i, q in enumerate(market_questions))
        user_msg = (
            f"News headline: {news_title}\n\n"
            f"Which of these prediction market questions is this news relevant to? "
            f"Return ONLY a JSON array of the question numbers (0-indexed). "
            f"Return [] if none are relevant.\n\n{numbered}"
        )

        result = await self._invoke_async(BEDROCK_HAIKU, "You are a concise news analyst.", user_msg, max_tokens=256)
        # Result may be a list directly, or a dict with a list value
        if isinstance(result, list):
            indices = result
        elif isinstance(result, dict):
            # Extract first list value found
            indices = next((v for v in result.values() if isinstance(v, list)), [])
        else:
            return []
        return [int(i) for i in indices if isinstance(i, (int, float)) and 0 <= int(i) < len(market_questions)]
