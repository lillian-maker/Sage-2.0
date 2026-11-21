"""ULID public ids (Q-D9): 48-bit millisecond time + 80 random bits, Crockford base32."""
import secrets
import time

ALPHABET = '0123456789ABCDEFGHJKMNPQRSTVWXYZ'
PUBLIC_ID_PATTERN = r'[0-9A-HJKMNP-TV-Z]{26}'


def new_public_id(now_ms=None):
    timestamp = int(time.time() * 1000) if now_ms is None else int(now_ms)
    if not 0 <= timestamp < 1 << 48:
        raise ValueError('INVALID_TIMESTAMP')
    value = (timestamp << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        chars.append(ALPHABET[value & 31])
        value >>= 5
    return ''.join(reversed(chars))
