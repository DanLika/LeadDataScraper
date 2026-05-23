import json
import re
from typing import Any, Optional, cast


def extract_json_from_response(text: Optional[str]) -> Optional[dict[str, Any]]:
    """
    Robustly extracts a JSON object from an AI model response.
    Handles markdown code fences, extra whitespace, and greedy regex edge cases.

    Returns ``None`` for empty/None input, parse failure, or when the parsed
    JSON is not an object (top-level array / scalar). Non-dict results are
    intentionally dropped — every caller expects a dict; returning a list
    or string would propagate the wrong shape into typed downstream code.
    """
    if not text:
        return None

    # Step 1: Strip markdown code fences
    cleaned = re.sub(r'```(?:json)?\s*', '', text)
    cleaned = re.sub(r'```\s*$', '', cleaned).strip()

    # Step 2: Try direct parse
    try:
        parsed = json.loads(cleaned)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    if isinstance(parsed, dict):
        return cast(dict[str, Any], parsed)

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
                        candidate = json.loads(cleaned[start:i + 1])
                    except (json.JSONDecodeError, ValueError):
                        candidate = None
                    if isinstance(candidate, dict):
                        return cast(dict[str, Any], candidate)
                    start = None

    return None
