import os
import json
import time
import random
import re
import html
import asyncio
import mimetypes
from html.parser import HTMLParser
import requests
import nats
from typing import Optional, Dict, Any, List, Tuple

# Configuration Defaults
BASE_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL", "1800"))  # Default baseline: 30mins

# NATS — the scraper is a pure producer: it publishes alerts to NATS and the
# whatsapp-server-api consumer delivers them. Text and media use separate subjects.
NATS_URL = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
NATS_TEXT_SUBJECT = os.getenv("NATS_SUBJECT", "notify.whatsapp")
NATS_MEDIA_SUBJECT = os.getenv("NATS_MEDIA_SUBJECT", "notify.whatsapp.media")
NATS_PUBLISH_TIMEOUT_SEC = int(os.getenv("NATS_PUBLISH_TIMEOUT_SEC", "10"))
# Recipient forwarded verbatim by the gateway; empty → gateway's GROUP_ID default.
WHATSAPP_TO = os.getenv("WHATSAPP_TO", "")

# Location of tracking file relative to execution path
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "last_processed_msg.json")


# -------------------------------------------------------------------------
# NATS PUBLISH HELPERS (sync wrappers over the async nats-py client)
# -------------------------------------------------------------------------
async def _publish_async(subject: str, data: bytes, headers: Optional[Dict[str, str]]) -> None:
    nc = await nats.connect(NATS_URL, name="school-scraper", connect_timeout=10)
    try:
        js = nc.jetstream()
        await asyncio.wait_for(
            js.publish(subject, data, headers=headers),
            timeout=NATS_PUBLISH_TIMEOUT_SEC,
        )
    finally:
        await nc.drain()


def publish_nats(subject: str, data: bytes, headers: Optional[Dict[str, str]] = None) -> bool:
    """
    Publish one message to NATS JetStream. Connects fresh each call — fine for
    this low-volume scraper (a few messages per 30-min cycle). Returns True on a
    confirmed JetStream ack, False otherwise (logged, non-fatal).
    """
    try:
        asyncio.run(_publish_async(subject, data, headers or None))
        return True
    except Exception as e:
        print(f"   ❌ NATS publish to {subject} failed: {e}")
        return False


# -------------------------------------------------------------------------
# MULTI-ACCOUNT CREDENTIALS CONFIGURATION MATRIX
# -------------------------------------------------------------------------
def load_student_accounts() -> List[Dict[str, str]]:
    """
    Parses isolated credentials for multiple students using Option A format.
    Looks for sequential individual environment blocks.
    """
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


# -------------------------------------------------------------------------
# NATIVE ADAPTIVE HTML TO MARKDOWN PARSING ENGINE
# -------------------------------------------------------------------------
class EdumergeHTMLParser(HTMLParser):
    """Parses raw school HTML notice pages into WhatsApp Markdown and isolates file attachments."""

    def __init__(self):
        super().__init__()
        self.text_chunks: List[str] = []
        self.attachment_urls: List[str] = []
        self.in_bold = False
        self.current_link_url = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, str]]) -> None:
        attr_dict = dict(attrs)

        if tag in ['strong', 'b', 'u']:
            self.in_bold = True
            self.text_chunks.append("*")
        elif tag in ['br', 'p', 'tr']:
            self.text_chunks.append("\n")
        elif tag == 'div':
            if self.text_chunks and not self.text_chunks[-1].endswith("\n"):
                self.text_chunks.append("\n")
        elif tag == 'a':
            href = attr_dict.get('href', '')
            if href:
                # Differentiate physical documents from standard informational website links
                if any(href.lower().endswith(ext) for ext in ['.pdf', '.png', '.jpg', '.jpeg', '.docx', '.xlsx']):
                    self.attachment_urls.append(href)
                else:
                    self.current_link_url = href

    def handle_endtag(self, tag: str) -> None:
        if tag in ['strong', 'b', 'u']:
            self.in_bold = False
            if self.text_chunks and self.text_chunks[-1] == " ":
                self.text_chunks.pop()
                self.text_chunks.append("* ")
            else:
                self.text_chunks.append("*")
        elif tag == 'a':
            if self.current_link_url:
                self.text_chunks.append(f" ({self.current_link_url}) ")
                self.current_link_url = None

    def handle_data(self, data: str) -> None:
        clean_data = html.unescape(data)
        if clean_data.strip() or " " in clean_data:
            self.text_chunks.append(clean_data)

    def get_clean_payload(self) -> Tuple[str, List[str]]:
        """Returns the fully compiled markdown text body along with discovered download URLs."""
        raw_text = "".join(self.text_chunks)
        # Normalize redundant space structures and line break fragments
        text_with_normalized_breaks = re.sub(r'\n\s*\n+', '\n\n', raw_text)
        return text_with_normalized_breaks.strip(), self.attachment_urls


