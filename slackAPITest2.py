from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from config import CONFIG
import requests

# Your bot token (xoxb-...)



SLACK_BOT_TOKEN = CONFIG['slack'].get('bot_token', None)
CHANNEL_ID = "GCDFFEZL1"

headers = {
    "Authorization": f"Bearer {SLACK_BOT_TOKEN}"
}

members = []
cursor = None

while True:
    params = {
        "channel": CHANNEL_ID,
        "limit": 1000
    }
    if cursor:
        params["cursor"] = cursor

    response = requests.get(
        "https://slack.com/api/conversations.members",
        headers=headers,
        params=params
    ).json()

    if not response.get("ok"):
        raise Exception(response)

    members.extend(response["members"])
    cursor = response.get("response_metadata", {}).get("next_cursor")

    if not cursor:
        break

print("User IDs:", members)

