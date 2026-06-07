import os
import json
import time
import requests
from typing import Optional, Dict, Any

# Configuration Defaults
BAILEYS_URL = os.getenv("BAILEYS_URL", "http://localhost:3001/send")
EDUMERGE_USERID = os.getenv("EDUMERGE_USERID")
EDUMERGE_PASSWORD = os.getenv("EDUMERGE_PASSWORD")

# Persistent state file configuration on your mini PC storage layout
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "last_processed_msg.json")


class EdumergeScraper:
    """An encapsulated stateful scraper client for tracking modern Edumerge notice calendars."""

    def __init__(self) -> None:
        if not EDUMERGE_USERID or not EDUMERGE_PASSWORD:
            raise ValueError("CRITICAL: Environmental values 'EDUMERGE_USERID' or 'EDUMERGE_PASSWORD' are missing.")

        self.session = requests.Session()
        self.session.headers.update({
            'Host': 'app.edumerge.com',
            'User-Agent': 'okhttp/4.12.0',
            'Accept-Encoding': 'gzip'
        })

        # State tracking store for authentication session dependencies
        self.session_ctx: Dict[str, Any] = {}

    def login(self) -> None:
        """Step 2: Authenticate and cache context tokens."""
        print("Executing Step 2: User Login...")
        url = "https://app.edumerge.com/V2/react-native/newServer/Account/wrapper.php"
        payload = {
            'reqType': (None, 'loginUser'),
            'userid': (None, EDUMERGE_USERID),
            'password': (None, EDUMERGE_PASSWORD),
            'is_google_login': (None, 'false'),
            'is_apple_login': (None, 'false'),
            'google_login_token': (None, 'null'),
            'appleLoginAuthorizationCode': (None, 'null'),
            'user_access_key': (None, 'null'),
            'is_remember_user': (None, '1')
        }

        response = self.session.post(url, files=payload)
        data = response.json()

        self.session_ctx.update({
            'em_unique_id': data.get('emuniqueid'),
            'school_id': data.get('schoolid'),
            'profile_id': data.get('profileid'),
            'si_data': data.get('_si'),
            'emidinfo_str': data.get('emidinfo'),
            'userid': EDUMERGE_USERID
        })
        print(f"-> Logged in successfully. EMUniqueId: {self.session_ctx['em_unique_id']}")

    def initialize_dashboard_context(self) -> None:
        """Steps 3 through 3_C: Register interface footprints and sync engine components matrix."""
        print("\nExecuting Step 3: Synchronizing System Path and Profiles Matrix...")

        path_url = "https://app.edumerge.com/V2/newDashboard/dashboard/src/server/Login/session_wrapper.php"
        self.session.post(path_url, files={'reqType': (None, 'getServerPath')})

        populate_url = "https://app.edumerge.com/V2/security/ui/populateselectuser.php"
        populate_res = self.session.post(populate_url, files={'emidinfo': (None, self.session_ctx['emidinfo_str'])})
        profiles = populate_res.json()
        print(f"-> Context locked onto profile: {profiles[0].get('text') if profiles else 'Unknown'}")

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
        print("\nExecuting Step 5: Fetching feeds list...")
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
        print(f"-> Step 5 Status Code: {response.status_code}. Feeds Count: {len(feeds_list)}")

        if not feeds_list or not isinstance(feeds_list, list):
            return None

        latest_feed_item = feeds_list[0]
        notices = latest_feed_item.get('notices', [])
        return notices[0] if notices and isinstance(notices, list) else None

    def fetch_notice_event_details(self, msg_id: int) -> Dict[str, Any]:
        """Step 5_A: Invoke internal view_event query mapping using application/json context schema."""
        view_event_url = 'https://app.edumerge.com/V2/noticecalendar/server/view_event.php'
        headers = {'accept': 'application/json'}
        payload = {"id": msg_id, "react_check": True}

        response = self.session.post(view_event_url, headers=headers, json=payload)
        return response.json()

    @staticmethod
    def format_whatsapp_markdown(event_data: Dict[str, Any]) -> str:
        """Parses internal HTML text line structures into high-readability markdown flags."""
        title = event_data.get("mtitle", "New School Notice").strip()
        raw_body = event_data.get("msgbody", "")
        date_sent = event_data.get("content_date", {}).get("full_date", "Recently")

        clean_body = raw_body.replace("<br/>", "\n").replace("<br>", "\n")
        clean_body = clean_body.replace("\u2022", "• ")

        return (
            f"🏫 *SCHOOL NOTICE ALERT*\n"
            f"📅 *Posted:* {date_sent}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 *Subject:* {title}\n\n"
            f"{clean_body}"
        )

    @staticmethod
    def broadcast_via_baileys(message_text: str) -> bool:
        """Dispatches an HTTP JSON POST payload stream to the local Baileys engine daemon."""
        payload = {"message": message_text}
        try:
            print("-> Firing notification update payload to local bridge microservice...")
            res = requests.post(BAILEYS_URL, json=payload, timeout=10)
            if res.status_code == 200:
                print("✅ Success! Notice successfully broadcasted to your family WhatsApp group.")
                return True
            else:
                print(f"⚠️ Gateway rejected message stream. Status: {res.status_code}, Context: {res.text}")
        except requests.exceptions.RequestException as e:
            print(f"❌ Communication pipeline failure reaching Baileys microservice on {BAILEYS_URL}: {e}")
        return False

    def logout(self) -> None:
        """Step 6: Execute modular multipart disconnection cleanup sequence."""
        print("\nExecuting Step 6: Logout using extracted variables...")
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

        self.session.post(url, files=multipart_payload)
        print("-> Logout finalized.")


