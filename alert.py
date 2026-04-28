"""
Sparks Wedding Flight Alert System
Queries Google Flights for each guest's cheapest route to Lisbon (LIS)
and sends a personalised email nudge via Resend.

Usage:
    python alert.py             # sends real emails (scheduled at 9 AM local)
    python alert.py --dry-run   # prints output, sends nothing
"""
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
import resend
from fast_flights import FlightData, Passengers, Result, get_flights

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DESTINATION   = "LIS"
SENDER        = "Sparks Wedding Travel <travel@sparkswedding.co>"
SEARCH_DAYS   = 90
SAMPLE_DATES  = 6
SLEEP_BETWEEN = 3
DRY_RUN       = "--dry-run" in sys.argv

resend.api_key = os.environ.get("RESEND_API_KEY", "")

# ── Airport → Timezone map ────────────────────────────────────────────────────
AIRPORT_TIMEZONES: dict[str, str] = {
    "SFO": "America/Los_Angeles",
    "DEN": "America/Denver",
    "EWR": "America/New_York",
    "AVP": "America/New_York",
    "JFK": "America/New_York",
    "LGA": "America/New_York",
    "MEL": "Australia/Melbourne",
    "PRG": "Europe/Prague",
    "DUB": "Europe/Dublin",
    "LHR": "Europe/London",
    "DTW": "America/Detroit",
    "MCO": "America/New_York",
    "DFW": "America/Chicago",
    "ALB": "America/New_York",
    "BNA": "America/Chicago",
    "IAD": "America/New_York",
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def next_9am_utc(airport: str) -> str:
    """
    Returns the next 9:00 AM in the airport's local timezone as a UTC
    ISO 8601 string (e.g. '2026-05-01T13:00:00Z'), suitable for Resend's
    scheduled_at parameter.

    Falls back to UTC if the airport isn't in AIRPORT_TIMEZONES.
    """
    tz_name = AIRPORT_TIMEZONES.get(airport.upper(), "UTC")
    tz = ZoneInfo(tz_name)
    now_local = datetime.now(tz)

    # Build today's 9 AM in local tz
    target = now_local.replace(hour=9, minute=0, second=0, microsecond=0)

    # If 9 AM has already passed today, roll to tomorrow
    if now_local >= target:
        target += timedelta(days=1)

    # Convert to UTC ISO string
    target_utc = target.astimezone(timezone.utc)
    return target_utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def candidate_dates(n: int = SAMPLE_DATES) -> list[str]:
    """
    Returns n evenly-spaced dates starting ~3 weeks from today.
    Once the wedding date is confirmed, replace with specific dates.
    """
    start = date.today() + timedelta(days=21)
    step  = SEARCH_DAYS // n
    return [(start + timedelta(days=i * step)).strftime("%Y-%m-%d") for i in range(n)]


def cheapest_flight(origin: str) -> dict | None:
    """
    Queries fast-flights across several sample dates and returns
    the cheapest result found, or None if all queries fail.
    """
    best = None
    for query_date in candidate_dates():
        try:
            result: Result = get_flights(
                flight_data=[FlightData(date=query_date, from_airport=origin, to_airport=DESTINATION)],
                trip="one-way",
                seat="economy",
                passengers=Passengers(adults=1),
                fetch_mode="fallback",
            )
            if not result or not result.flights:
                continue
            priced = [f for f in result.flights if f.price]
            if not priced:
                continue
            cheapest_today = min(priced, key=lambda f: f.price)
            if best is None or cheapest_today.price < best["price"]:
                best = {
                    "date":        query_date,
                    "price":       cheapest_today.price,
                    "airline":     cheapest_today.name,
                    "duration":    cheapest_today.duration,
                    "stops":       cheapest_today.stops,
                    "price_level": result.current_price,
                    "origin":      origin,
                }
        except Exception as e:
            print(f"  ⚠ Query failed for {origin} on {query_date}: {e}")
        time.sleep(SLEEP_BETWEEN)
    return best


def google_flights_url(origin: str) -> str:
    return f"https://www.google.com/travel/flights/search?q=flights+from+{origin}+to+{DESTINATION}"


def price_emoji(level: str) -> str:
    return {"low": "🟢", "typical": "🟡", "high": "🔴"}.get(level, "✈️")


def build_email(name: str, flight: dict) -> str:
    first      = name.split()[0]
    emoji      = price_emoji(flight["price_level"])
    stops_text = "direct" if flight["stops"] == 0 else f"{flight['stops']} stop(s)"
    gf_link    = google_flights_url(flight["origin"])
    return f"""
    <div style="font-family: Georgia, serif; max-width: 600px; margin: auto; color: #2c2c2c;">
      <h2 style="color: #8b5e3c;">✈️ Lisbon Flight Update — Sparks Wedding 2027</h2>
      <p>Hi {first},</p>
      <p>Just a friendly nudge — we want to make sure everyone can get to Portugal without
      paying through the nose for last-minute flights! Here's what we found flying out of
      <strong>{flight["origin"]}</strong>:</p>
      <table style="border-collapse: collapse; width: 100%; margin: 20px 0;">
        <tr style="background: #f9f3ee;">
          <td style="padding: 10px; font-weight: bold;">Best price found</td>
          <td style="padding: 10px;">{emoji} <strong>{flight["price"]}</strong> ({flight["price_level"]})</td>
        </tr>
        <tr>
          <td style="padding: 10px; font-weight: bold;">Sample date</td>
          <td style="padding: 10px;">{flight["date"]}</td>
        </tr>
        <tr style="background: #f9f3ee;">
          <td style="padding: 10px; font-weight: bold;">Airline</td>
          <td style="padding: 10px;">{flight["airline"]}</td>
        </tr>
        <tr>
          <td style="padding: 10px; font-weight: bold;">Duration</td>
          <td style="padding: 10px;">{flight["duration"]} ({stops_text})</td>
        </tr>
      </table>
      <p>Prices will only go up as the date gets closer — if you see a fare you're happy
      with, book it! The airport closest to the venue is <strong>Lisbon (LIS)</strong>.</p>
      <p style="text-align: center; margin: 30px 0;">
        <a href="{gf_link}"
           style="background: #8b5e3c; color: white; padding: 12px 24px;
                  text-decoration: none; border-radius: 6px; font-size: 16px;">
          Search Flights on Google →
        </a>
      </p>
      <p style="color: #888; font-size: 13px;">
        You're receiving this because you're invited to Isabella & Julie's wedding —
        we'll keep sending monthly updates until flights are booking out to the wedding window.
        Reply to this email with any questions about travel. 💛
      </p>
    </div>
    """


def load_guests(path: str = "guests.csv") -> list[dict]:
    """Loads guests from CSV and flattens plus ones into separate rows."""
    guests = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            guests.append({
                "name":    row["name"],
                "email":   row["email"],
                "airport": row["airport"],
            })
            if row.get("plus_one_name") and row.get("plus_one_email"):
                guests.append({
                    "name":    row["plus_one_name"],
                    "email":   row["plus_one_email"],
                    "airport": row["airport"],
                })
    return guests


def send_email(to_name: str, to_email: str, flight: dict) -> bool:
    html         = build_email(to_name, flight)
    scheduled_at = next_9am_utc(flight["origin"])

    if DRY_RUN:
        tz_name = AIRPORT_TIMEZONES.get(flight["origin"].upper(), "UTC")
        print(f"\n  DRY RUN — would send to {to_name} <{to_email}>")
        print(f"    {flight['origin']} → LIS | {flight['price_level']} | {flight['price']} on {flight['date']}")
        print(f"    Scheduled for: {scheduled_at} UTC (9 AM {tz_name})")
        return True

    payload = {
        "from":    SENDER,
        "to":      f"{to_name} <{to_email}>",
        "subject": "✈️ Flights to Lisbon — book before prices rise!",
        "html":    html,
    }

    # Try scheduled send first; fall back to immediate if plan doesn't support it
    try:
        payload["scheduled_at"] = scheduled_at
        resend.Emails.send(payload)
        print(f"  ✓ Scheduled for {scheduled_at} UTC → {to_email}")
        return True
    except Exception as e:
        if "scheduled" in str(e).lower():
            print(f"  ⚠ Scheduled send not supported on this plan — sending immediately")
            try:
                del payload["scheduled_at"]
                resend.Emails.send(payload)
                print(f"  ✓ Sent immediately → {to_email}")
                return True
            except Exception as e2:
                print(f"  ✗ Failed to send to {to_email}: {e2}")
                return False
        print(f"  ✗ Failed to send to {to_email}: {e}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print("Sparks Wedding Flight Alert")
    print(f"  Mode: {'DRY RUN' if DRY_RUN else 'LIVE'}\n")

    guests = load_guests()
    print(f"  {len(guests)} recipients loaded (guests + plus ones)\n")

    flight_cache: dict[str, dict | None] = {}
    sent   = 0
    failed = 0

    for guest in guests:
        origin = guest["airport"].upper()
        if origin not in flight_cache:
            print(f"Searching {origin} → {DESTINATION}...")
            flight_cache[origin] = cheapest_flight(origin)

        flight = flight_cache[origin]
        if not flight:
            print(f"  ⚠ No results for {origin} — skipping {guest['name']}")
            failed += 1
            continue

        print(f"Sending to {guest['name']} ({guest['email']})...")
        success = send_email(guest["name"], guest["email"], flight)
        sent   += 1 if success else 0
        failed += 0 if success else 1
        time.sleep(1)

    print(f"\n✅ Done — {sent} sent, {failed} failed")

if __name__ == "__main__":
    main()
