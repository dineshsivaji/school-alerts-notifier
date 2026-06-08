import os
import json
import time
import random
import requests
import html
import re
from typing import Optional, Dict, Any, List

# Configuration Defaults
BAILEYS_URL = os.getenv("BAILEYS_URL", "http://localhost:3001/send")
BASE_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "3600"))  # Default baseline: 1 hour

# Location of tracking file relative to execution path
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "last_processed_msg.json")


# -------------------------------------------------------------------------
# MULTI-ACCOUNT CREDENTIALS CONFIGURATION
# -------------------------------------------------------------------------
def load_student_accounts() -> List[Dict[str, str]]:
    """
    Parses credentials for multiple students.
    Can read from a JSON environment variable or look for individual env blocks.
    """
    # Prefer a structured JSON array string from env for ultimate flexibility:
    # EDUMERGE_ACCOUNTS='[{"name": "Vennila", "user": "user1", "pass": "pass1"}, {"name": "Surya", "user": "user2", "pass": "pass2"}]'
    accounts_json = os.getenv("EDUMERGE_ACCOUNTS")
    if accounts_json:
        try:
            return json.loads(accounts_json)
        except Exception as e:
            print(f"❌ Failed to parse EDUMERGE_ACCOUNTS JSON env: {e}")

    # Fallback to explicit single env variables or defaults if JSON isn't configured yet
    return [
        {
            "name": os.getenv("STUDENT_1_NAME", "Vennila"),
            "user": os.getenv("EDUMERGE_USERID_1"),
            "pass": os.getenv("EDUMERGE_PASSWORD_1")
        },
        {
            "name": os.getenv("STUDENT_2_NAME", "Surya"),
            "user": os.getenv("EDUMERGE_USERID_2"),
            "pass": os.getenv("EDUMERGE_PASSWORD_2")
        }
    ]


