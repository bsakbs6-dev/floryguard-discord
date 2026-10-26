import urllib.parse
import re

def is_valid_gif_url(raw_url: str) -> bool:
    """
    Strictly validates if a URL is a legitimate GIF animation from trusted GIF providers.
    Prevents advertising bypasses (e.g. adding '.gif' to arbitrary links or query params).
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

test_cases = [
    ('https://tenor.com/view/happy-cat-dance-gif-12345678', True),
    ('https://media.tenor.com/abc123def/tenor.gif', True),
    ('https://c.tenor.com/abc123def/tenor.gif', True),
    ('https://tenor.com/b/someid', True),
    ('https://giphy.com/gifs/funny-cat-3oEdv0LJmPRUAg18u4', True),
    ('https://media.giphy.com/media/3oEdv0LJmPRUAg18u4/giphy.gif', True),
    ('https://klipy.com/gif/funny-moment-12345', True),
    ('https://media.klipy.com/media/xyz123.gif', True),
    ('https://cdn.discordapp.com/attachments/123/456/party.gif?ex=65e123&is=65ce23', True),
    ('https://media.discordapp.net/attachments/123/456/party.gif', True),
    ('https://i.imgur.com/abc1234.gif', True),
    ('https://mysite.com/test.gif', False),
    ('https://t.me/channel?.gif', False),
    ('https://discord.gg/invite?.gif', False),
    ('https://myshop.ru/buy.gif', False),
    ('https://mysite.com/page.html#.gif', False),
    ('https://tenor.com.evil.com/view/cat-123', False),
    ('https://tenor.com@evil.com/view/cat-123', False),
    ('https://cdn.discordapp.com/attachments/123/456/virus.exe', False),
    ('https://cdn.discordapp.com/attachments/123/456/virus.exe?.gif', False),
    ('https://tenor.com/', False),
    ('tenor.com', False),
    ('https://klipy.com/', False),
    ('https://tenor.com/view/cat-12345!', True),
]

all_passed = True
for url, expected in test_cases:
    res = is_valid_gif_url(url)
    if res != expected:
        print(f'FAIL: {url} -> {res} (expected {expected})')
        all_passed = False
if all_passed:
    print('ALL 24 TESTS PASSED!')
