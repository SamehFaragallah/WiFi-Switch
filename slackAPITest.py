from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import CONFIG

# Your bot token (xoxb-...)
SLACK_BOT_TOKEN = CONFIG['slack'].get('bot_token', None)

# The user's Slack ID (e.g. U012ABCDEF)
USER_IDS = [
    "U08HUJ4R8MU",
    "U069J0MEQTF",
    "UN81NP3FV"
]

MESSAGE = "WiFi Switch: Test message to group"

client = WebClient(token=SLACK_BOT_TOKEN)

def send_group_dm(user_ids, message):
    try:
        # Open (or reuse) a group DM with the users
        response = client.conversations_open(
            users=",".join(user_ids)
        )

        channel_id = response["channel"]["id"]

        # Send the message
        client.chat_postMessage(
            channel=channel_id,
            text=message
        )

        print("Group DM sent successfully")

    except SlackApiError as e:
        print("Slack API error:", e.response["error"])
    except Exception as e:
        print("Unexpected error:", str(e))

if __name__ == "__main__":
    send_group_dm(USER_IDS, MESSAGE)
