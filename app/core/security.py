import hashlib


def hash_token(raw_token: str) -> str:
    """Hash a high-entropy random token (e.g. a session token) for storage.

    Not for passwords -- session tokens are already cryptographically random,
    so a fast, unsalted hash is sufficient to avoid storing the raw secret.
    """
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
