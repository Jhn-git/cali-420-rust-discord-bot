import os
import json
import aiohttp
import asyncio
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import discord
from discord.ext import tasks, commands

# Load environment variables from .env file
load_dotenv()

BM_API_TOKEN = os.getenv('BM_API_TOKEN')
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))
ONLINE_MESSAGE_ID = os.getenv('ONLINE_MESSAGE_ID')
OFFLINE_MESSAGE_ID = os.getenv('OFFLINE_MESSAGE_ID')
SERVER_ID = os.getenv('SERVER_ID')

CACHE_EXPIRATION_SECONDS = 600  # 10 minutes
PLAYER_LIST_UPDATE_INTERVAL = 60  # 1 minute
PLAYER_DETAILS_UPDATE_INTERVAL = 300  # 5 minutes
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILENAME = os.path.join(SCRIPT_DIR, 'player_playtime.json')
ONLINE_MESSAGE_ID_FILE = os.path.join(SCRIPT_DIR, 'online_message_id.txt')
OFFLINE_MESSAGE_ID_FILE = os.path.join(SCRIPT_DIR, 'offline_message_id.txt')
RATE_LIMIT_PER_SECOND = 45
MAX_RETRIES = 10

semaphore = asyncio.Semaphore(RATE_LIMIT_PER_SECOND)
player_cache = {}
cache_timestamps = {}

def load_message_id(file):
    if os.path.exists(file):
        with open(file, 'r') as f:
            return f.read().strip()
    return None

def save_message_id(message_id, file):
    with open(file, 'w') as f:
        f.write(message_id)

def load_existing_log(filename):
    if os.path.exists(filename):
        with open(filename, 'r') as json_file:
            try:
                data = json.load(json_file)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                print(f"Error decoding JSON from {filename}, initializing empty log.")
    return {"players": {}}

def save_to_json(data, filename):
    with open(filename, 'w') as json_file:
        json.dump(data, json_file, indent=4)
    print(f"Data saved to {filename}")

async def exponential_backoff(retries, response=None):
    delay = int(response.headers.get('Retry-After', 2 ** retries)) if response and response.status == 429 else min(2 ** retries, 60)
    print(f"Rate limit hit, backing off for {delay} seconds...")
    await asyncio.sleep(delay)

async def fetch_with_retries(session, url, headers, retries=0):
    while retries <= MAX_RETRIES:
        async with semaphore, session.get(url, headers=headers) as response:
            if response.status == 200:
                return await response.json()
            elif response.status == 429:
                await exponential_backoff(retries, response)
                retries += 1
            else:
                print(f"Failed to fetch {url}: {response.status} - {await response.text()}")
                return None
    return None

async def get_player_list(server_id):
    players, page_offset, page_size = [], 0, 100
    url_template = f'https://api.battlemetrics.com/players?filter[servers]={server_id}&filter[online]=true&page[offset]={{}}&page[size]={page_size}'
    headers = {'Authorization': f'Bearer {BM_API_TOKEN}'}

    async with aiohttp.ClientSession() as session:
        while True:
            url = url_template.format(page_offset)
            data = await fetch_with_retries(session, url, headers)
            if data and 'data' in data:
                player_ids = [(player['id'], player['attributes'].get('name', 'N/A')) for player in data['data']]
                players.extend(player_ids)
                if len(data['data']) < page_size:
                    break
                page_offset += page_size
                if page_offset > 90:
                    break
            else:
                break
    return players

async def get_player_details(session, player_id, player_name, server_id):
    if player_id in player_cache and datetime.now() - cache_timestamps[player_id] < timedelta(seconds=CACHE_EXPIRATION_SECONDS):
        return player_cache[player_id]

    url = f'https://api.battlemetrics.com/players/{player_id}?include=server&filter[servers]={server_id}'
    headers = {'Authorization': f'Bearer {BM_API_TOKEN}'}
    data = await fetch_with_retries(session, url, headers)
    
    if data and 'included' in data:
        for server in data['included']:
            if server['type'] == 'server' and server['id'] == server_id:
                server_meta = server.get('meta', {})
                if server_meta.get('online', False):
                    playtime = server_meta.get('timePlayed', 0)
                    playtime_minutes = playtime // 60
                    player_details = {
                        "id": player_id,
                        "name": player_name,
                        "playtime_minutes": playtime_minutes,
                        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    }
                    player_cache[player_id] = player_details
                    cache_timestamps[player_id] = datetime.now()
                    return player_details
    return None

