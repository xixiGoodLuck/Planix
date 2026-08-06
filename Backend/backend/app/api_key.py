INVALID_API_KEY_MESSAGE = "API key must be plain ASCII without spaces. Paste the raw key only."


def validate_api_key_format(api_key: str) -> str | None:
    cleaned = api_key.strip()
    if not cleaned:
        return None
    if not cleaned.isascii() or any(char.isspace() for char in cleaned):
        return INVALID_API_KEY_MESSAGE
    if len(cleaned) < 8 or cleaned.lower() in {"your_key", "api_key", "apikey", "replace_me", "test"}:
        return INVALID_API_KEY_MESSAGE
    return None
