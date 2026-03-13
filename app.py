import os
import json
import anthropic
from flask import Flask, request, redirect
from twilio.rest import Client as TwilioClient
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from datetime import datetime, timedelta
import pytz

app = Flask(__name__)

# Config from environment variables
TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN")
TWILIO_PHONE = os.environ.get("TWILIO_PHONE")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
YOUR_PHONE = os.environ.get("YOUR_PHONE")
BASE_URL = os.environ.get("BASE_URL")
TIMEZONE = os.environ.get("TIMEZONE", "America/Indiana/Indianapolis")

SCOPES = ["https://www.googleapis.com/auth/calendar"]
TOKEN_FILE = "token.json"
GROCERY_FILE = "grocery_list.json"
TODO_FILE = "todo_list.json"
CONVERSATION_FILE = "conversation_history.json"

twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


# Grocery List Helpers

def load_grocery_list():
    if os.path.exists(GROCERY_FILE):
        with open(GROCERY_FILE, "r") as f:
            return json.load(f)
    return []

def save_grocery_list(items):
    with open(GROCERY_FILE, "w") as f:
        json.dump(items, f)

def add_grocery_items(new_items):
    items = load_grocery_list()
    added = []
    for item in new_items:
        item = item.strip()
        if item and item.lower() not in [i.lower() for i in items]:
            items.append(item)
            added.append(item)
    save_grocery_list(items)
    return added, items

def remove_grocery_items(remove_items):
    items = load_grocery_list()
    removed = []
    for r in remove_items:
        for item in items[:]:
            if r.lower() in item.lower():
                items.remove(item)
                removed.append(item)
    save_grocery_list(items)
    return removed, items

def clear_grocery_list():
    save_grocery_list([])


# To-Do List Helpers

def load_todo_list():
    if os.path.exists(TODO_FILE):
        with open(TODO_FILE, "r") as f:
            return json.load(f)
    return []

def save_todo_list(items):
    with open(TODO_FILE, "w") as f:
        json.dump(items, f)

def add_todo_items(new_items):
    items = load_todo_list()
    added = []
    for item in new_items:
        item = item.strip()
        if item and item.lower() not in [i.lower() for i in items]:
            items.append(item)
            added.append(item)
    save_todo_list(items)
    return added, items

def remove_todo_items(remove_items):
    items = load_todo_list()
    removed = []
    for r in remove_items:
        for item in items[:]:
            if r.lower() in item.lower():
                items.remove(item)
                removed.append(item)
    save_todo_list(items)
    return removed, items

def clear_todo_list():
    save_todo_list([])


# Conversation History

def load_conversation():
    if os.path.exists(CONVERSATION_FILE):
        with open(CONVERSATION_FILE, "r") as f:
            history = json.load(f)
        return history[-20:]
    return []

def save_conversation(history):
    history = history[-20:]
    with open(CONVERSATION_FILE, "w") as f:
        json.dump(history, f)


# Google Calendar Helpers

def get_calendar_service():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
    return build("calendar", "v3", credentials=creds)

def get_upcoming_events(max_results=5):
    service = get_calendar_service()
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz).isoformat()
    events_result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=max_results,
        singleEvents=True,
        orderBy="startTime"
    ).execute()
    return events_result.get("items", [])

def format_events(events):
    if not events:
        return "No upcoming events found."
    lines = []
    tz = pytz.timezone(TIMEZONE)
    for e in events:
        start = e["start"].get("dateTime", e["start"].get("date"))
        if "T" in start:
            dt = datetime.fromisoformat(start).astimezone(tz)
            time_str = dt.strftime("%a %b %-d at %-I:%M %p")
        else:
            dt = datetime.fromisoformat(start)
            time_str = dt.strftime("%a %b %-d")
        lines.append(f"- {e['summary']} ({time_str})")
    return "\n".join(lines)

def add_to_calendar(event_data):
    service = get_calendar_service()
    start_dt = f"{event_data['date']}T{event_data['start_time']}:00"
    end_dt = f"{event_data['date']}T{event_data['end_time']}:00"
    event = {
        "summary": event_data["title"],
        "description": event_data.get("description", ""),
        "start": {"dateTime": start_dt, "timeZone": TIMEZONE},
        "end": {"dateTime": end_dt, "timeZone": TIMEZONE},
    }
    created = service.events().insert(calendarId="primary", body=event).execute()
    return created.get("htmlLink")


# Main Claude Handler

