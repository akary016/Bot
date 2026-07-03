import os
import io
import sqlite3
import random
from datetime import timedelta, datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

TOKEN = os.getenv("DISCORD_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_MODEL = "llama-3.3-70b-versatile"

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="*", intents=intents)

# ---------- Tema visual ----------
COLOR_PRIMARY = discord.Color.from_rgb(155, 89, 217)   # roxo
COLOR_SUCCESS = discord.Color.from_rgb(87, 242, 135)   # verde
COLOR_DANGER = discord.Color.from_rgb(237, 66, 69)     # vermelho
COLOR_GOLD = discord.Color.from_rgb(255, 200, 60)      # dourado (economia)
BOT_FOOTER = "⭐ Servidor Bot"

BG_TOP = (35, 20, 60)
BG_BOTTOM = (80, 40, 120)
ACCENT = (255, 200, 60)

DB_PATH = os.getenv("DB_PATH", "bot.db")
conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute(
    """
    CREATE TABLE IF NOT EXISTS levels (
        guild_id INTEGER,
        user_id INTEGER,
        xp INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        PRIMARY KEY (guild_id, user_id)
    )
    """
)
cur.execute(
    """
    CREATE TABLE IF NOT EXISTS config (
        guild_id INTEGER PRIMARY KEY,
        welcome_channel_id INTEGER
    )
    """
)
cur.execute(
    """
    CREATE TABLE IF NOT EXISTS economy (
        guild_id INTEGER,
        user_id INTEGER,
        balance INTEGER DEFAULT 0,
        last_daily TEXT,
        last_work TEXT,
        PRIMARY KEY (guild_id, user_id)
    )
    """
)
conn.commit()


def get_welcome_channel(guild_id: int):
    cur.execute("SELECT welcome_channel_id FROM config WHERE guild_id=?", (guild_id,))
    row = cur.fetchone()
    return row[0] if row else None


def set_welcome_channel(guild_id: int, channel_id: int):
    cur.execute(
        "INSERT INTO config (guild_id, welcome_channel_id) VALUES (?, ?) "
        "ON CONFLICT(guild_id) DO UPDATE SET welcome_channel_id=?",
        (guild_id, channel_id, channel_id),
    )
    conn.commit()