def update_player_data(log, player_data):
    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    online_players = set()

    for player in player_data:
        if not player:
            continue
        player_id = player["id"]
        player_name = player["name"]
        playtime_minutes = player["playtime_minutes"]
        timestamp = player["timestamp"]
        online_players.add(player_id)

        if player_id not in log["players"]:
            log["players"][player_id] = {
                "name": player_name,
                "total_playtime_minutes": playtime_minutes,
                "current_session_start": timestamp,
                "session_history": []
            }
        else:
            player_log = log["players"][player_id]
            if not player_log["current_session_start"]:
                player_log["current_session_start"] = timestamp

            session_start_time = datetime.strptime(player_log["current_session_start"], "%Y-%m-%dT%H:%M:%SZ")
            current_time_dt = datetime.strptime(current_time, "%Y-%m-%dT%H:%M:%SZ")
            session_playtime = int((current_time_dt - session_start_time).total_seconds() // 60)

            if not player_log["session_history"] or player_log["session_history"][-1]["end"] != current_time:
                player_log["session_history"].append({
                    "start": player_log["current_session_start"],
                    "end": current_time,
                    "playtime_minutes": session_playtime
                })
            player_log["total_playtime_minutes"] = playtime_minutes

        print(f"Player {player_name} (ID: {player_id}) - Current session start: {log['players'][player_id]['current_session_start']}")

    for player_id in list(log["players"].keys()):
        if player_id not in online_players:
            player_log = log["players"][player_id]
            if player_log["current_session_start"]:
                session_start = player_log["current_session_start"]
                last_session = player_log["session_history"][-1] if player_log["session_history"] else None
                if last_session and last_session["end"] != session_start:
                    player_log["session_history"].append({
                        "start": session_start,
                        "end": current_time,
                        "playtime_minutes": (datetime.strptime(current_time, "%Y-%m-%dT%H:%M:%SZ") - datetime.strptime(session_start, "%Y-%m-%dT%H:%M:%SZ")).seconds // 60
                    })
                player_log["current_session_start"] = None
                player_log["last_logged_off"] = current_time

def generate_player_lists(log):
    current_time = datetime.now(timezone.utc)
    online_players, recent_offline_players = [], []

    for player_id, player_info in log["players"].items():
        if player_info["current_session_start"]:
            session_start = datetime.strptime(player_info["current_session_start"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            online_time = current_time - session_start
            hours, remainder = divmod(online_time.total_seconds(), 3600)
            minutes, _ = divmod(remainder, 60)
            online_time_str = f"{int(hours)}h {int(minutes)}m" if hours > 0 else f"{int(minutes)}m"
            online_players.append((player_info['name'], online_time_str, online_time.total_seconds()))
        elif "last_logged_off" in player_info:
            logged_off_time = datetime.strptime(player_info["last_logged_off"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            time_since_logoff = current_time - logged_off_time
            recent_offline_players.append((player_info['name'], f"{int(time_since_logoff.total_seconds() // 3600)}h {int((time_since_logoff.total_seconds() % 3600) // 60)}m ago" if time_since_logoff.total_seconds() >= 3600 else f"{int(time_since_logoff.total_seconds() // 60)}m ago", logged_off_time))

    online_players.sort(key=lambda x: x[2], reverse=True)
    recent_offline_players.sort(key=lambda x: x[2], reverse=True)

    return online_players[:120], recent_offline_players[:5]

def create_embed(title, description, players, footer_text, color):
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=datetime.now(timezone.utc)
    )

    if players:
        player_names = "\n".join([f"{i+1}. {player[0]}" for i, player in enumerate(players)])
        player_times = "\n".join([player[1] for player in players])
        embed.add_field(name="Name", value=f"```{player_names}```", inline=True)
        embed.add_field(name="Time", value=f"```{player_times}```", inline=True)

    embed.set_footer(text=footer_text)
    return embed

async def update_or_create_discord_message(channel_id, message_id_file, embed):
    message_id = load_message_id(message_id_file)
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json"
    }
    url = f"https://discord.com/api/v9/channels/{channel_id}/messages/{message_id}"
    data = {"embeds": [embed.to_dict()], "content": ""}
    
    async with aiohttp.ClientSession() as session:
        if message_id:
            async with session.patch(url, headers=headers, json=data) as response:
                if response.status == 200:
                    print("Message updated successfully")
                elif response.status == 403:
                    print("Bot is missing access. Check the bot's permissions in the channel.")
                    return
                elif response.status == 404:
                    print("Message not found, creating a new message")
                    message_id = None
                else:
                    print(f"Failed to update message: {response.status} - {await response.text()}")
        if not message_id:
            async with session.post(f"https://discord.com/api/v9/channels/{channel_id}/messages", headers=headers, json=data) as post_response:
                if post_response.status == 200:
                    new_message_id = (await post_response.json())["id"]
                    save_message_id(new_message_id, message_id_file)
                    print("New message created successfully")
                else:
                    print(f"Failed to create new message: {post_response.status} - {await post_response.text()}")

# Define the Discord bot
intents = discord.Intents.default()
intents.messages = True
intents.guilds = True
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    print('------')
    update_online_players.start()
    update_player_details.start()

@tasks.loop(seconds=PLAYER_LIST_UPDATE_INTERVAL)
async def update_online_players():
    log = load_existing_log(LOG_FILENAME)
    player_data = await get_player_list(SERVER_ID)
    async with aiohttp.ClientSession() as session:
        player_details = await asyncio.gather(*[get_player_details(session, player_id, player_name, SERVER_ID) for player_id, player_name in player_data])
    update_player_data(log, player_details)
    save_to_json(log, LOG_FILENAME)

    online_players, recent_offline_players = generate_player_lists(log)
    await update_or_create_discord_message(CHANNEL_ID, ONLINE_MESSAGE_ID_FILE, create_embed("Online Players", f"Total Online Players: {len(online_players)}", online_players, "Player status updates every minute", 0x00ff00))
    await update_or_create_discord_message(CHANNEL_ID, OFFLINE_MESSAGE_ID_FILE, create_embed("Recent Log Offs", "", recent_offline_players, "Player status updates every minute", 0xff0000))

@tasks.loop(seconds=PLAYER_DETAILS_UPDATE_INTERVAL)
async def update_player_details():
    log = load_existing_log(LOG_FILENAME)
    player_data = await get_player_list(SERVER_ID)
    async with aiohttp.ClientSession() as session:
        player_details = await asyncio.gather(*[get_player_details(session, player_id, player_name, SERVER_ID) for player_id, player_name in player_data])
    update_player_data(log, player_details)
    save_to_json(log, LOG_FILENAME)

bot.run(DISCORD_BOT_TOKEN)
