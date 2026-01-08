from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# Your bot token (xoxb-...)
SLACK_BOT_TOKEN = "xoxb-7610951043-10272617235233-2gOVCwLCE4cwnlBSf0ytcVzO"

# The user's Slack ID (e.g. U012ABCDEF)
USER_ID = "U08HUJ4R8MU"

client = WebClient(token=SLACK_BOT_TOKEN)

def send_dm(user_id, message):
    try:
        # Open a DM channel with the user
        response = client.conversations_open(users=user_id)
        channel_id = response["channel"]["id"]

        # Send the message
        client.chat_postMessage(
            channel=channel_id,
            text=message
        )

        print("Message sent successfully!")

    except SlackApiError as e:
        print(f"Error sending message: {e.response['error']}")

if __name__ == "__main__":
    send_dm(USER_ID, "Hello from my Python bot 👋")