def add_xp(guild_id: int, user_id: int, amount: int):
    cur.execute("SELECT xp, level FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = cur.fetchone()
    if row is None:
        xp, level = 0, 1
        cur.execute("INSERT INTO levels (guild_id, user_id, xp, level) VALUES (?,?,?,?)", (guild_id, user_id, 0, 1))
    else:
        xp, level = row

    xp += amount
    leveled_up = False
    needed = level * 100
    while xp >= needed:
        xp -= needed
        level += 1
        needed = level * 100
        leveled_up = True

    cur.execute(
        "UPDATE levels SET xp=?, level=? WHERE guild_id=? AND user_id=?",
        (xp, level, guild_id, user_id),
    )
    conn.commit()
    return level, leveled_up


def get_level_info(guild_id: int, user_id: int):
    cur.execute("SELECT xp, level FROM levels WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    row = cur.fetchone()
    return row if row else (0, 1)


# ---------- Economia ----------

def _ensure_economy_row(guild_id: int, user_id: int):
    cur.execute("SELECT balance FROM economy WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    if cur.fetchone() is None:
        cur.execute(
            "INSERT INTO economy (guild_id, user_id, balance) VALUES (?, ?, 0)",
            (guild_id, user_id),
        )
        conn.commit()


def get_balance(guild_id: int, user_id: int) -> int:
    _ensure_economy_row(guild_id, user_id)
    cur.execute("SELECT balance FROM economy WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    return cur.fetchone()[0]


def add_balance(guild_id: int, user_id: int, amount: int):
    _ensure_economy_row(guild_id, user_id)
    cur.execute(
        "UPDATE economy SET balance = balance + ? WHERE guild_id=? AND user_id=?",
        (amount, guild_id, user_id),
    )
    conn.commit()


def get_cooldown(guild_id: int, user_id: int, column: str):
    _ensure_economy_row(guild_id, user_id)
    cur.execute(f"SELECT {column} FROM economy WHERE guild_id=? AND user_id=?", (guild_id, user_id))
    value = cur.fetchone()[0]
    return datetime.fromisoformat(value) if value else None


def set_cooldown(guild_id: int, user_id: int, column: str, when: datetime):
    cur.execute(
        f"UPDATE economy SET {column}=? WHERE guild_id=? AND user_id=?",
        (when.isoformat(), guild_id, user_id),
    )
    conn.commit()


def fmt_timedelta(td: timedelta) -> str:
    total = int(td.total_seconds())
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


async def ask_ia(prompt: str) -> str:
    if not GROQ_API_KEY:
        return "⚠️ IA não configurada (falta GROQ_API_KEY nas variáveis de ambiente)."
    url = "https://api.groq.com/openai/v1/chat/completions"
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
    payload = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 500,
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                data = await resp.json()
                if resp.status != 200:
                    erro = data.get("error", {}).get("message", "erro desconhecido")
                    return f"⚠️ Erro da IA: {erro}"
                escolhas = data.get("choices", [])
                if not escolhas:
                    return "⚠️ A IA não retornou nenhuma resposta."
                texto = escolhas[0].get("message", {}).get("content", "").strip()
                return texto or "⚠️ A IA não retornou nenhuma resposta."
    except Exception as e:
        return f"⚠️ Erro ao contatar a IA: {e}"


@bot.event
async def on_ready():
    await bot.tree.sync()
    print(f"Bot conectado como {bot.user}")


@bot.event
async def on_member_join(member: discord.Member):
    channel_id = get_welcome_channel(member.guild.id)
    if channel_id:
        channel = member.guild.get_channel(channel_id)
        if channel:
            embed = discord.Embed(
                title="🎉 Novo membro!",
                description=f"Bem-vindo(a) ao servidor, {member.mention}!",
                color=COLOR_PRIMARY,
            )
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.set_footer(text=BOT_FOOTER)
            await channel.send(embed=embed)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return

    if bot.user in message.mentions:
        pergunta = message.content
        for mention in (f"<@{bot.user.id}>", f"<@!{bot.user.id}>"):
            pergunta = pergunta.replace(mention, "")
        pergunta = pergunta.strip()
        if pergunta:
            async with message.channel.typing():
                resposta = await ask_ia(pergunta)
            for i in range(0, len(resposta), 2000):
                await message.reply(resposta[i:i + 2000], mention_author=False)

    level, leveled_up = add_xp(message.guild.id, message.author.id, random.randint(5, 15))
    if leveled_up:
        embed = discord.Embed(
            description=f"🎉 {message.author.mention} subiu para o nível **{level}**!",
            color=COLOR_GOLD,
        )
        await message.channel.send(embed=embed)
    await bot.process_commands(message)


# ---------- Utilidade ----------

@bot.tree.command(name="ping", description="Verifica a latência do bot")
async def ping(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🏓 Pong!",
        description=f"Latência: **{round(bot.latency * 1000)}ms**",
        color=COLOR_PRIMARY,
    )
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="avatar", description="Mostra o avatar de um usuário")
@app_commands.describe(usuario="Usuário (opcional)")
async def avatar(interaction: discord.Interaction, usuario: discord.Member = None):
    usuario = usuario or interaction.user
    embed = discord.Embed(title=f"Avatar de {usuario.display_name}", color=COLOR_PRIMARY)
    embed.set_image(url=usuario.display_avatar.url)
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="userinfo", description="Mostra informações de um usuário")
@app_commands.describe(usuario="Usuário (opcional)")
async def userinfo(interaction: discord.Interaction, usuario: discord.Member = None):
    usuario = usuario or interaction.user
    embed = discord.Embed(title=f"Informações de {usuario.display_name}", color=COLOR_PRIMARY)
    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.add_field(name="ID", value=usuario.id)
    embed.add_field(name="Entrou em", value=usuario.joined_at.strftime("%d/%m/%Y") if usuario.joined_at else "N/A")
    embed.add_field(name="Conta criada em", value=usuario.created_at.strftime("%d/%m/%Y"))
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="serverinfo", description="Mostra informações do servidor")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=guild.name, color=COLOR_PRIMARY)
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Membros", value=guild.member_count)
    embed.add_field(name="Criado em", value=guild.created_at.strftime("%d/%m/%Y"))
    embed.add_field(name="Dono", value=str(guild.owner) if guild.owner else "N/A")
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="rank", description="Mostra seu nível e XP")
@app_commands.describe(usuario="Usuário (opcional)")
async def rank(interaction: discord.Interaction, usuario: discord.Member = None):
    usuario = usuario or interaction.user
    xp, level = get_level_info(interaction.guild.id, usuario.id)
    needed = level * 100
    embed = discord.Embed(title=f"Rank de {usuario.display_name}", color=COLOR_GOLD)
    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.add_field(name="Nível", value=str(level))
    embed.add_field(name="XP", value=f"{xp}/{needed}")
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


