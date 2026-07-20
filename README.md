
# School Alert Notifier 🏫📱

An automated notification system that parses updates from the school portal using a Python scraper and pushes real-time alert notifications directly to a dedicated WhatsApp Group or individual phone numbers via a resilient Node.js WhatsApp Bridge microservice.

---

## Architecture Overview

The project is structured as a multi-container microservice application managed via Docker Compose:

1. **`whatsapp-bridge` (Node.js)**: A raw WebSocket gateway infrastructure utilizing the modern `@whiskeysockets/baileys` engine to spin up a persistent web client instance. It handles automatic cryptographic handshakes, multi-file session authentication persistence, and exposes an Express HTTP REST API to dispatch text messages.
2. **`python-scraper` (Python)**: An adaptive polling service that scrapes school updates, converts new notices into clean payloads, tracks state, and relays message blocks to the WhatsApp Bridge. It respects timezone constraints (configured for IST) to dynamically switch polling schedules during night hours.

---

## Features

* **No Browser Overhead**: The WhatsApp Bridge communicates directly via native WebSockets using raw network packet manipulation (no heavy Chromium/Puppeteer footprint required).
* **Persistent Session State**: Authentication session keys are written permanently to a mounted volume (`./auth`), keeping the container authenticated even across complete restarts.
* **Dynamic Content Routing**: The Express REST API defaults to a target institutional group JID but gracefully evaluates individual phone numbers dynamically passed inside request body arrays.
* **Timezone Awareness**: Microservices are explicitly synchronized with Indian Standard Time (`Asia/Kolkata`) to prevent timing offsets during cron intervals or adaptive polling loops.

---

## Directory Structure

```text
school-alerts-notifier/
├── .env                         # Centralized environment variable store
├── docker-compose.yml           # Core Docker multi-container orchestrator
├── auth/                        # Local volume directory for WhatsApp session tokens
├── scraper-data/                # Local volume directory for scraper delta tracking
├── whatsapp-bridge/
│   ├── Dockerfile               # Slim Debian-based compilation build stage
│   ├── package.json             # App requirements (Baileys, Express, Pino, etc.)
│   └── server.js                # Core WebSocket client & API gateway code
└── python-scraper/
    ├── Dockerfile               # Lightweight Python runtime container
    ├── requirements.txt         # Parsing and HTTP client dependencies
    └── scraper.py               # Main adaptive polling application script

```

---

## Prerequisites

* **Docker & Docker Compose** installed on your host system (e.g., macOS, Ubuntu Server, or Home Lab environments).
* A valid WhatsApp account on a physical smartphone to scan the initial QR linking token.

---

## Environment Configuration

Configure your environment runtime settings inside a .env file in the root directory. You can structure your multiple student profiles using individual variables.
Code snippet
```
# Network Gateway Infrastructure 
BAILEYS_URL=http://localhost:3001/send
POLL_INTERVAL=3600

# Student Account 1 Configuration Matrix
STUDENT_1_NAME=Vennila
EDUMERGE_USERID_1=your_student1_username
EDUMERGE_PASSWORD_1=your_student1_password

# Student Account 2 Configuration Matrix
STUDENT_2_NAME=Surya
EDUMERGE_USERID_2=your_student2_username
EDUMERGE_PASSWORD_2=your_student2_password
```

## Storage & Tracking Blueprint

The application guarantees zero cookie footprint on disk. The only persistent record stored locally is the atomic state ledger inside data/last_processed_msg.json, which segregates message pointers dynamically by student name:
JSON
```
{
  "Vennila": 43552,
  "Surya": 43549,
  "_updated_at": <TIMESTAMP>
}
```
---

## Getting Started & Initial Authentication

Because WhatsApp authentication requires a one-time terminal QR code scan, deploy the setup for the first time in the foreground:

### 1. Build and Start the Cluster Interactively

Run the following command to compile the native cryptographic and C-shared dependencies inside your local containers:

```bash
docker compose up --build whatsapp-bridge

```

### 2. Scan the Pairing QR Code

1. Watch the terminal logs until the execution scripts dynamically resolve the latest WhatsApp Web Version protocol.
2. A compact scannable text-based **QR Code** will be rendered inline in your terminal window by the `qrcode-terminal` library.
3. Open WhatsApp on your mobile phone, navigate to **Settings > Linked Devices > Link a Device**, and scan the terminal QR code.
4. Once you see `✅ Success! Connected to WhatsApp Core Web Gateway Engine.`, terminate the process in your terminal console by hitting `Ctrl + C`.

### 3. Deploy Everything in Production Background Mode

Now that your secure authentication tokens have been cached permanently in your host's local `./auth` folder space, spin up the entire cluster seamlessly in detached mode:

```bash
docker compose up -d

```

---

## API Documentation (`whatsapp-bridge`)

The Express server exposes an endpoint to trigger immediate manual alerts or programmatically link third-party scrapers.

### 1. Send Alert to Default Institutional Group

Sends a text string straight to the pre-configured `GROUP_ID` specified in your global `.env` configuration file.

* **URL**: `/send`
* **Method**: `POST`
* **Headers**: `Content-Type: application/json`
* **Body Pattern**:
```json
{
  "message": "⚠️ School Notice: The terminal exams scheduled for tomorrow have been postponed."
}

```



**Example `curl` execution command:**

```bash
curl -X POST http://localhost:3001/send \
  -H "Content-Type: application/json" \
  -d '{"message": "⚠️ School Notice: The terminal exams scheduled for tomorrow have been postponed."}'

```

### 2. Send Alert to a Specific Phone Number

Overrides the fallback group parameter and routes messages directly to an individual private chat space. Always include the country code prefix (e.g., `91` for India) without dashes, plus signs, or whitespace.

* **URL**: `/send`
* **Method**: `POST`
* **Headers**: `Content-Type: application/json`
* **Body Pattern**:
```json
{
  "to": "919xxxxxxxxx",
  "message": "Hello! This is a direct test message sent to an individual number."
}

```



**Example `curl` execution command:**

```bash
curl -X POST http://localhost:3001/send \
  -H "Content-Type: application/json" \
  -d '{"to": "919xxxxxxxxx", "message": "Hello! This is a direct test message."}'

```
Example `curl` to send attachment 

```bash
curl -X POST http://localhost:3001/media \
  -F "file=@'/tmp/Document 4.pdf';type=application/pdf"
```


---

## Useful Operations Commands

### Check Live Logging Streams

To trace adaptive polling behaviors or incoming transmission responses, monitor the container logs:

```bash
# View all container streams simultaneously
docker compose logs -f

# Filter specific logs solely for the WhatsApp infrastructure
docker compose logs -f whatsapp-bridge

# Filter specific logs solely for the Python scraper tasks
docker compose logs -f python-scraper

```

### Clean System Reset

To force your bot to completely log out and generate a fresh onboarding QR code sequence, clear the mounted state tracking contents:

```bash
docker compose down
rm -rf ./auth/*
docker compose up whatsapp-bridge

```
