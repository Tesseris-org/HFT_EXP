#!/usr/bin/env python3
"""Test if Claude Sonnet 4.5 is available on Bedrock."""

import boto3
import json
from config import settings
from config.constants import BEDROCK_SONNET

def test_model():
    client = boto3.client("bedrock-runtime", region_name=settings.AWS_REGION)

    print(f"Testing model: {BEDROCK_SONNET}")
    print(f"Region: {settings.AWS_REGION}")
    print()

    try:
        response = client.invoke_model(
            modelId=BEDROCK_SONNET,
            contentType="application/json",
            accept="application/json",
            body=json.dumps({
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 100,
                "messages": [
                    {
                        "role": "user",
                        "content": "What is 2+2? Reply with just the number."
                    }
                ]
            })
        )

        result = json.loads(response['body'].read())
        print("✅ SUCCESS! Sonnet 4.5 is available and working!")
        print(f"Response: {result['content'][0]['text']}")
        print()
        print("Model successfully upgraded to Claude Sonnet 4.5")

    except Exception as e:
        print(f"❌ ERROR: {e}")
        print()
        print("Sonnet 4.5 may not be available on your Bedrock account yet.")
        print("Reverting to Sonnet 4...")
        return False

    return True

if __name__ == "__main__":
    test_model()
