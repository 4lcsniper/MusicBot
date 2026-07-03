import os
import json
import base64
import discord
from discord import app_commands
from discord.ext import commands
import aiohttp
from flask import Flask
from threading import Thread

# Fetch environment variables from Render's dashboard setup
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

try:
    OWNER_ID = int(os.getenv("OWNER_ID", 0))
except (ValueError, TypeError):
    OWNER_ID = 0

# --- DATA PERSISTENCE FOR RENDER ---
DATA_FILE = "bot_data.json"

def load_data():
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                data = json.load(f)
                allowed = set(int(uid) for uid in data.get("allowed_users", []))
                allowed.add(OWNER_ID)
                return allowed, data.get("usage_count", 0)
        except Exception:
            return {OWNER_ID}, 0
    return {OWNER_ID}, 0

def save_data():
    with open(DATA_FILE, "w") as f:
        json.dump({
            "allowed_users": list(allowed_users),
            "usage_count": usage_count
        }, f)

allowed_users, usage_count = load_data()

# --- DISCORD SETUP ---
intents = discord.Intents.default()
intents.message_content = True

class MusicBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await self.tree.sync()
        print("Slash commands synced globally.")

bot = MusicBot()

# --- FLASK BACKGROUND LAYER FOR RENDER ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_webserver():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

# --- OPENROUTER CONNECTION ---
async def get_ai_answer(text_content, image_bytes=None):
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }
    
    system_instruction = (
        "You are a strict music theory solver. Output ONLY the direct answer "
        "to the question. Do not include labels, conversational text, markdown "
        "formatting like bolding, explanations, or introductory text. Just the raw text answer."
    )
    
    messages_content = [
        {
            "type": "text", 
            "text": text_content if text_content else "Solve the music theory question in this image."
        }
    ]
    
    if image_bytes:
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        messages_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{base64_image}"
            }
        })

    payload = {
        "model": "openrouter/free",  # Free tier fallback router
        "messages": [
            {"role": "system", "content": system_instruction},
            {"role": "user", "content": messages_content}
        ]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 200:
                    result = await resp.json()
                    return result['choices'][0]['message']['content'].strip()
                else:
                    return f"Error: API returned status {resp.status}"
    except Exception as e:
        return f"Error processing request: {e}"

@bot.event
async def on_ready():
    print(f'Bot logged in as {bot.user}')

# --- MODERN SLASH COMMANDS ---

@bot.tree.command(name="add-user", description="Authorize a user to access the bot (Owner Only).")
@app_commands.describe(user="Select the Discord user to authorize")
async def add_user(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("ERROR: You do not have permission to use this command!", ephemeral=True)
        return
    
    allowed_users.add(user.id)
    save_data()
    await interaction.response.send_message(f"Successfully added **{user.name}** ({user.id}) to the system.")

@bot.tree.command(name="remove-user", description="Revoke a user's access from the bot (Owner Only).")
@app_commands.describe(user="Select the Discord user to remove")
async def remove_user(interaction: discord.Interaction, user: discord.User):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("ERROR: You do not have permission to use this command!", ephemeral=True)
        return
    
    if user.id == OWNER_ID:
        await interaction.response.send_message("You cannot remove yourself (the owner).", ephemeral=True)
        return

    if user.id in allowed_users:
        allowed_users.discard(user.id)
        save_data()
        await interaction.response.send_message(f"Successfully removed **{user.name}** ({user.id}) from the system.")
    else:
        await interaction.response.send_message(f"User **{user.name}** was not in the access list.", ephemeral=True)

@bot.tree.command(name="clear", description="Delete a specific number of messages.")
@app_commands.describe(amount="Number of messages to delete")
async def clear(interaction: discord.Interaction, amount: int):
    if interaction.user.id not in allowed_users:
        await interaction.response.send_message("ERROR: You do not have permission to use me!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    # Check if we are in DMs or a regular channel
    if isinstance(interaction.channel, discord.DMChannel):
        deleted = 0
        async for msg in interaction.channel.history(limit=amount):
            if msg.author == bot.user:
                try:
                    await msg.delete()
                    deleted += 1
                except Exception:
                    pass
        await interaction.followup.send(f"Cleared {deleted} bot messages from DM history.", ephemeral=True)
    else:
        try:
            deleted = await interaction.channel.purge(limit=amount)
            await interaction.followup.send(f"Successfully deleted {len(deleted)} messages.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Failed to clear messages: {e}", ephemeral=True)

@bot.tree.command(name="clear-all", description="Delete all recent messages in the channel/DM.")
async def clear_all(interaction: discord.Interaction):
    if interaction.user.id not in allowed_users:
        await interaction.response.send_message("ERROR: You do not have permission to use me!", ephemeral=True)
        return
    
    await interaction.response.defer(ephemeral=True)
    
    if isinstance(interaction.channel, discord.DMChannel):
        deleted = 0
        async for msg in interaction.channel.history(limit=100):
            if msg.author == bot.user:
                try:
                    await msg.delete()
                    deleted += 1
                except Exception:
                    pass
        await interaction.followup.send(f"Cleared all possible bot messages ({deleted}) from DM.", ephemeral=True)
    else:
        try:
            deleted = await interaction.channel.purge(limit=100)
            await interaction.followup.send(f"Successfully deleted {len(deleted)} messages.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"Failed to clear channel: {e}", ephemeral=True)

@bot.tree.command(name="count", description="View total processed music theory questions.")
async def count(interaction: discord.Interaction):
    if interaction.user.id not in allowed_users:
        await interaction.response.send_message("ERROR: You do not have permission to use me!", ephemeral=True)
        return
    await interaction.response.send_message(f"Total analysis assignments completed: **{usage_count}**")

# --- CORE QUESTION LISTENER ---
@bot.event
async def on_message(message):
    global usage_count
    if message.author.bot:
        return

    if message.author.id not in allowed_users:
        if isinstance(message.channel, discord.DMChannel):
            await message.channel.send("ERROR: You do not have permission to use me!")
        return

    text_data = message.content
    image_data = None

    # Handle native Discord Forwards / Message Attachments
    attachments_to_check = list(message.attachments)
    
    # If it's a layout forward reference containing snapshots or files
    if message.reference and message.reference.cached_message:
        ref_msg = message.reference.cached_message
        if ref_msg.attachments:
            attachments_to_check.extend(ref_msg.attachments)
        if not text_data and ref_msg.content:
            text_data = ref_msg.content

    if attachments_to_check:
        for attachment in attachments_to_check:
            if attachment.content_type and "image" in attachment.content_type:
                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            break # Grab the primary image resource

    # Skip empty configurations or test triggers
    if not text_data and not image_data:
        return
    if text_data and text_data.lower().strip() in ["hi", "hello", "hey", "test"]:
        if isinstance(message.channel, discord.DMChannel):
            await message.channel.send("System online. Send or forward an analysis image.")
        return

    async with message.channel.typing():
        answer = await get_ai_answer(text_data, image_data)
        
    await message.channel.send(answer)
    
    if not answer.startswith("Error:"):
        usage_count += 1
        save_data()

# Start Flask web server in background thread, then trigger bot
Thread(target=run_webserver).start()
bot.run(DISCORD_TOKEN)
