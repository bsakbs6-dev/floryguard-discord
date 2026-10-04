import re
import unicodedata
import urllib.parse
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

# Regex for IP addresses (IPv4 with optional port, e.g. 192.168.1.1 or 45.142.122.10:25565)
IP_REGEX = re.compile(
    r'\b(?:(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.){3}(?:25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)(?::\d{1,5})?\b'
)

# Regex for Phishing / Scam keywords & fake domains
SCAM_DOMAINS_REGEX = re.compile(
    r'(?:dlscord|discorcl|discrod|discord-app|discord-gift|discord-nitro|nitro-gift|free-nitro|steamcomminuty|steamcommunlty|steamcommunity-trade|steancommunity|rust-skins|csgo-skins|free-robux|claim-nitro|airdrop-token)\.[a-zA-Z0-9\-]{2,12}',
    re.IGNORECASE
)

# General URL regex
GENERAL_URL_REGEX = re.compile(
    r'(?:https?:\/\/|www\.)[^\s<>\(\)\[\]\{\}]+|(?:\b[a-zA-Z0-9\-]+\.(?:com|net|org|ru|xyz|top|live|site|online|pro|gg|io|me|info|biz|shop|app|tech|su|by|kz|ua|gift|fun|link|cc|space|cloud|store|dev|page|vip|trade|monster|quest|club|to)\b(?:\/[^\s]*)?)',
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


def is_valid_gif_url(raw_url: str) -> bool:
    """
    Strictly validates if a URL is a legitimate GIF animation from trusted GIF providers.
    Prevents advertising bypasses (e.g. adding '.gif' to arbitrary links or query params).
    Only whitelisted media hosts with verified path formats are permitted.
    """
    if not raw_url:
        return False

    url = raw_url.strip().rstrip('.,!?:;)"\'>]')
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        parsed = urllib.parse.urlsplit(url)
    except Exception:
        return False

    # Block userinfo tricks (e.g. https://tenor.com@evil.com)
    if parsed.username or parsed.password:
        return False

    hostname = (parsed.hostname or '').lower().rstrip('.')
    if not hostname:
        return False

    # Standard web ports only
    if parsed.port and parsed.port not in (80, 443):
        return False

    # Prevent IDN homoglyph spoofing in domain
    if hostname.startswith('xn--') or any(ord(c) > 127 for c in hostname):
        return False

    path = parsed.path

    # 1. Tenor (Discord default GIF platform)
    if hostname in ('tenor.com', 'www.tenor.com'):
        return bool(re.match(r'^/(?:view/[a-zA-Z0-9\-_%]+|b/[a-zA-Z0-9\-_%]+)', path))
    if hostname in ('media.tenor.com', 'c.tenor.com'):
        return path.lower().endswith(('.gif', '.mp4', '.webm', '.gifv')) or bool(re.match(r'^/m/[a-zA-Z0-9\-_%]+', path))

    # 2. Giphy
    if hostname in ('giphy.com', 'www.giphy.com'):
        return bool(re.match(r'^/(?:gifs|clips|media)/[a-zA-Z0-9\-_%]+', path))
    if hostname in ('media.giphy.com', 'i.giphy.com') or (hostname.endswith('.giphy.com') and bool(re.match(r'^media\d+\.giphy\.com$', hostname))):
        return path.lower().endswith(('.gif', '.mp4', '.webp', '.gifv')) or bool(re.match(r'^/media/[a-zA-Z0-9\-_%]+', path))

    # 3. Klipy (Discord GIF integration)
    if hostname in ('klipy.com', 'www.klipy.com'):
        return bool(re.match(r'^/(?:gifs?|clips?|memes?)/[a-zA-Z0-9\-_%]+', path))
    if hostname in ('media.klipy.com', 'static.klipy.com'):
        return path.lower().endswith(('.gif', '.mp4', '.webp', '.gifv'))

    # 4. Discord CDN / Media Proxy (attachments / emojis / stickers ending strictly in .gif/.gifv)
    if hostname in ('cdn.discordapp.com', 'media.discordapp.net'):
        if path.startswith(('/attachments/', '/emojis/', '/stickers/')):
            return path.lower().endswith(('.gif', '.gifv'))
        return False

    # 5. Imgur (Direct GIFs only, not albums or user profiles)
    if hostname == 'i.imgur.com':
        return bool(re.match(r'^/[a-zA-Z0-9]{4,12}\.(?:gif|gifv)$', path.lower()))

    return False


def scan_for_links(text: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Scans text for prohibited links, phishing, invites, IPs, and shorteners.
    Returns: (is_malicious, match_type, matched_string)
    Legitimate GIFs from verified platforms are allowed.
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

    # 3. Check for IP addresses (Game server ads, etc.)
    ip_match = IP_REGEX.search(text) or IP_REGEX.search(cleaned)
    if ip_match:
        return True, "IP-адрес / Реклама сервера", ip_match.group(0)

    # 4. Check for Shorteners
    short_match = SHORTENER_REGEX.search(text) or SHORTENER_REGEX.search(cleaned) or SHORTENER_REGEX.search(deobfuscated)
    if short_match:
        return True, "Сокращатель ссылок", short_match.group(0)

    # 5. Check for Any Link / URL (with GIF whitelist verification)
    # Check all URLs in original text only (avoids false-positives from homoglyph transliteration)
    for gen_match in GENERAL_URL_REGEX.finditer(text):
        matched_url = gen_match.group(0)
        if not is_valid_gif_url(matched_url):
            return True, "Сторонняя веб-ссылка / Реклама", matched_url

    return False, None, None


def levenshtein_similarity(s1: str, s2: str) -> float:
    """Fast length-bounded Levenshtein similarity ratio to prevent Event Loop DoS."""
    s1, s2 = s1.lower(), s2.lower()
    if s1 == s2:
        return 1.0
    if not s1 or not s2:
        return 0.0
    len1, len2 = len(s1), len(s2)
    max_len = max(len1, len2)
    # Fast pruning: если разница в длинах превышает 15%, схожесть не может быть >= 0.85
    if abs(len1 - len2) / max_len > 0.15:
        return 0.0
    # Защита от DoS: ограничиваем расчет первыми 250 символами
    if max_len > 250:
        s1 = s1[:250]
        s2 = s2[:250]
        len1, len2 = len(s1), len(s2)
        max_len = max(len1, len2)
    dp = list(range(len2 + 1))
    for i in range(1, len1 + 1):
        prev = dp[0]
        dp[0] = i
        c1 = s1[i - 1]
        for j in range(1, len2 + 1):
            temp = dp[j]
            cost = 0 if c1 == s2[j - 1] else 1
            dp[j] = min(dp[j] + 1, dp[j - 1] + 1, prev + cost)
            prev = temp
    return 1.0 - (dp[len2] / max_len)