# ---------- Economia ----------

@bot.tree.command(name="saldo", description="Mostra quantas moedas você (ou alguém) tem")
@app_commands.describe(usuario="Usuário (opcional)")
async def saldo(interaction: discord.Interaction, usuario: discord.Member = None):
    usuario = usuario or interaction.user
    balance = get_balance(interaction.guild.id, usuario.id)
    embed = discord.Embed(
        description=f"💰 {usuario.mention} tem **{balance}** moedas.",
        color=COLOR_GOLD,
    )
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="diario", description="Resgata sua recompensa diária de moedas")
async def diario(interaction: discord.Interaction):
    guild_id, user_id = interaction.guild.id, interaction.user.id
    last = get_cooldown(guild_id, user_id, "last_daily")
    now = datetime.now(timezone.utc)
    if last and now - last < timedelta(hours=24):
        restante = timedelta(hours=24) - (now - last)
        embed = discord.Embed(
            description=f"⏳ Você já pegou o diário hoje. Tenta de novo em **{fmt_timedelta(restante)}**.",
            color=COLOR_DANGER,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    reward = random.randint(100, 300)
    add_balance(guild_id, user_id, reward)
    set_cooldown(guild_id, user_id, "last_daily", now)
    embed = discord.Embed(
        description=f"🎁 Você resgatou **{reward}** moedas! Volte em 24h para resgatar de novo.",
        color=COLOR_GOLD,
    )
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


WORK_MESSAGES = [
    "Você entregou pizzas pela vizinhança",
    "Você ajudou um vizinho a mudar de casa",
    "Você vendeu doces na escola",
    "Você fez um bico programando",
    "Você lavou carros no estacionamento",
    "Você cuidou do jardim de alguém",
]


@bot.tree.command(name="trabalhar", description="Trabalhe para ganhar moedas (cooldown de 1h)")
async def trabalhar(interaction: discord.Interaction):
    guild_id, user_id = interaction.guild.id, interaction.user.id
    last = get_cooldown(guild_id, user_id, "last_work")
    now = datetime.now(timezone.utc)
    if last and now - last < timedelta(hours=1):
        restante = timedelta(hours=1) - (now - last)
        embed = discord.Embed(
            description=f"⏳ Você está cansado. Tenta de novo em **{fmt_timedelta(restante)}**.",
            color=COLOR_DANGER,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    reward = random.randint(50, 150)
    add_balance(guild_id, user_id, reward)
    set_cooldown(guild_id, user_id, "last_work", now)
    frase = random.choice(WORK_MESSAGES)
    embed = discord.Embed(
        description=f"💼 {frase} e ganhou **{reward}** moedas!",
        color=COLOR_GOLD,
    )
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="pagar", description="Transfere moedas para outro usuário")
@app_commands.describe(usuario="Quem vai receber", valor="Quantidade de moedas")
async def pagar(interaction: discord.Interaction, usuario: discord.Member, valor: app_commands.Range[int, 1, 1000000]):
    if usuario.id == interaction.user.id:
        await interaction.response.send_message("Você não pode pagar para si mesmo.", ephemeral=True)
        return
    saldo_atual = get_balance(interaction.guild.id, interaction.user.id)
    if saldo_atual < valor:
        embed = discord.Embed(description="❌ Você não tem moedas suficientes.", color=COLOR_DANGER)
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    add_balance(interaction.guild.id, interaction.user.id, -valor)
    add_balance(interaction.guild.id, usuario.id, valor)
    embed = discord.Embed(
        description=f"💸 {interaction.user.mention} transferiu **{valor}** moedas para {usuario.mention}.",
        color=COLOR_GOLD,
    )
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ranking-economia", description="Mostra os usuários mais ricos do servidor")
async def ranking_economia(interaction: discord.Interaction):
    cur.execute(
        "SELECT user_id, balance FROM economy WHERE guild_id=? ORDER BY balance DESC LIMIT 10",
        (interaction.guild.id,),
    )
    rows = cur.fetchall()
    if not rows:
        await interaction.response.send_message("Ainda não há dados de economia neste servidor.", ephemeral=True)
        return
    linhas = []
    medalhas = ["🥇", "🥈", "🥉"]
    for i, (user_id, balance) in enumerate(rows):
        membro = interaction.guild.get_member(user_id)
        nome = membro.display_name if membro else f"Usuário {user_id}"
        prefixo = medalhas[i] if i < 3 else f"{i + 1}."
        linhas.append(f"{prefixo} **{nome}** — {balance} moedas")
    embed = discord.Embed(title="💰 Ranking de moedas", description="\n".join(linhas), color=COLOR_GOLD)
    embed.set_footer(text=BOT_FOOTER)
    await interaction.response.send_message(embed=embed)


# ---------- Perfil personalizado (imagem) ----------

def _vertical_gradient(size, top_color, bottom_color):
    width, height = size
    base = Image.new("RGB", (1, height), color=0)
    top = Image.new("RGB", (1, 1), top_color)
    bottom = Image.new("RGB", (1, 1), bottom_color)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        r = round(top_color[0] * (1 - ratio) + bottom_color[0] * ratio)
        g = round(top_color[1] * (1 - ratio) + bottom_color[1] * ratio)
        b = round(top_color[2] * (1 - ratio) + bottom_color[2] * ratio)
        base.putpixel((0, y), (r, g, b))
    return base.resize((width, height))


def _load_font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


async def build_profile_card(member: discord.Member, level: int, xp: int, needed_xp: int, balance: int) -> io.BytesIO:
    W, H = 750, 260
    card = _vertical_gradient((W, H), BG_TOP, BG_BOTTOM)
    draw = ImageDraw.Draw(card)

    # avatar
    async with aiohttp.ClientSession() as session:
        async with session.get(str(member.display_avatar.replace(size=256).url)) as resp:
            avatar_bytes = await resp.read()
    avatar_img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((180, 180))
    mask = Image.new("L", (180, 180), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.ellipse((0, 0, 180, 180), fill=255)
    avatar_img = ImageOps.fit(avatar_img, (180, 180))
    circle_pos = (40, 40)
    ring = Image.new("RGBA", (196, 196), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse((0, 0, 196, 196), fill=ACCENT + (255,))
    card.paste(ring, (33, 33), ring)
    card.paste(avatar_img, circle_pos, mask)

    font_name = _load_font(38)
    font_sub = _load_font(24)
    font_small = _load_font(20)

    text_x = 250
    draw.text((text_x, 45), member.display_name, font=font_name, fill=(255, 255, 255))
    draw.text((text_x, 95), f"Nível {level}", font=font_sub, fill=ACCENT)
    draw.text((text_x, 130), f"💰 {balance} moedas", font=font_sub, fill=(255, 255, 255))

    # barra de XP
    bar_x, bar_y, bar_w, bar_h = text_x, 180, 440, 26
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=13, fill=(60, 40, 90))
    progress = min(xp / max(needed_xp, 1), 1)
    if progress > 0:
        draw.rounded_rectangle((bar_x, bar_y, bar_x + int(bar_w * progress), bar_y + bar_h), radius=13, fill=ACCENT)
    draw.text((bar_x, bar_y + bar_h + 6), f"XP: {xp}/{needed_xp}", font=font_small, fill=(230, 230, 230))

    buffer = io.BytesIO()
    card.convert("RGB").save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


@bot.tree.command(name="perfil", description="Mostra seu cartão de perfil personalizado")
@app_commands.describe(usuario="Usuário (opcional)")
async def perfil(interaction: discord.Interaction, usuario: discord.Member = None):
    await interaction.response.defer()
    usuario = usuario or interaction.user
    xp, level = get_level_info(interaction.guild.id, usuario.id)
    needed = level * 100
    balance = get_balance(interaction.guild.id, usuario.id)
    buffer = await build_profile_card(usuario, level, xp, needed, balance)
    file = discord.File(buffer, filename="perfil.png")
    await interaction.followup.send(file=file)


# ---------- Configuração ----------

@bot.tree.command(name="setwelcome", description="Define o canal de boas-vindas (admin)")
@app_commands.describe(canal="Canal para as mensagens de boas-vindas")
@app_commands.checks.has_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction, canal: discord.TextChannel):
    set_welcome_channel(interaction.guild.id, canal.id)
    embed = discord.Embed(description=f"✅ Canal de boas-vindas definido para {canal.mention}", color=COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed)


# ---------- Moderação ----------

@bot.tree.command(name="kick", description="Expulsa um membro (mod)")
@app_commands.describe(membro="Membro a expulsar", motivo="Motivo")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não especificado"):
    await membro.kick(reason=motivo)
    embed = discord.Embed(description=f"👋 {membro.mention} foi expulso. Motivo: {motivo}", color=COLOR_DANGER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ban", description="Bane um membro (mod)")
@app_commands.describe(membro="Membro a banir", motivo="Motivo")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não especificado"):
    await membro.ban(reason=motivo)
    embed = discord.Embed(description=f"🔨 {membro.mention} foi banido. Motivo: {motivo}", color=COLOR_DANGER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="mute", description="Silencia um membro por X minutos (mod)")
@app_commands.describe(membro="Membro a silenciar", minutos="Duração em minutos")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, membro: discord.Member, minutos: int):
    duracao = timedelta(minutes=minutos)
    await membro.timeout(duracao)
    embed = discord.Embed(description=f"🔇 {membro.mention} foi silenciado por {minutos} minutos.", color=COLOR_DANGER)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="unmute", description="Remove o silenciamento de um membro (mod)")
