import os
import json
import anthropic
from flask import Flask, request, redirect, session
from twilio.rest import Client as TwilioClient
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime
import dateparser

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "supersecretkey123")

# Config from environment variables
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.environ.get("TWILIO_PHONE")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
YOUR_PHONE = os.environ.get("YOUR_PHONE")  # Your personal phone number
BASE_URL = os.environ.get("BASE_URL")  # Your Render URL

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = "token.json"

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def get_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    return build("calendar", "v3", credentials=creds)


def parse_event_with_claude(message):
    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[
            {
                "role": "user",
                "content": f"""Extract calendar event details from this message and return ONLY a JSON object with these fields:
- title (string)
- date (string in YYYY-MM-DD format)
- start_time (string in HH:MM format, 24hr)
- end_time (string in HH:MM format, 24hr, assume 1 hour if not specified)
- description (string, optional)

Today's date is {datetime.now().strftime('%Y-%m-%d')}.

Message: "{message}"

Return ONLY the JSON, no other text.""",
            }
        ],
    )

    text = response.content[0].text.strip()
    # Strip markdown code blocks if present
    if text.startswith("```"):
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def add_to_calendar(event_data):
    service = get_calendar_service()

    start_dt = f"{event_data['date']}T{event_data['start_time']}:00"
    end_dt = f"{event_data['date']}T{event_data['end_time']}:00"

    event = {
        "summary": event_data["title"],
        "description": event_data.get("description", ""),
        "start": {"dateTime": start_dt, "timeZone": "America/New_York"},
        "end": {"dateTime": end_dt, "timeZone": "America/New_York"},
    }

    created = service.events().insert(calendarId="primary", body=event).execute()
    return created.get("htmlLink")


@app.route("/sms", methods=["POST"])
def sms_reply():
    from_number = request.form.get("From")
    body = request.form.get("Body", "").strip()

    # Only respond to your number
    if from_number != YOUR_PHONE:
        return "Unauthorized", 403

    try:
        event_data = parse_event_with_claude(body)
        link = add_to_calendar(event_data)
        reply = f"✅ Added '{event_data['title']}' on {event_data['date']} at {event_data['start_time']}!"
    except Exception as e:
        reply = f"❌ Couldn't parse that. Try something like 'Dentist appointment Tuesday at 3pm'. Error: {str(e)}"

    twilio_client.messages.create(
        body=reply, from_=TWILIO_PHONE, to=YOUR_PHONE
    )

    return "", 204


def make_flow():
    return Flow.from_client_config(
        {
            "web": {
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [f"{BASE_URL}/oauth/callback"],
            }
        },
        scopes=SCOPES,
        redirect_uri=f"{BASE_URL}/oauth/callback",
    )


@app.route("/oauth/callback")
def oauth_callback():
    flow = make_flow()
    flow.fetch_token(
        code=request.args.get("code"),
    )
    creds = flow.credentials

    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())

    return "✅ Google Calendar connected! You can now text the bot to add events."


@app.route("/auth")
def auth():
    flow = make_flow()
    auth_url, state = flow.authorization_url(
        prompt="consent",
        access_type="offline",
    )
    session["oauth_state"] = state
    return redirect(auth_url)


@app.route("/")
def index():
    return "Calendar Bot is running! Visit /auth to connect Google Calendar."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