# -------------------------------------------------------------------------
# STATEFUL EDUMERGE SCRAPER ENGINE
# -------------------------------------------------------------------------
class EdumergeScraper:
    """An encapsulated stateful scraper client for a specific Edumerge student account lifecycle."""

    def __init__(self, username: str, password: str, display_name: str) -> None:
        if not username or not password:
            raise ValueError(f"CRITICAL: Credentials missing for student profile: {display_name}")

        self.username = username
        self.password = password
        self.display_name = display_name

        self.session = requests.Session()
        self.session.headers.update({
            'Host': 'app.edumerge.com',
            'User-Agent': 'okhttp/4.12.0',
            'Accept-Encoding': 'gzip'
        })

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
                print(f"   ❌ Login failed for {self.display_name}: Invalid response frame schema.")
                return False

            self.session_ctx.update({
                'em_unique_id': data.get('emuniqueid'),
                'school_id': data.get('schoolid'),
                'profile_id': data.get('profileid'),
                'si_data': data.get('_si'),
                'emidinfo_str': data.get('emidinfo'),
                'userid': self.username
            })
            print(f"   -> Success. Session authenticated cleanly.")
            return True
        except Exception as e:
            print(f"   ❌ Exception during login phase for {self.display_name}: {e}")
            return False

    def initialize_dashboard_context(self) -> None:
        """Steps 3 through 3_C: Register interface footprints and sync engine components matrix."""
        path_url = "https://app.edumerge.com/V2/newDashboard/dashboard/src/server/Login/session_wrapper.php"
        self.session.post(path_url, files={'reqType': (None, 'getServerPath')})

        populate_url = "https://app.edumerge.com/V2/security/ui/populateselectuser.php"
        populate_res = self.session.post(populate_url, files={'emidinfo': (None, self.session_ctx['emidinfo_str'])})

        try:
            profiles = populate_res.json()
            print(f"   -> Primary Server Profile: {profiles[0].get('text') if profiles else 'Unknown'}")
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

    def parse_and_format_content(self, event_data: Dict[str, Any]) -> Tuple[str, List[str]]:
        """
        Leverages native parser mechanics to process text bodies, while fallback-checking
        structured metadata keys to guarantee file attachments are caught across both Edumerge layouts.
        """
        title = event_data.get("mtitle", "New School Notice").strip()
        raw_body = event_data.get("msgbody", "")
        date_sent = event_data.get("content_date", {}).get("full_date", "Recently")

        # --- PASS 1: Parse the main HTML text body ---
        parser = EdumergeHTMLParser()
        parser.feed(raw_body)
        clean_body, attachment_urls = parser.get_clean_payload()

        # Convert list to a set to eliminate duplicate assets automatically
        final_attachments = set(attachment_urls)

        # --- PASS 2: Metadata Fallback Collection Engine ---
        # Strategy A: Check legacy singular flat attachment keys
        single_link = event_data.get("attachment1_link") or event_data.get("attachment1_link_full_path")
        if single_link and isinstance(single_link, str) and single_link.strip():
            # Filter out broken placeholders or stringified null tokens
            if "null" not in single_link.lower() and single_link.startswith("http"):
                final_attachments.add(single_link.strip())

        # Strategy B: Iterate over modern arrays if exposed by the server instance
        array_links = event_data.get("attachments_array", [])
        if isinstance(array_links, list):
            for entry in array_links:
                if isinstance(entry, dict):
                    link = entry.get("attachment_link")
                    if link and isinstance(link, str) and link.startswith("http"):
                        final_attachments.add(link.strip())

        # Clean formatting text anomalies
        clean_body = clean_body.replace("\u2022", "• ")

        # Clean up common signature footers from the text to keep alerts crisp
        clean_body = re.sub(r'(?i)Thanks\s*&\s*Regards.*', '', clean_body).strip()

        formatted_text = (
            f"🏫 *SCHOOL NOTICE ALERT ({self.display_name.upper()})*\n"
            f"📅 *Posted:* {date_sent}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 *Subject:* {title}\n\n"
            f"{clean_body}"
        )

        # Return text payload alongside a clean, unique list of URLs
        return formatted_text, list(final_attachments)

    def fetch_latest_classroom_chats(self) -> List[Dict[str, Any]]:
        """
        Queries the Academics room manager to fetch all chat groups, filtering
        for rooms that have received a message update matching today's date.
        """
        url = "https://app.edumerge.com/V2/react-native/newServer/Academics/Wrapper.php"
        payload = {'reqType': (None, 'getRecentUsers')}

        try:
            response = self.session.post(url, files=payload, timeout=20)
            groups = response.json().get('data', [])
            if not groups or not isinstance(groups, list):
                return []

            # Dynamically calculate today's date in 'DD-MM-YYYY' matching the string structure
            today_str = time.strftime('%d-%m-%Y')
            active_today_groups = []

            for group in groups:
                g_id = group.get('groupID')
                created_at_str = group.get('created_at')  # Expected: "17-06-2026 15:34:15"

                if g_id and created_at_str:
                    # Extract just the date component (first 10 characters: "17-06-2026")
                    msg_date = created_at_str.split()[0] if " " in created_at_str else created_at_str[:10]

                    if msg_date == today_str:
                        active_today_groups.append(group)

            # Sort chronologically by lastMessageTime so the newest messages are evaluated first
            active_today_groups.sort(key=lambda x: x.get('lastMessageTime', ''), reverse=True)
            return active_today_groups

        except Exception as e:
            print(f"   ❌ Exception fetching classroom group list for {self.display_name}: {e}")
            return []

    def fetch_room_messages(self, group_id: str) -> Optional[Dict[str, Any]]:
        """
        Polls the chat frame logs inside a specific subject room container
        to isolate the single newest message metadata entry object.
        """
        url = "https://app.edumerge.com/V2/react-native/newServer/Academics/chat_wrapper.php"
        payload = {
            'reqType': (None, 'getUserChats'),
            'receiverID': (None, 'undefined'),
            'currentPage': (None, '1'),
            'recordsPerPage': (None, '10'),
            'replyMessageId': (None, '0'),
            'groupID': (None, group_id),
            'isOwner': (None, '0'),
            'filterString': (None, '')
        }

        try:
            response = self.session.post(url, files=payload, timeout=20)
            chats_matrix = response.json().get('data', {}).get('chatsArray', [])
            if not chats_matrix or not isinstance(chats_matrix, list):
                return None

            # Flatten Edumerge's nested arrays: structural responses enclose internal chat tracks inside individual wrappers
            first_row = chats_matrix[0]
            if isinstance(first_row, list) and len(first_row) > 0:
                return first_row[0]
            elif isinstance(first_row, dict):
                return first_row
            return None
        except Exception as e:
            print(f"   ❌ Exception pulling text streams for group {group_id}: {e}")
            return None

    @staticmethod
    def format_chat_message(student_name: str, group_name: str, chat_node: Dict[str, Any]) -> str:
        """Compiles chat message records cleanly into consistent WhatsApp layout wrappers."""
        subject = chat_node.get("subject", "Classroom Update").strip()
        message = chat_node.get("message", "").strip()
        teacher = chat_node.get("Fname", "Class Teacher").strip()
        time_info = chat_node.get("publishedDateTime", "Recently")

        return (
            f"💬 *CLASSROOM CHAT ALERT ({student_name.upper()})*\n"
            f"👥 *Group:* {group_name}\n"
            f"👤 *From:* {teacher}\n"
            f"📅 *Sent:* {time_info}\n"
            f"━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 *Topic:* {subject}\n\n"
            f"{message}"
        )

    @staticmethod
    def broadcast_text(message_text: str, dedup_id: Optional[str] = None) -> None:
        """Publish a text alert to NATS (notify.whatsapp). The gateway consumer delivers it."""
        payload = json.dumps({"to": WHATSAPP_TO, "text": message_text}).encode()
        headers = {"Nats-Msg-Id": f"text-{dedup_id}"} if dedup_id else None
        if publish_nats(NATS_TEXT_SUBJECT, payload, headers):
            print("   ✅ Text alert published to NATS.")

    def process_and_send_attachments(self, attachment_urls: List[str], dedup_prefix: str = "") -> None:
        """Download each attachment into memory and publish it to NATS (notify.whatsapp.media)."""
        for url in attachment_urls:
            filename = url.split('/')[-1] or "attachment"
            print(f"   📥 Downloading notice attachment from school portal: {filename}, from {url}...")
            download_headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36',
                "Referer": "app.edumerge.com",
            }
            try:
                # Fetch asset stream keeping parameters coupled inside the current child's auth session
                file_res = requests.get(url, headers=download_headers, timeout=30)
                if file_res.status_code != 200:
                    print(f"   ⚠️ Attachment asset download returned failure status code: {file_res.status_code}")
                    continue

                content = file_res.content
                # Prefer the server's Content-Type, else guess from the filename.
                ctype = (file_res.headers.get("Content-Type") or "").split(";")[0].strip()
                mimetype = ctype or mimetypes.guess_type(filename)[0] or "application/octet-stream"

                headers = {
                    "To": WHATSAPP_TO,
                    "X-Filename": filename,
                    "X-Mimetype": mimetype,
                    "Nats-Msg-Id": f"media-{dedup_prefix}-{filename}",
                }
                print(f"   📤 Publishing attachment to NATS ({len(content)} bytes, {mimetype})...")
                if publish_nats(NATS_MEDIA_SUBJECT, content, headers):
                    print(f"   ✅ Attachment [{filename}] published to NATS media subject.")
            except Exception as e:
                print(f"   ❌ Failed to download/publish attachment: {e}")

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
            print(f"   -> Session authentication footprints torn down cleanly for {self.display_name}.")
        except Exception:
            pass