@app_commands.describe(membro="Membro")
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, membro: discord.Member):
    await membro.timeout(None)
    embed = discord.Embed(description=f"🔊 {membro.mention} não está mais silenciado.", color=COLOR_SUCCESS)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="clear", description="Limpa mensagens no canal (mod)")
@app_commands.describe(quantidade="Quantidade de mensagens a apagar (máx 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=quantidade)
    await interaction.followup.send(f"🧹 {len(deleted)} mensagens apagadas.", ephemeral=True)


@bot.tree.command(name="warn", description="Adverte um membro (mod)")
@app_commands.describe(membro="Membro", motivo="Motivo")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, membro: discord.Member, motivo: str):
    try:
        await membro.send(f"⚠️ Você recebeu uma advertência em **{interaction.guild.name}**. Motivo: {motivo}")
    except discord.Forbidden:
        pass
    embed = discord.Embed(description=f"⚠️ {membro.mention} foi advertido. Motivo: {motivo}", color=COLOR_DANGER)
    await interaction.response.send_message(embed=embed)


# ---------- Tratamento de erros de permissão ----------

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("Você não tem permissão para usar esse comando.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Ocorreu um erro: {error}", ephemeral=True)
        raise error


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Defina a variável de ambiente DISCORD_TOKEN antes de rodar o bot.")
    bot.run(TOKEN)
