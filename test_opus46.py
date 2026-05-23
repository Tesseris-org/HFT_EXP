#!/usr/bin/env python3
"""Test if Claude Opus 4.6 is available on Bedrock."""

import boto3
import json
from config import settings

# Try different Opus 4.6 model IDs
OPUS_46_IDS = [
    "us.anthropic.claude-opus-4-6-20250926-v1:0",
    "global.anthropic.claude-opus-4-6-20250926-v1:0",
    "anthropic.claude-opus-4-6-20250926-v1:0",
    "us.anthropic.claude-opus-4-6-v1:0",
]

def test_opus_46():
    client = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)

    print("Testing Claude Opus 4.6 availability...")
    print(f"Region: {settings.AWS_REGION}")
    print()

    for model_id in OPUS_46_IDS:
        print(f"Trying: {model_id}...")
        try:
            response = client.invoke_model(
                modelId=model_id,
                contentType="application/json",
                accept="application/json",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 100,
                    "messages": [
                        {
                            "role": "user",
                            "content": "Say 'Opus 4.6 works' if you are Claude Opus 4.6."
                        }
                    ]
                })
            )

            result = json.loads(response['body'].read())
            print(f"✅ SUCCESS! {model_id}")
            print(f"Response: {result['content'][0]['text']}")
            print(f"\nUse this model ID: {model_id}")
            return model_id

        except Exception as e:
            print(f"❌ Failed: {str(e)[:100]}")
            print()

    print("\n❌ Opus 4.6 not available on your Bedrock account yet.")
    print("Current available models:")
    print("  - Claude Sonnet 4.5: us.anthropic.claude-sonnet-4-5-20250929-v1:0")
    print("  - Claude Sonnet 4: global.anthropic.claude-sonnet-4-20250514-v1:0")
    return None

if __name__ == "__main__":
    test_opus_46()