# -------------------------------------------------------------------------
# PERSISTENT LOCAL STATE HELPER FUNCTIONS
# -------------------------------------------------------------------------
def get_last_processed_id() -> Optional[int]:
    """Reads the last broadcasted message ID from local storage profile."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                return state.get("last_msg_id")
        except (json.JSONDecodeError, IOError):
            print("⚠️ Warning: State file corrupted or unreadable. Treating as first-time execution.")
    return None


def save_last_processed_id(msg_id: int) -> None:
    """Saves the successfully broadcasted message ID to local tracking file."""
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"last_msg_id": msg_id, "updated_at": time.time()}, f, indent=2)
        print(f"-> State updated. Token saved: {msg_id}")
    except IOError as e:
        print(f"❌ Failed to commit state verification parameters to disk: {e}")


# -------------------------------------------------------------------------
# EXECUTION LIFECYCLE
# -------------------------------------------------------------------------
if __name__ == "__main__":
    scraper = EdumergeScraper()
    try:
        scraper.login()
        scraper.initialize_dashboard_context()

        print("\nWaiting 5s before navigating tabs...")
        time.sleep(5)

        latest_notice = scraper.fetch_latest_feed_notice()

        if latest_notice and latest_notice.get('msgid'):
            notice_id = int(latest_notice['msgid'])
            last_seen_id = get_last_processed_id()

            print(f"\n-> Evaluation Engine: Current App Notice ID={notice_id} | Last Notified ID={last_seen_id}")

            # State Gate check: Stop right here if this announcement was already broadcasted
            if last_seen_id == notice_id:
                print(
                    "🛑 Notice ID matches history logs. WhatsApp alert already dispatched previously. Exiting pipeline.")
            else:
                print("\n✨ New notice discovered! Waiting 5s before processing view event...")
                time.sleep(5)

                print(f"-> Extracting details for Notice ID: {notice_id}")
                event_details = scraper.fetch_notice_event_details(notice_id)
                formatted_alert = scraper.format_whatsapp_markdown(event_details)

                # Fire to the WhatsApp server
                if scraper.broadcast_via_baileys(formatted_alert):
                    # Update our state file so we never send this ID again
                    save_last_processed_id(notice_id)
        else:
            print("⚠️ Warning: No valid notice data structures encountered in current feed block.")

    except Exception as run_error:
        print(f"❌ Automation pipeline runtime failure encountered: {run_error}")
    finally:
        print("\nCooling down thread architecture before session cleanup verification...")
        time.sleep(30)
        scraper.logout()