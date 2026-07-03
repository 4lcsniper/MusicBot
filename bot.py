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
                # Convert user IDs back to integers
                allowed = set(int(uid) for uid in data.get("allowed_users", []))
                allowed.add(OWNER_ID) # Always ensure owner is included
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
        # Syncs slash commands globally across Discord
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
        "model": "google/gemini-2.5-flash",
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

@bot.tree.command(name="ping", description="Check the bot's current response latency.")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! {latency}ms")

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

    # Let the modern slash command architecture process interactions natively
    # If it's a message in DMs or channels, check user security status
    if message.author.id not in allowed_users:
        # Ignore normal casual channel chit-chat unless they specifically try talking to the bot directly in DMs
        if isinstance(message.channel, discord.DMChannel):
            await message.channel.send("ERROR: You do not have permission to use me!")
        return

    text_data = message.content
    image_data = None
    
    if message.attachments:
        attachment = message.attachments[0]
        if attachment.content_type and "image" in attachment.content_type:
            async with aiohttp.ClientSession() as session:
                async with session.get(attachment.url) as resp:
                    if resp.status == 200:
                        image_data = await resp.read()

    # Skip basic greeting checks or completely blank notifications so they don't count towards analytics
    if not text_data and not image_data:
        return
    if text_data.lower().strip() in ["hi", "hello", "hey", "test"]:
        if isinstance(message.channel, discord.DMChannel):
            await message.channel.send("System online. Send an analysis image or music theory request.")
        return

    # True music theory analysis request triggered here
    async with message.channel.typing():
        answer = await get_ai_answer(text_data, image_data)
        
    await message.channel.send(answer)
    
    # Increment tracking metrics exclusively on real query operations
    if not answer.startswith("Error:"):
        usage_count += 1
        save_data()

# Start Flask web server in background thread, then trigger bot
Thread(target=run_webserver).start()
bot.run(DISCORD_TOKEN)
