import re
import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

from config import UNAUTHORIZED_MESSAGE, DEFAULT_POST_AVATAR_PATH
from core.permissions import (
    is_authorized_guild,
    is_admin,
    has_warn_access,
)
from utils.embeds import (
    create_security_embed,
    COLOR_FLORY_ORANGE,
    COLOR_FLORY_GOLD,
    COLOR_DANGER,
    COLOR_SUCCESS,
    COLOR_PRIMARY,
    COLOR_DARK,
)
from utils.logger import logger


COLOR_PRESETS = {
    "orange": COLOR_FLORY_ORANGE,
    "gold": COLOR_FLORY_GOLD,
    "red": COLOR_DANGER,
    "green": COLOR_SUCCESS,
    "blue": COLOR_PRIMARY,
    "dark": COLOR_DARK,
    "purple": 0x9B59B6,
    "white": 0xFFFFFF,
}


def _parse_hex_color(raw_hex: str, fallback: int) -> int:
    if not raw_hex or not raw_hex.strip():
        return fallback
    cleaned = raw_hex.strip().lstrip("#")
    if re.match(r"^[0-9a-fA-F]{6}$", cleaned):
        return int(cleaned, 16)
    return fallback


class PostComposerModal(discord.ui.Modal, title="⚡ Создание поста • FloryMine"):
    post_title = discord.ui.TextInput(
        label="Заголовок поста",
        placeholder="Например: ⚡ Обновление сервера FloryMine!",
        required=True,
        max_length=256,
    )

    post_content = discord.ui.TextInput(
        label="Текст поста (поддерживается Markdown)",
        style=discord.TextStyle.paragraph,
        placeholder="Введите текст объявления или новости...",
        required=True,
        max_length=4000,
    )

    custom_hex = discord.ui.TextInput(
        label="Свой HEX-цвет (необязательно)",
        placeholder="Например: #FFA500 (оставьте пустым для выбранного цвета)",
        required=False,
        max_length=7,
    )

    def __init__(
        self,
        bot,
        target_channel: discord.TextChannel,
        base_color: int,
        sender_name: str,
        avatar_attachment: Optional[discord.Attachment] = None,
        avatar_url: Optional[str] = None,
        post_image: Optional[discord.Attachment] = None,
        mention_text: Optional[str] = None,
    ):
        super().__init__()
        self.bot = bot
        self.target_channel = target_channel
        self.base_color = base_color
        self.sender_name = sender_name
        self.avatar_attachment = avatar_attachment
        self.avatar_url = avatar_url
        self.post_image = post_image
        self.mention_text = mention_text

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        final_color = _parse_hex_color(self.custom_hex.value, self.base_color)
        embed = discord.Embed(
            title=self.post_title.value.strip(),
            description=self.post_content.value,
            color=final_color,
        )

        if self.post_image:
            embed.set_image(url=self.post_image.url)

        # Determine avatar URL or default local avatar bytes
        chosen_avatar_url = None
        if self.avatar_attachment:
            chosen_avatar_url = self.avatar_attachment.url
        elif self.avatar_url and self.avatar_url.strip().startswith(("http://", "https://")):
            chosen_avatar_url = self.avatar_url.strip()

        default_avatar_bytes = None
        if DEFAULT_POST_AVATAR_PATH.exists():
            try:
                default_avatar_bytes = DEFAULT_POST_AVATAR_PATH.read_bytes()
            except Exception as e:
                logger.warning(f"Failed to read default post avatar: {e}")

        # Send via Webhook so custom sender_name and avatar work seamlessly
        sent_ok = False
        if self.target_channel.permissions_for(interaction.guild.me).manage_webhooks:
            try:
                webhooks = await self.target_channel.webhooks()
                bot_webhook = next(
                    (wh for wh in webhooks if wh.user and wh.user.id == self.bot.user.id and wh.name == "FloryMine Publisher"),
                    None,
                )
                if not bot_webhook:
                    bot_webhook = await self.target_channel.create_webhook(
                        name="FloryMine Publisher",
                        avatar=default_avatar_bytes,
                        reason=f"FloryGuard Post System by {interaction.user}",
                    )
                elif not chosen_avatar_url and bot_webhook.avatar is None and default_avatar_bytes:
                    try:
                        await bot_webhook.edit(avatar=default_avatar_bytes)
                    except Exception:
                        pass

                # Whitelist this webhook ID in AutoMod so links/mentions in official posts are never blocked
                if hasattr(self.bot, "trusted_webhook_ids"):
                    self.bot.trusted_webhook_ids.add(bot_webhook.id)

                send_kwargs = {
                    "username": self.sender_name,
                    "embed": embed,
                    "wait": True,
                }
                if self.mention_text:
                    send_kwargs["content"] = self.mention_text
                if chosen_avatar_url:
                    send_kwargs["avatar_url"] = chosen_avatar_url

                await bot_webhook.send(**send_kwargs)
                sent_ok = True
            except Exception as e:
                logger.error(f"Webhook post send failed, falling back to direct send: {e}")

        # Fallback: direct bot message if Manage Webhooks is missing
        if not sent_ok:
            try:
                files = []
                if chosen_avatar_url:
                    embed.set_author(name=self.sender_name, icon_url=chosen_avatar_url)
                elif DEFAULT_POST_AVATAR_PATH.exists():
                    file = discord.File(str(DEFAULT_POST_AVATAR_PATH), filename="florymine_avatar.jpg")
                    files.append(file)
                    embed.set_author(name=self.sender_name, icon_url="attachment://florymine_avatar.jpg")
                else:
                    embed.set_author(name=self.sender_name)

                await self.target_channel.send(
                    content=self.mention_text or None,
                    embed=embed,
                    files=files if files else discord.utils.MISSING,
                )
                sent_ok = True
            except Exception as e:
                await interaction.followup.send(f"❌ Ошибка при отправке поста: {e}", ephemeral=True)
                return

        confirm_embed = create_security_embed(
            title="✅ ПОСТ УСПЕШНО ОПУБЛИКОВАН",
            description=(
                f"📢 **Канал:** {self.target_channel.mention}\n"
                f"👤 **Отправитель:** `{self.sender_name}`\n"
                f"📌 **Заголовок:** `{self.post_title.value.strip()}`"
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=confirm_embed, ephemeral=True)


class PostsCog(commands.Cog, name="Posts"):
    """
    ⚡ Custom Post Publisher for FloryMine:
    Allows publishing rich announcements with custom sender name, custom avatar (default: FM FloryMine gold logo),
    custom title, and selectable embed colors.
    """
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="post",
        description="📢 Отправить оформленный пост с выбором заголовка, цвета, имени и аватарки бота"
    )
    @app_commands.describe(
        channel="Канал для публикации (по умолчанию: текущий канал)",
        color="Цвет боковой полоски поста (по умолчанию: Фирменный Оранжевый FloryMine)",
        sender_name="Имя, от которого бот напишет пост (по умолчанию: FloryMine)",
        avatar="Загрузить фото для аватарки отправителя (по умолчанию: золотая аватарка FM FloryMine)",
        avatar_url="Или ссылка (URL) на картинку для аватарки отправителя",
        image="Прикрепить большую картинку/баннер внутрь самого поста (необязательно)",
        mention="Упоминание над постом (например: @everyone или роль, необязательно)",
    )
    @app_commands.choices(
        color=[
            app_commands.Choice(name="🟧 Фирменный Оранжевый (FloryMine)", value="orange"),
            app_commands.Choice(name="🟨 Золотой (FloryMine Gold)", value="gold"),
            app_commands.Choice(name="🟥 Красный (Важное / Внимание)", value="red"),
            app_commands.Choice(name="🟩 Зелёный (Успех / Открытие)", value="green"),
            app_commands.Choice(name="🟦 Синий (Информация)", value="blue"),
            app_commands.Choice(name="⬛ Тёмный (Строгий стиль)", value="dark"),
            app_commands.Choice(name="🟪 Фиолетовый", value="purple"),
            app_commands.Choice(name="⬜ Белый", value="white"),
        ]
    )
    async def post_command(
        self,
        interaction: discord.Interaction,
        channel: Optional[discord.TextChannel] = None,
        color: Optional[str] = "orange",
        sender_name: Optional[str] = "FloryMine",
        avatar: Optional[discord.Attachment] = None,
        avatar_url: Optional[str] = None,
        image: Optional[discord.Attachment] = None,
        mention: Optional[str] = None,
    ):
        if not is_authorized_guild(interaction.guild):
            await interaction.response.send_message(f"⚠️ {UNAUTHORIZED_MESSAGE}", ephemeral=True)
            return

        # Allow Security Admins, Senior Admins, Owners, or Staff Curators
        has_access = await is_admin(interaction.guild.id, interaction.user.id) or (
            isinstance(interaction.user, discord.Member) and has_warn_access(interaction.user)
        )
        if not has_access:
            await interaction.response.send_message(
                "❌ У вас нет прав для публикации официальных постов.",
                ephemeral=True,
            )
            return

        target_channel = channel or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message(
                "❌ Посты можно отправлять только в текстовые каналы.",
                ephemeral=True,
            )
            return

        chosen_color = COLOR_PRESETS.get(color or "orange", COLOR_FLORY_ORANGE)
        clean_sender = (sender_name or "FloryMine").strip()[:80] or "FloryMine"

        modal = PostComposerModal(
            bot=self.bot,
            target_channel=target_channel,
            base_color=chosen_color,
            sender_name=clean_sender,
            avatar_attachment=avatar,
            avatar_url=avatar_url,
            post_image=image,
            mention_text=mention.strip() if mention else None,
        )
        await interaction.response.send_modal(modal)


async def setup(bot: commands.Bot):
    await bot.add_cog(PostsCog(bot))
