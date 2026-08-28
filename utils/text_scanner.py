import re
import unicodedata
from typing import Tuple, Optional


# Invisible and zero-width characters regex
ZERO_WIDTH_PATTERN = re.compile(
    r'[\u200B-\u200D\uFEFF\u200E\u200F\u202A-\u202E\u2060-\u206F\u00AD\u180E\u00A0]'
)

# Combining diacritics (Zalgo text)
ZALGO_PATTERN = re.compile(r'[\u0300-\u036F\u1AB0-\u1AFF\u1DC0-\u1DFF\u20D0-\u20FF\uFE20-\uFE2F]')

# Common homoglyph map (Cyrillic / Greek -> Latin)
HOMOGLYPH_MAP = {
    'а': 'a', 'а́': 'a', 'б': 'b', 'в': 'b', 'г': 'r', 'д': 'd', 'е': 'e', 'ё': 'e',
    'ж': 'zh', 'з': 'z', 'и': 'u', 'й': 'u', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'h',
    'о': 'o', 'п': 'n', 'р': 'p', 'с': 'c', 'т': 't', 'у': 'y', 'ф': 'f', 'х': 'x',
    'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e',
    'ю': 'yu', 'я': 'ya',
    'А': 'A', 'В': 'B', 'Е': 'E', 'К': 'K', 'М': 'M', 'Н': 'H', 'О': 'O', 'Р': 'P',
    'С': 'C', 'Т': 'T', 'У': 'Y', 'Х': 'X',
    'α': 'a', 'β': 'b', 'γ': 'g', 'δ': 'd', 'ε': 'e', 'ι': 'i', 'κ': 'k', 'ν': 'v',
    'ο': 'o', 'ρ': 'p', 'τ': 't', 'υ': 'u', 'χ': 'x',
}

# Leetspeak map for obfuscated text
LEET_MAP = {
    '0': 'o',
    '1': 'i',
    '!': 'i',
    '|': 'l',
    '3': 'e',
    '4': 'a',
    '@': 'a',
    '5': 's',
    '$': 's',
    '7': 't',
    '+': 't',
    '8': 'b',
}

# Regex for Discord Invites
INVITE_REGEX = re.compile(
    r'(?:https?:\/\/)?(?:www\.)?(?:discord\.(?:gg|io|me|li|com\/invite)|discordapp\.com\/invite|dsc\.gg|invite\.gg)\/([a-zA-Z0-9\-_]+)',
    re.IGNORECASE
)

# Regex for URL shorteners
SHORTENER_REGEX = re.compile(
    r'(?:https?:\/\/)?(?:www\.)?(?:bit\.ly|is\.gd|tinyurl\.com|t\.co|cutt\.ly|clck\.ru|goo\.gl|ow\.ly|rb\.gy|tiny\.cc|shorte\.st|adf\.ly|bc\.vc|v\.gd|t\.me)\/[a-zA-Z0-9\-_]+',
    re.IGNORECASE
)

# Regex for Phishing / Scam keywords & fake domains
SCAM_DOMAINS_REGEX = re.compile(
    r'(?:dlscord|discorcl|discrod|discord-app|discord-gift|discord-nitro|nitro-gift|free-nitro|steamcomminuty|steamcommunlty|steamcommunity-trade|steancommunity|rust-skins|csgo-skins|free-robux|claim-nitro|airdrop-token)\.(?:com|org|net|xyz|ru|to|site|online|club|info|link|cc|gg)',
    re.IGNORECASE
)

# General URL regex
GENERAL_URL_REGEX = re.compile(
    r'(?:https?:\/\/|www\.)[^\s<>\(\)\[\]\{\}]+|(?:\b[a-zA-Z0-9\-]+\.(?:com|net|org|ru|xyz|top|live|site|online|pro|gg|io|me|info|biz|shop|app|tech)\b(?:\/[^\s]*)?)',
    re.IGNORECASE
)


def normalize_text(text: str) -> str:
    """Strip zero-width characters, zalgo diacritics, and normalize homoglyphs."""
    if not text:
        return ""
    
    # 1. Remove zero-width & invisible spaces
    text = ZERO_WIDTH_PATTERN.sub('', text)
    
    # 2. Remove Zalgo diacritics
    text = ZALGO_PATTERN.sub('', text)
    
    # 3. Unicode NFKD normalization
    text = unicodedata.normalize('NFKD', text)
    
    # 4. Replace homoglyphs
    normalized_chars = []
    for ch in text:
        normalized_chars.append(HOMOGLYPH_MAP.get(ch, ch))
    text = ''.join(normalized_chars)
    
    return text.strip()


def deobfuscate_leetspeak(text: str) -> str:
    """Convert common leetspeak characters to letters."""
    chars = []
    for ch in text:
        chars.append(LEET_MAP.get(ch, ch))
    return ''.join(chars)


def scan_for_links(text: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Scans text for prohibited links, phishing, invites, and shorteners.
    Returns: (is_malicious, match_type, matched_string)
    """
    if not text:
        return False, None, None

    cleaned = normalize_text(text)
    deobfuscated = deobfuscate_leetspeak(cleaned)

    # 1. Check for Phishing / Scam domains
    scam_match = SCAM_DOMAINS_REGEX.search(text) or SCAM_DOMAINS_REGEX.search(cleaned) or SCAM_DOMAINS_REGEX.search(deobfuscated)
    if scam_match:
        return True, "Фишинг / Скам домен", scam_match.group(0)

    # 2. Check for Discord Invites
    invite_match = INVITE_REGEX.search(text) or INVITE_REGEX.search(cleaned) or INVITE_REGEX.search(deobfuscated)
    if invite_match:
        return True, "Инвайт-ссылка на Discord сервер", invite_match.group(0)

    # 3. Check for Shorteners
    short_match = SHORTENER_REGEX.search(text) or SHORTENER_REGEX.search(cleaned) or SHORTENER_REGEX.search(deobfuscated)
    if short_match:
        return True, "Сокращатель ссылок", short_match.group(0)

    # 4. Check for Any Link / URL
    gen_match = GENERAL_URL_REGEX.search(text) or GENERAL_URL_REGEX.search(cleaned)
    if gen_match:
        return True, "Сторонняя веб-ссылка", gen_match.group(0)

    return False, None, None


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Compute Levenshtein distance ratio between 0.0 and 1.0."""
    s1, s2 = s1.lower(), s2.lower()
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0

    len1, len2 = len(s1), len(s2)
    dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]

    for i in range(len1 + 1):
        dp[i][0] = i
    for j in range(len2 + 1):
        dp[0][j] = j

    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            cost = 0 if s1[i - 1] == s2[j - 1] else 1
            dp[i][j] = min(
                dp[i - 1][j] + 1,        # deletion
                dp[i][j - 1] + 1,        # insertion
                dp[i - 1][j - 1] + cost  # substitution
            )

    max_len = max(len1, len2)
    return 1.0 - (dp[len1][len2] / max_len)
