import json
import re
import unicodedata
from typing import Any


def strip_code_fence(raw: str) -> str:
    """
    Remove a surrounding ```json ... ``` (or plain ``` ... ```) fence from an
    LLM response. Unlike `str.strip("```json")`, which strips any of those
    characters and can corrupt valid JSON, this matches the fence as a substring.
    """
    s = raw.strip()
    # Leading fence
    s = re.sub(r"^```(?:json|JSON)?\s*\n?", "", s)
    # Trailing fence
    s = re.sub(r"\n?```\s*$", "", s)
    return s.strip()


def parse_json_loose(raw: str) -> Any:
    """
    Parse a JSON value from an LLM response that may be wrapped in a code fence
    or have trailing prose. Falls back to truncating at the last `}` or `]`.
    """
    cleaned = strip_code_fence(raw)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        last = max(cleaned.rfind("}"), cleaned.rfind("]"))
        if last != -1:
            return json.loads(cleaned[: last + 1])
        raise


def slugify(text: str) -> str:
    """
    Convert text to a URL-friendly slug.
    Supports Vietnamese characters by removing diacritics.
    """
    if not text:
        return ""
    
    # Convert to lowercase
    text = text.lower()
    
    # Remove Vietnamese diacritics
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode('ascii')
    
    # Replace non-alphanumeric characters with hyphens
    text = re.sub(r'[^a-z0-9]+', '-', text)
    
    # Remove leading/trailing hyphens
    text = text.strip('-')
    
    return text
