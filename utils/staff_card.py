import io
from pathlib import Path
from typing import Optional
from PIL import Image, ImageDraw, ImageFont, ImageFilter

FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"


def _load_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Loads bundled Cyrillic TrueType font from assets/fonts first, then system fallbacks."""
    bundled_bold = str(FONTS_DIR / "segoeuib.ttf")
    bundled_reg = str(FONTS_DIR / "segoeui.ttf")

    candidates = (
        [
            bundled_bold,
            "C:/Windows/Fonts/segoeuib.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        ]
        if bold
        else [
            bundled_reg,
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/seguisb.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        ]
    )
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _format_online_time(seconds: int) -> str:
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours} ч. {minutes} мин."


def _draw_rounded_gradient_rect(
    width: int,
    height: int,
    radius: int,
    color_top: tuple[int, int, int],
    color_bottom: tuple[int, int, int],
) -> Image.Image:
    """Creates a rounded rectangle RGBA image with a vertical linear gradient."""
    grad = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    pix = grad.load()
    for y in range(height):
        t = y / max(1, height - 1)
        r = int(color_top[0] * (1 - t) + color_bottom[0] * t)
        g = int(color_top[1] * (1 - t) + color_bottom[1] * t)
        b = int(color_top[2] * (1 - t) + color_bottom[2] * t)
        for x in range(width):
            pix[x, y] = (r, g, b, 255)

    # 2x supersampled mask for smooth rounded corners
    mask_2x = Image.new("L", (width * 2, height * 2), 0)
    ImageDraw.Draw(mask_2x).rounded_rectangle(
        (0, 0, width * 2 - 1, height * 2 - 1),
        radius=radius * 2,
        fill=255,
    )
    mask = mask_2x.resize((width, height), Image.Resampling.LANCZOS)
    grad.putalpha(mask)
    return grad


def _draw_icon(draw: ImageDraw.ImageDraw, icon_type: str, x: int, y: int, color: tuple[int, int, int, int]):
    """Draws clean vector icons for the 4 dark stat cards."""
    if icon_type == "shield":
        pts = [
            (x + 14, y),
            (x + 28, y + 5),
            (x + 28, y + 16),
            (x + 14, y + 30),
            (x, y + 16),
            (x, y + 5),
        ]
        draw.polygon(pts, fill=color)
        draw.line([(x + 14, y + 8), (x + 14, y + 17)], fill=(26, 22, 18, 255), width=3)
        draw.ellipse((x + 12, y + 20, x + 16, y + 24), fill=(26, 22, 18, 255))

    elif icon_type == "calendar":
        # Clean vector calendar icon
        draw.line([(x + 7, y + 2), (x + 7, y + 7)], fill=color, width=3)
        draw.line([(x + 21, y + 2), (x + 21, y + 7)], fill=color, width=3)
        draw.rounded_rectangle((x + 2, y + 5, x + 26, y + 29), radius=4, fill=(28, 24, 20, 255), outline=color, width=2)
        # Top accent bar
        draw.rectangle((x + 3, y + 5, x + 25, y + 12), fill=color)
        # Grid dots
        for row in range(2):
            for col in range(3):
                px = x + 6 + col * 6
                py = y + 16 + row * 5
                draw.rectangle((px, py, px + 2, py + 2), fill=color)

    elif icon_type == "gavel":
        draw.line([(x + 6, y + 24), (x + 22, y + 8)], fill=color, width=4)
        head_pts = [(x + 14, y + 2), (x + 26, y + 14), (x + 22, y + 18), (x + 10, y + 6)]
        draw.polygon(head_pts, fill=color)
        draw.rounded_rectangle((x, y + 26, x + 18, y + 30), radius=2, fill=color)

    elif icon_type == "report":
        draw.rounded_rectangle((x + 2, y, x + 26, y + 30), radius=4, fill=color)
        bg_col = (28, 24, 20, 255)
        draw.line([(x + 7, y + 10), (x + 21, y + 10)], fill=bg_col, width=2)
        draw.line([(x + 7, y + 16), (x + 21, y + 16)], fill=bg_col, width=2)
        draw.line([(x + 7, y + 22), (x + 15, y + 22)], fill=bg_col, width=2)

    elif icon_type == "mute":
        draw.rounded_rectangle((x + 9, y + 2, x + 19, y + 17), radius=5, fill=color)
        draw.arc((x + 5, y + 7, x + 23, y + 23), start=0, end=180, fill=color, width=3)
        draw.line([(x + 14, y + 23), (x + 14, y + 29)], fill=color, width=3)
        draw.line([(x + 9, y + 29), (x + 19, y + 29)], fill=color, width=3)
        draw.line([(x + 3, y + 3), (x + 25, y + 27)], fill=(255, 165, 0, 230), width=3)


def _draw_watermark_status(card: Image.Image, available: bool):
    """Draws a subtle decorative circle and check (✔) or cross (✖) on the right side of a dark stat card."""
    overlay = Image.new("RGBA", card.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    w, h = card.size
    cx, cy = w - 65, h // 2 + 6

    r = 58
    if available:
        odraw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 180, 40, 14))
        cut_col = (26, 22, 19, 255)
        odraw.line([(cx - 22, cy + 2), (cx - 6, cy + 18), (cx + 24, cy - 16)], fill=cut_col, width=10)
    else:
        odraw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, 10))
        cut_col = (26, 22, 19, 255)
        odraw.line([(cx - 20, cy - 20), (cx + 20, cy + 20)], fill=cut_col, width=10)
        odraw.line([(cx + 20, cy - 20), (cx - 20, cy + 20)], fill=cut_col, width=10)

    card.alpha_composite(overlay)


def generate_staff_profile_card(
    username: str,
    rank_name: str,
    avatar_bytes: Optional[bytes],
    active_warns: int,
    max_warns: int,
    online_seconds: int = 0,
    norm_seconds: int = 14400,
    days_count: int = 1,
    bans_issued: Optional[int] = 0,
    reports_accepted: Optional[int] = 0,
    mutes_issued: Optional[int] = 0,
    is_staff: bool = True,
    is_trainee: bool = False,
    warns_issued: Optional[int] = None,
) -> io.BytesIO:
    """
    Generates a high-resolution FloryMine yellow-orange themed Staff Profile Card PNG.
    Returns a BytesIO buffer ready to be sent as a Discord File.
    """
    W, H = 1280, 720
    base = Image.new("RGBA", (W, H), (14, 10, 7, 255))

    # 1. Ambient orange-gold radial glow on background
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    gdraw.ellipse((680, 60, 1340, 720), fill=(255, 145, 0, 58))
    gdraw.ellipse((-120, -100, 520, 420), fill=(255, 175, 0, 38))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=95))
    base.alpha_composite(glow)

    # Fonts
    font_label = _load_font(22, bold=False)
    font_big_pct = _load_font(68, bold=True)
    font_online_sub = _load_font(20, bold=False)
    font_online_val = _load_font(27, bold=True)
    font_stat_label = _load_font(21, bold=False)
    font_stat_val = _load_font(34, bold=True)

    # Layout constants for Left Grid (2 columns x 3 rows)
    col1_x = 34
    col2_x = 398
    card_w = 342
    top_card_h = 210
    stat_card_h = 198
    row1_y = 34
    row2_y = 266
    row3_y = 486
    radius = 20

    # =========================================================================
    # CARD 1: АКТИВНОСТЬ (Top-Left, Vibrant Yellow-Orange Gradient)
    # =========================================================================
    act_card = _draw_rounded_gradient_rect(
        card_w, top_card_h, radius,
        color_top=(255, 180, 18),     # Warm Gold-Yellow
        color_bottom=(235, 110, 0),   # Vibrant FloryMine Orange
    )

    wm = Image.new("RGBA", (card_w, top_card_h), (0, 0, 0, 0))
    wmd = ImageDraw.Draw(wm)
    wmd.ellipse((card_w - 150, 35, card_w + 35, 250), fill=(150, 58, 0, 52))
    wmd.line([(card_w - 62, 115), (card_w - 30, 210)], fill=(245, 145, 10, 95), width=9)
    wm.putalpha(Image.composite(wm.getchannel("A"), Image.new("L", (card_w, top_card_h), 0), act_card.getchannel("A")))
    act_card.alpha_composite(wm)

    act_draw = ImageDraw.Draw(act_card)
    if norm_seconds > 0:
        pct = int(round((online_seconds / norm_seconds) * 100))
    else:
        pct = 100 if online_seconds > 0 else 0

    pct_text = f"{pct}%"
    pct_font = font_big_pct if len(pct_text) <= 5 else _load_font(52, bold=True)

    act_draw.text((26, 22), "Активность", font=font_label, fill=(255, 245, 220, 235))
    act_draw.text((24, 56), pct_text, font=pct_font, fill=(255, 255, 255, 255))
    act_draw.text((26, 142), "Онлайн", font=font_online_sub, fill=(255, 240, 200, 220))
    act_draw.text((26, 165), _format_online_time(online_seconds), font=font_online_val, fill=(255, 255, 255, 255))

    base.alpha_composite(act_card, (col1_x, row1_y))

    # =========================================================================
    # CARD 2: ВЫГОВОРЫ (Top-Right of Left Grid, Golden-Orange / Amber Gradient)
    # =========================================================================
    has_warns = is_staff and active_warns > 0
    warn_top_col = (255, 155, 15) if not has_warns else (245, 95, 25)
    warn_bot_col = (220, 95, 0) if not has_warns else (195, 40, 15)

    warn_card = _draw_rounded_gradient_rect(
        card_w, top_card_h, radius,
        color_top=warn_top_col,
        color_bottom=warn_bot_col,
    )
    wm2 = Image.new("RGBA", (card_w, top_card_h), (0, 0, 0, 0))
    wmd2 = ImageDraw.Draw(wm2)
    wmd2.ellipse((card_w - 135, 50, card_w + 25, 210), fill=(120, 40, 0, 45))
    if not is_staff:
        wcx, wcy = card_w - 55, 130
        wmd2.line([(wcx - 20, wcy - 20), (wcx + 20, wcy + 20)], fill=(235, 115, 10, 150), width=10)
        wmd2.line([(wcx + 20, wcy - 20), (wcx - 20, wcy + 20)], fill=(235, 115, 10, 150), width=10)
    wm2.putalpha(Image.composite(wm2.getchannel("A"), Image.new("L", (card_w, top_card_h), 0), warn_card.getchannel("A")))
    warn_card.alpha_composite(wm2)

    w_draw = ImageDraw.Draw(warn_card)
    w_draw.text((26, 22), "Выговоры", font=font_label, fill=(255, 245, 220, 235))
    if is_staff:
        w_draw.text((26, 66), f"{active_warns}/{max_warns}", font=font_big_pct, fill=(255, 255, 255, 255))
        limit_note = "Лимит стажера: 1 выговор" if max_warns == 1 else f"Лимит должности: {max_warns}"
        w_draw.text((26, 162), limit_note, font=font_online_sub, fill=(255, 240, 200, 225))
    else:
        font_warn_unavail = _load_font(38, bold=True)
        w_draw.text((26, 82), "Недоступно", font=font_warn_unavail, fill=(255, 255, 255, 255))
        w_draw.text((26, 162), "Нет должности модерации", font=font_online_sub, fill=(255, 240, 200, 215))

    base.alpha_composite(warn_card, (col2_x, row1_y))

    # =========================================================================
    # CARDS 3..6: 4 DARK STAT CARDS (2x2 Grid)
    # =========================================================================
    font_stat_unavail = _load_font(28, bold=True)

    # Permissions check: Trainees do NOT have access to bans and reports!
    can_issue_bans = is_staff and (not is_trainee)
    can_handle_reports = is_staff and (not is_trainee)
    can_issue_mutes = is_staff

    first_card_label = "Дней в стаффе" if is_staff else "На сервере"
    first_card_val = f"{max(1, days_count)} дн."

    stat_items = [
        (col1_x, row2_y, "calendar", first_card_label, first_card_val, True),
        (col2_x, row2_y, "gavel", "Выдано банов", str(int(bans_issued or 0)) if can_issue_bans else None, can_issue_bans),
        (col1_x, row3_y, "report", "Принято репортов", str(int(reports_accepted or 0)) if can_handle_reports else None, can_handle_reports),
        (col2_x, row3_y, "mute", "Выдано мутов", str(int(mutes_issued or 0)) if can_issue_mutes else None, can_issue_mutes),
    ]

    for sx, sy, icon_name, label_text, display_val, available in stat_items:
        scard = Image.new("RGBA", (card_w, stat_card_h), (0, 0, 0, 0))
        sdraw = ImageDraw.Draw(scard)
        sdraw.rounded_rectangle(
            (0, 0, card_w - 1, stat_card_h - 1),
            radius=radius,
            fill=(26, 22, 19, 245),
            outline=(255, 165, 20, 32),
            width=1,
        )

        _draw_watermark_status(scard, available)

        icon_col = (255, 180, 40, 220)
        _draw_icon(sdraw, icon_name, 26, 26, icon_col)

        sdraw.text((26, 98), label_text, font=font_stat_label, fill=(165, 156, 145, 255))
        if available and display_val is not None:
            sdraw.text((26, 130), str(display_val), font=font_stat_val, fill=(255, 255, 255, 255))
        else:
            sdraw.text((26, 134), "Недоступно", font=font_stat_unavail, fill=(235, 230, 225, 245))

        base.alpha_composite(scard, (sx, sy))

    # =========================================================================
    # RIGHT SIDE: 2X SUPERSAMPLED TILTED STAFF ID BADGE & LANYARD STRAPS
    # =========================================================================
    lanyard_2x = Image.new("RGBA", (W * 2, H * 2), (0, 0, 0, 0))
    ldraw = ImageDraw.Draw(lanyard_2x)
    ldraw.arc((910 * 2, -140 * 2, 1420 * 2, 210 * 2), start=115, end=245, fill=(255, 165, 10, 235), width=34)
    ldraw.arc((945 * 2, -90 * 2, 1380 * 2, 185 * 2), start=105, end=255, fill=(235, 120, 0, 220), width=26)
    base.alpha_composite(lanyard_2x.resize((W, H), Image.Resampling.LANCZOS))

    # Render Badge at 2x resolution so rotation is completely anti-aliased
    scale = 2
    bw, bh = 410 * scale, 540 * scale
    pad = 90 * scale
    badge_canvas = Image.new("RGBA", (bw + pad * 2, bh + pad * 2), (0, 0, 0, 0))

    badge_img = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    bdraw = ImageDraw.Draw(badge_img)
    bdraw.rounded_rectangle(
        (0, 0, bw - 1, bh - 1),
        radius=26 * scale,
        fill=(22, 17, 12, 248),
    )

    # Warm radial glow behind avatar inside the badge
    bglow = Image.new("RGBA", (bw, bh), (0, 0, 0, 0))
    ImageDraw.Draw(bglow).ellipse((45 * scale, 55 * scale, bw - 45 * scale, 385 * scale), fill=(255, 155, 15, 82))
    bglow = bglow.filter(ImageFilter.GaussianBlur(radius=42 * scale))
    badge_img.alpha_composite(bglow)

    # Clip to rounded rect and draw crisp border
    bmask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(bmask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius=26 * scale, fill=255)
    badge_img.putalpha(bmask)

    bdraw = ImageDraw.Draw(badge_img)
    bdraw.rounded_rectangle(
        (2, 2, bw - 3, bh - 3),
        radius=26 * scale,
        outline=(255, 170, 30, 58),
        width=3,
    )

    # Top gold grommet ring
    gx, gy = bw // 2, 36 * scale
    bdraw.ellipse((gx - 15 * scale, gy - 15 * scale, gx + 15 * scale, gy + 15 * scale), outline=(255, 175, 20, 230), width=5)
    bdraw.ellipse((gx - 9 * scale, gy - 9 * scale, gx + 9 * scale, gy + 9 * scale), fill=(245, 145, 10, 215))

    # Circular Avatar
    av_size = 224 * scale
    av_x = (bw - av_size) // 2
    av_y = 84 * scale

    if avatar_bytes:
        try:
            av_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((av_size, av_size), Image.Resampling.LANCZOS)
        except Exception:
            av_img = _draw_rounded_gradient_rect(av_size, av_size, av_size // 2, (255, 170, 20), (180, 70, 0))
    else:
        av_img = _draw_rounded_gradient_rect(av_size, av_size, av_size // 2, (255, 170, 20), (180, 70, 0))

    av_mask = Image.new("L", (av_size, av_size), 0)
    ImageDraw.Draw(av_mask).ellipse((0, 0, av_size - 1, av_size - 1), fill=255)
    badge_img.paste(av_img, (av_x, av_y), av_mask)

    # Rank Name (Centered below avatar)
    rank_font_size = (26 if len(rank_name) <= 16 else (21 if len(rank_name) <= 24 else 18)) * scale
    font_rank = _load_font(rank_font_size, bold=False)
    r_bbox = bdraw.textbbox((0, 0), rank_name, font=font_rank)
    r_w = r_bbox[2] - r_bbox[0]
    bdraw.text(
        ((bw - r_w) // 2, 342 * scale),
        rank_name,
        font=font_rank,
        fill=(255, 188, 65, 235),
    )

    # Username / Minecraft Nick (Centered below rank)
    nick_display = username if len(username) <= 20 else username[:18] + ".."
    nick_font_size = (34 if len(nick_display) <= 14 else 27) * scale
    font_nick = _load_font(nick_font_size, bold=True)
    n_bbox = bdraw.textbbox((0, 0), nick_display, font=font_nick)
    n_w = n_bbox[2] - n_bbox[0]
    bdraw.text(
        ((bw - n_w) // 2, 382 * scale),
        nick_display,
        font=font_nick,
        fill=(255, 255, 255, 255),
    )

    badge_canvas.alpha_composite(badge_img, (pad, pad))
    rotated_2x = badge_canvas.rotate(-10, resample=Image.Resampling.BICUBIC, expand=False)
    rotated_1x = rotated_2x.resize(
        (badge_canvas.width // scale, badge_canvas.height // scale),
        Image.Resampling.LANCZOS,
    )
    base.alpha_composite(rotated_1x, (685, 45))

    # Export to PNG buffer
    out = io.BytesIO()
    base.convert("RGB").save(out, format="PNG", optimize=True)
    out.seek(0)
    return out
