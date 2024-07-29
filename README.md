# Cali 420 Rust Discord Bot

This Discord bot is designed for the Cali 420 Rust Discord Community. It fetches player data from the BattleMetrics API and updates a Discord message with the list of online and recently logged-off players.

## Setup

1. Clone the repository:
   git clone https://github.com/yourusername/cali-420-rust-discord-bot.git
   cd cali-420-rust-discord-bot

2. Create a virtual environment and activate it:
   python -m venv venv
   source venv/bin/activate  # On Windows use `venv\Scripts\activate`

3. Install the required packages:
   pip install -r requirements.txt

4. Create a `.env` file and add your environment variables:
   touch .env

   Add the following lines to the `.env` file:
   
   BM_API_TOKEN=your_battlemetrics_api_token            # Your BattleMetrics API token
   
   DISCORD_BOT_TOKEN=your_discord_bot_token             # Your Discord bot token
   
   CHANNEL_ID=your_discord_channel_id                   # The ID of the Discord channel where the bot will post updates
   
   ONLINE_MESSAGE_ID=online_message_id                  # The ID of the message for online players (will be created if not provided)
   
   OFFLINE_MESSAGE_ID=offline_message_id                # The ID of the message for offline players (will be created if not provided)
   
   SERVER_ID=your_server_id                             # Your BattleMetrics server ID

6. Run the bot:
   python bot.py

## Contributing

If you would like to contribute to this project, please fork the repository and submit a pull request.

## License

This project is licensed under the MIT License.
