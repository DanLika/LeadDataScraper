import json
import re
from typing import Optional


def extract_json_from_response(text: str) -> Optional[dict]:
    """
    Robustly extracts a JSON object from an AI model response.
    Handles markdown code fences, extra whitespace, and greedy regex edge cases.
    """
    if not text:
        return None

    # Step 1: Strip markdown code fences
    cleaned = re.sub(r'```(?:json)?\s*', '', text)
    cleaned = re.sub(r'```\s*$', '', cleaned).strip()

    # Step 2: Try direct parse
    try:
        return json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        pass

    # Step 3: Find JSON object with balanced braces
    depth = 0
    start = None
    for i, ch in enumerate(cleaned):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            if depth > 0:
                depth -= 1
                if depth == 0 and start is not None:
                    try:
                        return json.loads(cleaned[start:i + 1])
                    except (json.JSONDecodeError, ValueError):
                        start = None

    return None