SYSTEM_PROMPT = """You are Aaron's personal SMS assistant. Aaron is 22, goes to Indiana University, is a pre-PA student and EMT. He's casual and direct.

You have access to these capabilities:
1. CALENDAR - Add events, check upcoming events
2. GROCERY LIST - Add/remove/view/clear shopping items
3. TO-DO LIST - Add/remove/view/clear tasks and reminders
4. GENERAL - Answer any question, have a conversation

For every message, respond with a JSON object:
{
  "action": "calendar_add" | "calendar_view" | "grocery_add" | "grocery_remove" | "grocery_view" | "grocery_clear" | "todo_add" | "todo_remove" | "todo_view" | "todo_clear" | "chat",
  "reply": "SMS reply (concise, under 300 chars when possible, no markdown)",
  "event": {"title": "...", "date": "YYYY-MM-DD", "start_time": "HH:MM", "end_time": "HH:MM"},
  "items": ["item1", "item2"]
}

Only include "event" for calendar_add. Only include "items" for grocery/todo add/remove actions.
Use grocery list for shopping, to-do list for tasks and reminders.
Today: {date}. Time: {time} ({timezone}).
Casual tone. RETURN ONLY VALID JSON."""


def handle_message(user_message):
    tz = pytz.timezone(TIMEZONE)
    now = datetime.now(tz)

    history = load_conversation()
    system = SYSTEM_PROMPT.format(
        date=now.strftime("%Y-%m-%d"),
        time=now.strftime("%-I:%M %p"),
        timezone=TIMEZONE
    )

    history.append({"role": "user", "content": user_message})

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        system=system,
        messages=history
    )

    raw = response.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    result = json.loads(raw.strip())
    action = result.get("action", "chat")
    reply = result.get("reply", "Got it!")

    if action == "calendar_add":
        try:
            add_to_calendar(result.get("event", {}))
        except Exception as e:
            reply = f"Couldn't add to calendar: {str(e)[:100]}"

    elif action == "calendar_view":
        try:
            events = get_upcoming_events()
            reply = "Upcoming:\n" + format_events(events)
        except Exception as e:
            reply = f"Couldn't fetch calendar: {str(e)[:100]}"

    elif action == "grocery_add":
        added, all_items = add_grocery_items(result.get("items", []))
        reply = f"Added: {', '.join(added)}. {len(all_items)} items on grocery list." if added else "Already on your grocery list!"

    elif action == "grocery_remove":
        removed, all_items = remove_grocery_items(result.get("items", []))
        reply = f"Removed: {', '.join(removed)}. {len(all_items)} left." if removed else "Couldn't find those on your grocery list."

    elif action == "grocery_view":
        items = load_grocery_list()
        reply = "Grocery list:\n" + "\n".join(f"- {i}" for i in items) if items else "Grocery list is empty!"

    elif action == "grocery_clear":
        clear_grocery_list()
        reply = "Grocery list cleared!"

    elif action == "todo_add":
        added, all_items = add_todo_items(result.get("items", []))
        reply = f"Added to to-do: {', '.join(added)}. {len(all_items)} items total." if added else "Already on your to-do list!"

    elif action == "todo_remove":
        removed, all_items = remove_todo_items(result.get("items", []))
        reply = f"Done! Removed: {', '.join(removed)}. {len(all_items)} left." if removed else "Couldn't find those on your to-do list."

    elif action == "todo_view":
        items = load_todo_list()
        reply = "To-do list:\n" + "\n".join(f"- {i}" for i in items) if items else "To-do list is empty!"

    elif action == "todo_clear":
        clear_todo_list()
        reply = "To-do list cleared!"

    history.append({"role": "assistant", "content": reply})
    save_conversation(history)
    return reply


# Routes

@app.route("/sms", methods=["POST"])
def sms_reply():
    from_number = request.form.get("From")
    body = request.form.get("Body", "").strip()

    if from_number != YOUR_PHONE:
        return "Unauthorized", 403

    try:
        reply = handle_message(body)
    except Exception as e:
        reply = f"Something went wrong: {str(e)[:100]}"

    twilio_client.messages.create(body=reply, from_=TWILIO_PHONE, to=YOUR_PHONE)
    return "", 204


@app.route("/oauth/callback")
def oauth_callback():
    flow = Flow.from_client_config(
        {"web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{BASE_URL}/oauth/callback"],
        }},
        scopes=SCOPES,
        redirect_uri=f"{BASE_URL}/oauth/callback",
    )
    flow.fetch_token(authorization_response=request.url)
    with open(TOKEN_FILE, "w") as f:
        f.write(flow.credentials.to_json())
    return "Google Calendar connected! You can now text the bot."


@app.route("/auth")
def auth():
    flow = Flow.from_client_config(
        {"web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [f"{BASE_URL}/oauth/callback"],
        }},
        scopes=SCOPES,
        redirect_uri=f"{BASE_URL}/oauth/callback",
    )
    auth_url, _ = flow.authorization_url(prompt="consent")
    return redirect(auth_url)


@app.route("/")
def index():
    return "Aaron's AI Assistant is running!"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