class EdumergeScraper:
    """An encapsulated stateful scraper client for a specific Edumerge student account lifecycle."""

    def __init__(self, username: str, password: str, display_name: str) -> None:
        if not username or not password:
            raise ValueError(f"CRITICAL: Credentials missing for student student: {display_name}")

        self.username = username
        self.password = password
        self.display_name = display_name

        self.session = requests.Session()
        self.session.headers.update({
            'Host': 'app.edumerge.com',
            'User-Agent': 'okhttp/4.12.0',
            'Accept-Encoding': 'gzip'
        })

        # State tracking store for authentication session dependencies
        self.session_ctx: Dict[str, Any] = {}

    def login(self) -> bool:
        """Step 2: Authenticate and cache context tokens."""
        print(f"   🔑 Logging into account for {self.display_name}...")
        url = "https://app.edumerge.com/V2/react-native/newServer/Account/wrapper.php"
        payload = {
            'reqType': (None, 'loginUser'),
            'userid': (None, self.username),
            'password': (None, self.password),
            'is_google_login': (None, 'false'),
            'is_apple_login': (None, 'false'),
            'google_login_token': (None, 'null'),
            'appleLoginAuthorizationCode': (None, 'null'),
            'user_access_key': (None, 'null'),
            'is_remember_user': (None, '1')
        }

        try:
            response = self.session.post(url, files=payload)
            data = response.json()

            if not data.get('emuniqueid'):
                print(f"   ❌ Login failed for {self.display_name}: Invalid response schema.")
                return False

            self.session_ctx.update({
                'em_unique_id': data.get('emuniqueid'),
                'school_id': data.get('schoolid'),
                'profile_id': data.get('profileid'),
                'si_data': data.get('_si'),
                'emidinfo_str': data.get('emidinfo'),
                'userid': self.username
            })
            print(f"   -> Success. EMUniqueId secured.")
            return True
        except Exception as e:
            print(f"   ❌ Exception during login for {self.display_name}: {e}")
            return False

    def initialize_dashboard_context(self) -> None:
        """Steps 3 through 3_C: Register interface footprints and sync engine components matrix."""
        path_url = "https://app.edumerge.com/V2/newDashboard/dashboard/src/server/Login/session_wrapper.php"
        self.session.post(path_url, files={'reqType': (None, 'getServerPath')})

        populate_url = "https://app.edumerge.com/V2/security/ui/populateselectuser.php"
        populate_res = self.session.post(populate_url, files={'emidinfo': (None, self.session_ctx['emidinfo_str'])})

        try:
            profiles = populate_res.json()
            print(f"   -> Primary Profile: {profiles[0].get('text') if profiles else 'Unknown'}")
        except Exception:
            pass

        modules_url = "https://app.edumerge.com/V2/react-native/newServer/Modules/modules_wrapper.php"
        modules_payload = {
            'reqType': (None, 'getModules'),
            'deviceOS': (None, 'android'),
            'appVersion': (None, '4.0.0'),
            'appBuildNo': (None, '4'),
            'appName': (None, 'MLZSUNITE'),
            'deviceBrand': (None, 'google'),
            'deviceId': (None, 'goldfish_arm64')
        }
        self.session.post(modules_url, files=modules_payload)

        home_url = "https://app.edumerge.com/V2/react-native/newServer/HomeScreen/home_screen_wrapper.php"
        self.session.post(home_url, files={'reqType': (None, 'getUserCardInfo')})
        self.session.post(home_url, files={'reqType': (None, 'getInItData')})

    def fetch_latest_feed_notice(self) -> Optional[Dict[str, Any]]:
        """Step 5: Fetch chronological feeds list and isolate the newest active notice element."""
        feeds_url = "https://app.edumerge.com/V2/react-native/newServer/Feed/feeds_wrapper_new.php"
        feeds_payload = {
            'req_type': (None, 'get_feeds_new'),
            'number_of_records_to_send': (None, '20'),
            'tbl_unread_count_log_flag': (None, '0'),
            'limit_from': (None, '0'),
            'no_record_found_flag': (None, '0')
        }

        response = self.session.post(feeds_url, files=feeds_payload)
        feeds_list = response.json().get('feeds_data', [])

        if not feeds_list or not isinstance(feeds_list, list):
            return None

        latest_feed_item = feeds_list[0]
        notices = latest_feed_item.get('notices', [])
        return notices[0] if notices and isinstance(notices, list) else None

    def fetch_notice_event_details(self, msg_id: int) -> Dict[str, Any]:
        """Step 5_A: Invoke internal view_event query mapping."""
        view_event_url = 'https://app.edumerge.com/V2/noticecalendar/server/view_event.php'
        headers = {'accept': 'application/json'}
        payload = {"id": msg_id, "react_check": True}
        response = self.session.post(view_event_url, headers=headers, json=payload)
        return response.json()

    def format_whatsapp_markdown(self, event_data: Dict[str, Any]) -> str:
        """Parses internal HTML text line structures into high-readability markdown flags."""
        title = event_data.get("mtitle", "New School Notice").strip()
        raw_body = event_data.get("msgbody", "")
        date_sent = event_data.get("content_date", {}).get("full_date", "Recently")

        # 1. Convert explicit line breaks
        clean_body = raw_body.replace("<br/>", "\n").replace("<br>", "\n").replace("<br />", "\n")

        # 2. Handle paragraphs: replace closing tags with a newline, strip opening tags
        clean_body = clean_body.replace("</p>", "\n").replace("<p>", "")

        # 3. Convert HTML bold tags to WhatsApp markdown stars (*text*)
        clean_body = clean_body.replace("<strong>", "*").replace("</strong>", "*")
        clean_body = clean_body.replace("<b>", "*").replace("</b>", "*")

        # 4. Strip any remaining rogue HTML tags (like <span>, <div> etc. if they pop up)
        clean_body = re.sub(r'<[^>]+>', '', clean_body)

        # 5. Decode HTML entities (converts &nbsp; to spaces, &amp; to &, etc.)
        clean_body = html.unescape(clean_body)

        # 6. Normalize list bullets and clean up trailing whitespace
        clean_body = clean_body.replace("\u2022", "• ")
        clean_body = clean_body.strip()

        return (
            f"🏫 *SCHOOL NOTICE ALERT ({self.display_name.upper()})*\n"
            f"📅 *Posted:* {date_sent}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 *Subject:* {title}\n\n"
            f"{clean_body}"
        )

    @staticmethod
    def broadcast_via_baileys(message_text: str) -> None:
        """Dispatches an HTTP JSON POST payload stream to the local Baileys engine daemon."""
        payload = {"message": message_text}
        try:
            res = requests.post(BAILEYS_URL, json=payload, timeout=10)
            if res.status_code == 200:
                print("   ✅ Success! Notice successfully broadcasted via Baileys.")
            else:
                print(f"   ⚠️ Gateway rejected message stream. Status: {res.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"   ❌ Communication pipeline failure reaching Baileys: {e}")

    def logout(self) -> None:
        """Step 6: Execute modular multipart disconnection cleanup sequence."""
        if not self.session_ctx:
            return
        url = 'https://app.edumerge.com/V2/react-native/newServer/Account/LogOut.php'

        reg_payload = [{
            "EMUniqueId": self.session_ctx['em_unique_id'],
            "app_user_id": str(self.session_ctx['profile_id']),
            "school_db": f"EM{self.session_ctx['school_id']}"
        }]

        saved_data_payload = {
            "userid": self.session_ctx['userid'],
            "is_google_login": False,
            "is_apple_login": False,
            "google_login_token": None,
            "appleLoginAuthorizationCode": None,
            "user_access_key": None,
            "_si": self.session_ctx['si_data']
        }

        multipart_payload = {
            'reg': (None, json.dumps(reg_payload)),
            'saved_data': (None, json.dumps(saved_data_payload))
        }
        try:
            self.session.post(url, files=multipart_payload)
            print(f"   -> Session torn down for {self.display_name}.")
        except Exception:
            pass


# -------------------------------------------------------------------------
# PERSISTENT LOCAL STORAGE TRACKING OPERATIONS
# -------------------------------------------------------------------------
def load_all_processed_states() -> Dict[str, Any]:
    """Reads historical tracking cursor keys mapped by unique student labels."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                saved_data = json.load(f)
                if "last_msg_id" in saved_data:
                    return {}  # Wipe obsolete unified state formats smoothly
                return saved_data
        except (json.JSONDecodeError, IOError):
            print("⚠️ Warning: State file structure unreadable. Resetting boundaries.")
    return {}


def save_student_processed_id(student_key: str, msg_id: int) -> None:
    """Commits tracking status keys isolated per student back to the volume file storage."""
    current_states = load_all_processed_states()
    current_states[student_key] = msg_id
    current_states["_updated_at"] = time.time()

    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(current_states, f, indent=2)
        print(f"   -> Tracking synchronized for [{student_key}] with ID token: {msg_id}")
    except IOError as e:
        print(f"❌ Failed to commit state verification parameters to disk: {e}")


# -------------------------------------------------------------------------
# CORE EXECUTION PIPELINE UNIT
# -------------------------------------------------------------------------
def run_pipeline() -> None:
    """Instantiates sequentially mapped execution loops across all individual student accounts."""
    print(f"\n🔄 [{time.strftime('%Y-%m-%d %H:%M:%S')}] Launching automated multi-account verification pass...")

    student_accounts = load_student_accounts()
    tracking_history = load_all_processed_states()

    for account in student_accounts:
        name = account.get("name")
        username = account.get("user")
        password = account.get("pass")

        if not username or not password:
            print(f"⚠️ Skipping account profile configuration for '{name}': Username/Password not configured in env.")
            continue

        print(f"\n🚀 Processing isolated tracking stream for student: {name}")
        scraper = EdumergeScraper(username, password, name)

        try:
            if not scraper.login():
                continue

            scraper.initialize_dashboard_context()
            time.sleep(1.5)

            latest_notice = scraper.fetch_latest_feed_notice()
            if latest_notice and latest_notice.get('msgid'):
                notice_id = int(latest_notice['msgid'])
                last_seen_id = tracking_history.get(name)

                print(f"   ↳ State Check: Notice ID={notice_id} | Cursor History ID={last_seen_id}")

                if last_seen_id == notice_id:
                    print(f"   🛑 Notice ID matches history logs for {name}. Already handled.")
                else:
                    print(f"   ✨ New notification discovered for {name}! Extracting specific properties...")
                    time.sleep(1.5)
                    event_details = scraper.fetch_notice_event_details(notice_id)

                    # Formats alert explicitly reflecting current loop student
                    formatted_alert = scraper.format_whatsapp_markdown(event_details)
                    print(formatted_alert)
                    # Dispatch payload out to Baileys Engine
                    scraper.broadcast_via_baileys(formatted_alert)

                    # Update state data cursor isolated precisely to this loop kid name space
                    save_student_processed_id(name, notice_id)
            else:
                print(f"   ⚠️ Warning: No active layout feeds blocks discovered for {name}.")

        except Exception as err:
            print(f"   ❌ Automation flow failure encountered for {name}: {err}")
        finally:
            scraper.logout()
            time.sleep(2)  # Cooldown pacing window between distinct login sessions


# -------------------------------------------------------------------------
# INITIALIZATION RUNTIME CONTROL ENGINE
# -------------------------------------------------------------------------
if __name__ == "__main__":
    print("🚀 Edumerge multi-account sequential polling manager successfully initialized...")

    while True:
        current_hour = time.localtime().tm_hour

        # Night-mode constraints configuration mapping gate (22:00 to 06:00)
        if current_hour >= 22 or current_hour < 6:
            if current_hour >= 22:
                hours_to_wait = (24 - current_hour) + 6
            else:
                hours_to_wait = 6 - current_hour

            sleep_duration = hours_to_wait * 3600
            wake_time = time.strftime('%I:%M %p', time.localtime(time.time() + sleep_duration))

            print(f"🌙 Night-mode active (Current Hour: {current_hour}). Silencing background scrapers.")
            print(f"💤 Suspending thread into deep sleep for {hours_to_wait} hours. Waking up at: {wake_time}")

            time.sleep(sleep_duration)
            continue

        run_pipeline()

        # Add variable humanized Jitter matrix (Adds between -10 mins and +15 mins to baseline)
        jitter = random.randint(-600, 900)
        next_sleep_interval = BASE_INTERVAL_SECONDS + jitter

        sleep_minutes_metric = round(next_sleep_interval / 60, 1)
        target_wake_timestamp = time.strftime('%I:%M:%S %p', time.localtime(time.time() + next_sleep_interval))

        print(
            f"💤 Variable sleep window activated: Sleeping for {sleep_minutes_metric} minutes ({next_sleep_interval}s).")
        print(f"⏰ Next evaluation pass is scheduled to execute at approx: {target_wake_timestamp}")

        time.sleep(next_sleep_interval)