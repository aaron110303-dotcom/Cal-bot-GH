import os
import json
import requests as req
import anthropic
from flask import Flask, request, redirect
from twilio.rest import Client as TwilioClient
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from datetime import datetime
from urllib.parse import urlencode

app = Flask(__name__)

TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.environ.get("TWILIO_PHONE")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
YOUR_PHONE = os.environ.get("YOUR_PHONE")
BASE_URL = os.environ.get("BASE_URL")

TOKEN_FILE = "token.json"

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def get_calendar_service():
    with open(TOKEN_FILE) as f:
        token_data = json.load(f)
    creds = Credentials(
        token=token_data.get("access_token"),
        refresh_token=token_data.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
    )
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
    if from_number != YOUR_PHONE:
        return "Unauthorized", 403
    try:
        event_data = parse_event_with_claude(body)
        add_to_calendar(event_data)
        reply = f"Added '{event_data['title']}' on {event_data['date']} at {event_data['start_time']}!"
    except Exception as e:
        reply = f"Couldn't parse that. Try: 'Dentist Tuesday at 3pm'. Error: {str(e)}"
    twilio_client.messages.create(body=reply, from_=TWILIO_PHONE, to=YOUR_PHONE)
    return "", 204


@app.route("/oauth/callback")
def oauth_callback():
    code = request.args.get("code")
    token_response = req.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{BASE_URL}/oauth/callback",
            "grant_type": "authorization_code",
        },
    )
    token_data = token_response.json()
    with open(TOKEN_FILE, "w") as f:
        json.dump(token_data, f)
    return "Google Calendar connected! You can now text the bot to add events."


@app.route("/auth")
def auth():
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{BASE_URL}/oauth/callback",
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/calendar",
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/auth?" + urlencode(params)
    return redirect(auth_url)


@app.route("/")
def index():
    return "Calendar Bot is running! Visit /auth to connect Google Calendar."


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
