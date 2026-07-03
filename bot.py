import os
import sqlite3
import random
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="*", intents=intents)

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
            await channel.send(f"Bem-vindo(a) ao servidor, {member.mention}! 🎉")


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    level, leveled_up = add_xp(message.guild.id, message.author.id, random.randint(5, 15))
    if leveled_up:
        await message.channel.send(f"🎉 {message.author.mention} subiu para o nível **{level}**!")
    await bot.process_commands(message)


# ---------- Utilidade ----------

@bot.tree.command(name="ping", description="Verifica a latência do bot")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)}ms")


@bot.tree.command(name="avatar", description="Mostra o avatar de um usuário")
@app_commands.describe(usuario="Usuário (opcional)")
async def avatar(interaction: discord.Interaction, usuario: discord.Member = None):
    usuario = usuario or interaction.user
    await interaction.response.send_message(usuario.display_avatar.url)


@bot.tree.command(name="userinfo", description="Mostra informações de um usuário")
@app_commands.describe(usuario="Usuário (opcional)")
async def userinfo(interaction: discord.Interaction, usuario: discord.Member = None):
    usuario = usuario or interaction.user
    embed = discord.Embed(title=f"Informações de {usuario.display_name}", color=discord.Color.blurple())
    embed.set_thumbnail(url=usuario.display_avatar.url)
    embed.add_field(name="ID", value=usuario.id)
    embed.add_field(name="Entrou em", value=usuario.joined_at.strftime("%d/%m/%Y") if usuario.joined_at else "N/A")
    embed.add_field(name="Conta criada em", value=usuario.created_at.strftime("%d/%m/%Y"))
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="serverinfo", description="Mostra informações do servidor")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild
    embed = discord.Embed(title=guild.name, color=discord.Color.green())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="Membros", value=guild.member_count)
    embed.add_field(name="Criado em", value=guild.created_at.strftime("%d/%m/%Y"))
    embed.add_field(name="Dono", value=str(guild.owner) if guild.owner else "N/A")
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="rank", description="Mostra seu nível e XP")
@app_commands.describe(usuario="Usuário (opcional)")
async def rank(interaction: discord.Interaction, usuario: discord.Member = None):
    usuario = usuario or interaction.user
    cur.execute("SELECT xp, level FROM levels WHERE guild_id=? AND user_id=?", (interaction.guild.id, usuario.id))
    row = cur.fetchone()
    xp, level = row if row else (0, 1)
    await interaction.response.send_message(f"{usuario.mention} está no nível **{level}** com **{xp}** XP.")


# ---------- Configuração ----------

@bot.tree.command(name="setwelcome", description="Define o canal de boas-vindas (admin)")
@app_commands.describe(canal="Canal para as mensagens de boas-vindas")
@app_commands.checks.has_permissions(administrator=True)
async def setwelcome(interaction: discord.Interaction, canal: discord.TextChannel):
    set_welcome_channel(interaction.guild.id, canal.id)
    await interaction.response.send_message(f"Canal de boas-vindas definido para {canal.mention}")


# ---------- Moderação ----------

@bot.tree.command(name="kick", description="Expulsa um membro (mod)")
@app_commands.describe(membro="Membro a expulsar", motivo="Motivo")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não especificado"):
    await membro.kick(reason=motivo)
    await interaction.response.send_message(f"{membro.mention} foi expulso. Motivo: {motivo}")


@bot.tree.command(name="ban", description="Bane um membro (mod)")
@app_commands.describe(membro="Membro a banir", motivo="Motivo")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, membro: discord.Member, motivo: str = "Não especificado"):
    await membro.ban(reason=motivo)
    await interaction.response.send_message(f"{membro.mention} foi banido. Motivo: {motivo}")


@bot.tree.command(name="mute", description="Silencia um membro por X minutos (mod)")
@app_commands.describe(membro="Membro a silenciar", minutos="Duração em minutos")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, membro: discord.Member, minutos: int):
    duracao = timedelta(minutes=minutos)
    await membro.timeout(duracao)
    await interaction.response.send_message(f"{membro.mention} foi silenciado por {minutos} minutos.")


@bot.tree.command(name="unmute", description="Remove o silenciamento de um membro (mod)")
@app_commands.describe(membro="Membro")
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, membro: discord.Member):
    await membro.timeout(None)
    await interaction.response.send_message(f"{membro.mention} não está mais silenciado.")


@bot.tree.command(name="clear", description="Limpa mensagens no canal (mod)")
@app_commands.describe(quantidade="Quantidade de mensagens a apagar (máx 100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, quantidade: app_commands.Range[int, 1, 100]):
    await interaction.response.defer(ephemeral=True)
    deleted = await interaction.channel.purge(limit=quantidade)
    await interaction.followup.send(f"{len(deleted)} mensagens apagadas.", ephemeral=True)


@bot.tree.command(name="warn", description="Adverte um membro (mod)")
@app_commands.describe(membro="Membro", motivo="Motivo")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(interaction: discord.Interaction, membro: discord.Member, motivo: str):
    try:
        await membro.send(f"⚠️ Você recebeu uma advertência em **{interaction.guild.name}**. Motivo: {motivo}")
    except discord.Forbidden:
        pass
    await interaction.response.send_message(f"{membro.mention} foi advertido. Motivo: {motivo}")


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