# -------------------------------------------------------------------------
# PERSISTENT LOCAL STORAGE TRACKING OPERATIONS
# -------------------------------------------------------------------------
def load_all_processed_states() -> Dict[str, Any]:
    """Reads historical tracking cursors from disk mapped by unique child names."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r") as f:
                saved_data = json.load(f)
                if "last_msg_id" in saved_data:
                    return {}  # Erase obsolete single-student schemas safely
                return saved_data
        except (json.JSONDecodeError, IOError):
            print("⚠️ Warning: State tracker file corrupted or unreadable. Initializing default baseline map.")
    return {}


def save_student_processed_id(student_key: str, state_payload: Dict[str, Any]) -> None:
    """Commits complex structured tracking status blocks per child back to volume file storage."""
    current_states = load_all_processed_states()
    current_states[student_key] = state_payload
    current_states["_updated_at"] = time.time()

    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            json.dump(current_states, f, indent=2)
        print(f"   -> Disk cursor matrix tracking synchronized safely for [{student_key}].")
    except IOError as e:
        print(f"❌ Failed to commit updated historical tracking criteria state parameters to disk: {e}")


# -------------------------------------------------------------------------
# CORE RUNTIME TRANSACTION EXECUTION LIFECYCLE
# -------------------------------------------------------------------------
def run_pipeline() -> None:
    """Instantiates sequentially mapped execution loops across all active child accounts for Notices and Chats."""
    print(f"\n🔄 [{time.strftime('%Y-%m-%d %H:%M:%S')}] Launching automated account audit pass...")

    student_accounts = load_student_accounts()
    tracking_history = load_all_processed_states()

    for account in student_accounts:
        name = account.get("name")
        username = account.get("user")
        password = account.get("pass")

        if not username or not password:
            print(f"⚠️ Skipping execution lane for '{name}': Login environment matrices not initialized.")
            continue

        print(f"\n🚀 Processing isolated tracking stream for student: {name}")
        scraper = EdumergeScraper(username, password, name)

        try:
            if not scraper.login():
                continue

            scraper.initialize_dashboard_context()
            time.sleep(1.5)

            # Extract child state dictionaries from persistent state store
            student_state = tracking_history.get(name, {})
            if not isinstance(student_state, dict):
                student_state = {"notice_id": student_state, "chat_id": None}

            # =========================================================================
            # ENGINE PASS A: REGULAR SYSTEM PORTAL NOTICES
            # =========================================================================
            latest_notice = scraper.fetch_latest_feed_notice()
            if latest_notice and latest_notice.get('msgid'):
                notice_id = int(latest_notice['msgid'])
                last_seen_notice = student_state.get("notice_id")

                if last_seen_notice != notice_id:
                    print(f"   ✨ New global notice discovered ({notice_id})! Processing content...")
                    event_details = scraper.fetch_notice_event_details(notice_id)
                    formatted_alert, attachment_list = scraper.parse_and_format_content(event_details)
                    # print("formatted_alert : ", formatted_alert)
                    # NOTE: dedup_id is scoped to this student (name + notice_id), not just
                    # notice_id. The same school-wide notice is legitimately re-sent once
                    # per linked student account (e.g. to different WhatsApp groups); a
                    # dedup_id shared across students caused JetStream to silently drop
                    # every publish after the first one for the same notice_id.
                    scraper.broadcast_text(formatted_alert, dedup_id=f"notice-{name}-{notice_id}")
                    if attachment_list:
                        print("Found attachments in the message.")
                        scraper.process_and_send_attachments(
                            attachment_list, dedup_prefix=f"{name}-{notice_id}"
                        )
                    else:
                        print("No attachments in the message.")

                    student_state["notice_id"] = notice_id
                    save_student_processed_id(name, student_state)

            time.sleep(1.5)

            # =========================================================================
            # ENGINE PASS B: CLASSROOM CHAT ROOMS & HOMEWORK (TODAY ONLY)
            # =========================================================================
            chat_groups = scraper.fetch_latest_classroom_chats()
            if chat_groups:
                print(f"   ✨ Found {len(chat_groups)} classroom group(s) updated today.")

                for target_group in chat_groups:
                    g_id = target_group.get("groupID")
                    g_name = target_group.get("groupName", "Subject Room")

                    chat_node = scraper.fetch_room_messages(g_id)
                    if chat_node and chat_node.get("academicId"):
                        academic_msg_id = int(chat_node["academicId"])

                        # Use a group-specific sub-key to track seen messages cleanly
                        # Example dictionary structure: {"notice_id": 3807, "chat_tracks": {"group_abc": 6998}}
                        if "chat_tracks" not in student_state or not isinstance(student_state["chat_tracks"], dict):
                            student_state["chat_tracks"] = {}

                        last_seen_chat = student_state["chat_tracks"].get(g_id)

                        print(
                            f"   ↳ Chat Check [{g_name}]: ID={academic_msg_id} | Dispatched History ID={last_seen_chat}")

                        if last_seen_chat != academic_msg_id:
                            print(f"   🚀 Distributing fresh classroom update from [{g_name}] room...")
                            formatted_chat = scraper.format_chat_message(name, g_name, chat_node)
                            # print("formatted_chat: ", formatted_chat)
                            # Publish out via NATS (gateway consumer delivers)
                            scraper.broadcast_text(formatted_chat, dedup_id=f"chat-{g_id}-{academic_msg_id}")

                            # Commit specific ID change to state tracking
                            student_state["chat_tracks"][g_id] = academic_msg_id
                            save_student_processed_id(name, student_state)
                        else:
                            print(f"   🛑 Chat message ID matches historical records for group {g_name}.")
            else:
                print(f"   ℹ️ No classroom chat updates detected for {name} today.")

        except Exception as err:
            print(
                f"   ❌ Operational exception breakdown encountered during tracking lifecycle execution flow for {name}: {err}")
        finally:
            scraper.logout()
            time.sleep(2)  # Defensive pacing window across sequential student context profiles


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

