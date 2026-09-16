"""Shared spam/junk detection for the apinav catalog.

Open catalogs attract non-API junk that embeds to near-identical vectors and
drowns out real results. This module provides one source of truth for spam
detection, used by the MCP server (ranking gate) and ingest.py (persistence
gate).

Categories caught:
- account-selling spam ("buy verified/old <platform> accounts")
- gambling/betting spam (Vietnamese + English)
- adult/NSFW content spam
- SEO-article spam (keyword-stuffed articles disguised as APIs)

English tokens use word-boundary matching to avoid false positives
('bet' in 'beta', 'tyle' in 'style'). Vietnamese tokens are concatenated
and don't collide with English words, so substring matching is safe.
"""
import re
import unicodedata

_SPAM_NORM = re.compile(r"[^a-z0-9]+")
_SPAM_RE = re.compile(
    r"(buy|purchase|acquire|obtain)"
    r".*(account|accounts|gmail|paypal|cashapp|stripe|binance|"
    r"wise|coinbase|github|facebook|instagram|telegram|whatsapp|snapchat|"
    r"twitter|linkedin|googlevoice|reviews|ssn|drivinglicence|edumail)"
)
# "verified <platform> account" spam without a buy verb (e.g. "Fast Verified
# PayPal Account", "Verified Cash App Account")
_VERIFIED_ACCOUNT_RE = re.compile(
    r"verified.*(account|accounts|paypal|cashapp|stripe|binance|wise|coinbase|"
    r"gmail|github|telegram|whatsapp|snapchat|linkedin|googlevoice|reviews|ssn)"
)
_GAMBLE_EN = re.compile(
    r"\b(bet|betting|casino|gambl|jackpot|slot|poker|blackjack|roulette|"
    r"baccarat|sportsbook|bookmaker|wager)\b", re.I)
_ADULT_EN = re.compile(
    r"\b(sex|porn|xxx|nude|naked|dildo|hentai|escort|onlyfans|sexting|"
    r"pornhub)\b", re.I)
_GAMBLE_VI = re.compile(
    r"(nhacai|nhacaiuytin|lode|xoso|soicau|taixiu|gamebai|danhbai|tienao|"
    r"keonhacai|dudoan|ketqua|thongke|cacuoc|nohu|banca|quayhu|"
    r"doithuong|vipbet|net88|hcm66|go88|lodeonline|thantai|kqxs|xsmb|xsmn)", re.I)
_ADULT_VI = re.compile(r"(cugia|amdao|tinhduc|girlsnude)", re.I)
_SEO_MARKERS = re.compile(
    r"(la gi|la gì|là gì|tam quan trong|tầm quan trọng|dieu can biet|"
    r"điều cần biết|huong dan|hướng dẫn|cach |cách |tai sao|tại sao|"
    r"loi ich|lợi ích|how to|what is|why |top \d+|best \d+)", re.I)


def _strip_diacritics(s: str) -> str:
    """Strip Vietnamese/Latin diacritics so 'âm đạo' -> 'am dao'."""
    s = s.replace("đ", "d").replace("Đ", "D")
    return "".join(
        c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn"
    )


def is_spam(row) -> bool:
    """Detect spam/junk APIs. Accepts sqlite3.Row or dict-like with name,
    description, author keys."""
    if isinstance(row, dict):
        get = row.get
    else:
        get = lambda k, d=None: row[k] if k in row.keys() else d

    name = (get("name") or "").lower()
    norm = _SPAM_NORM.sub("", name)
    if _SPAM_RE.search(norm) or _VERIFIED_ACCOUNT_RE.search(norm):
        return True

    author = (get("author") or "").lower()
    anorm = _SPAM_NORM.sub("", author)
    if _SPAM_RE.search(anorm) or _VERIFIED_ACCOUNT_RE.search(anorm):
        return True

    desc = _strip_diacritics((get("description") or "").lower())
    blob = name + " " + desc + " " + author
    if _GAMBLE_EN.search(blob) or _ADULT_EN.search(blob):
        return True

    blob_norm = _SPAM_NORM.sub("", blob)
    if _GAMBLE_VI.search(blob_norm) or _ADULT_VI.search(blob_norm):
        return True
    if _SEO_MARKERS.search(blob):
        return True
    return False
