import os
import base64
import discord
from discord.ext import commands
import aiohttp
from flask import Flask
from threading import Thread

# Fetch environment variables from Render's dashboard setup
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Safely handle the Owner ID conversion
try:
    OWNER_ID = int(os.getenv("OWNER_ID", 0))
except (ValueError, TypeError):
    OWNER_ID = 0

allowed_users = {OWNER_ID}
usage_count = 0

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="?", intents=intents)

# Flask Web Server Layer for Render / UptimeRobot
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run_webserver():
    # Render automatically sets a 'PORT' environment variable
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

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
    
    # Structure the prompt for multimodal inputs
    messages_content = [
        {
            "type": "text", 
            "text": text_content if text_content else "Solve the music theory question in this image."
        }
    ]
    
    if image_bytes:
        # Encode image to base64 string for the API payload
        base64_image = base64.b64encode(image_bytes).decode('utf-8')
        messages_content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{base64_image}"
            }
        })

    payload = {
        "model": "google/gemini-flash-1.5-8b",
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

@bot.command()
async def add(ctx, user_id: str):
    if ctx.author.id == OWNER_ID:
        try:
            uid = int(user_id)
            allowed_users.add(uid)
            await ctx.send(f"Added {uid}")
        except ValueError:
            await ctx.send("Invalid User ID.")
    else:
        await ctx.send("No permission.")

@bot.command()
async def remove(ctx, user_id: str):
    if ctx.author.id == OWNER_ID:
        try:
            uid = int(user_id)
            if uid != OWNER_ID:
                allowed_users.discard(uid)
                await ctx.send(f"Removed {uid}")
            else:
                await ctx.send("Cannot remove the owner.")
        except ValueError:
            await ctx.send("Invalid User ID.")
    else:
        await ctx.send("No permission.")

@bot.command()
async def ping(ctx):
    await ctx.send(f"Pong! {round(bot.latency * 1000)}ms")

@bot.command()
async def count(ctx):
    if ctx.author.id in allowed_users:
        await ctx.send(f"Total answers: {usage_count}")

@bot.event
async def on_message(message):
    global usage_count
    if message.author.bot:
        return
    
    # Let standard '?' commands process first
    if message.content.startswith('?'):
        await bot.process_commands(message)
        return
        
    # Process forwarded questions/images automatically if user has access
    if message.author.id in allowed_users:
        text_data = message.content
        image_data = None
        
        if message.attachments:
            attachment = message.attachments[0]
            if attachment.content_type and "image" in attachment.content_type:
                async with aiohttp.ClientSession() as session:
                    async with session.get(attachment.url) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            
        if not text_data and not image_data:
            return
            
        async with message.channel.typing():
            answer = await get_ai_answer(text_data, image_data)
            
        await message.channel.send(answer)
        usage_count += 1

# Start Flask web server in a background thread before running the bot
Thread(target=run_webserver).start()
bot.run(DISCORD_TOKEN)