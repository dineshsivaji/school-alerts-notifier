const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require("@whiskeysockets/baileys");
const P = require("pino");
const qrcode = require("qrcode-terminal");
const express = require("express");

const PORT = process.env.PORT || 3001;
const GROUP_ID = process.env.GROUP_ID;

// Validate ENV
if (!GROUP_ID) {
    console.error("❌ GROUP_ID missing in environment");
    process.exit(1);
}

// =========================
// GLOBAL SOCKET
// =========================
let sock;

// =========================
// EXPRESS SERVER (START ONCE)
// =========================
const app = express();
app.use(express.json());

app.get("/", (req, res) => {
    res.send("WhatsApp Bot Running ✅");
});

app.post("/send", async (req, res) => {
    const { message, to } = req.body;

    if (!message) {
        return res.status(400).json({ error: "message is required" });
    }

    if (!sock) {
        return res.status(500).json({ error: "WhatsApp not connected" });
    }

    // Determine target JID routing architecture
    let target;
    if (to) {
        // Normalize: remove spaces, dashes, or plus signs if any exist
        let cleanTo = to.toString().replace(/[\s\-+]/g, "");

        // If it's a phone number without any suffix, append the individual chat domain
        if (!cleanTo.endsWith("@s.whatsapp.net") && !cleanTo.endsWith("@g.us")) {
            target = `${cleanTo}@s.whatsapp.net`;
        } else {
            target = cleanTo;
        }
    } else {
        // Default fallback option straight to group configuration
        target = GROUP_ID;
    }

    try {
        await sock.sendMessage(target, {
            text: message,
        });

        console.log("📤 Sent:", message);
        console.log("To:", target);

        res.json({ status: "sent", to: target });
    } catch (err) {
        console.error("Send error:", err);
        res.status(500).json({ error: "failed to send" });
    }
});

// Start server ONLY once
app.listen(PORT, () => {
    console.log(`🚀 API running on http://localhost:${PORT}`);
});

// =========================
// WHATSAPP BOT
// =========================
let isStarting = false;

async function startBot() {
    if (isStarting) return;
    isStarting = true;

    // Persist session tokens securely in the configured auth folder space
    const { state, saveCreds } = await useMultiFileAuthState("auth_info_baileys");

    // Fetch or define a valid version array to pass the noise gate
    let version = [2, 3000, 1017578434];
    try {
        const latest = await fetchLatestBaileysVersion();
        if (latest && latest.version) {
            version = latest.version;
            console.log(`🌐 Dynamically resolved latest WhatsApp Web Version: ${version.join('.')}`);
        }
    } catch (err) {
        console.log(`⚠️ Could not fetch remote version, falling back to static override: ${version.join('.')}`);
    }

    sock = makeWASocket({
        version: version,
        auth: state,
        logger: P({ level: "error" }), // Suppresses verbose packet telemetry logs
        mobile: false,
        browser: ["Ubuntu", "Chrome", "20.0.04"],
        // ADD THESE TWO CONFIGURATION LINES BELOW:
        defaultQueryTimeoutMs: 0,   // Prevent 408 Time-out issues during high data synchronization
        syncFullHistory: false,     // Skip heavy historical data downloads on startup
    });

    sock.ev.on("connection.update", (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            console.log("\n📱 Scan the QR Code below to link your server:");
            qrcode.generate(qr, { small: true });
        }

        if (connection === "open") {
            console.log("✅ Success! Connected to WhatsApp Core Web Gateway Engine.");
            isStarting = false;
        }

        if (connection === "close") {
            isStarting = false;

            const shouldReconnect =
                lastDisconnect?.error?.output?.statusCode !== DisconnectReason.loggedOut;

            console.log("❌ Connection Closed. Reconnect Target Status:", shouldReconnect);

            if (shouldReconnect) {
                setTimeout(startBot, 3000); // Safe delay step execution layout
            }
        }
    });

    // Temporary listener to discover your real Group ID
    //sock.ev.on("messages.upsert", async (chatUpdate) => {
    //    try {
    //        const msg = chatUpdate.messages[0];
    //        if (!msg.message || msg.key.fromMe) return;
    //
    //        const chatId = msg.key.remoteJid;
    //
    //        // If the message comes from a group, it will end with @g.us
    //        if (chatId.endsWith("@g.us")) {
    //            console.log("\n==============================================");
    //            console.log("👥 FOUND GROUP ID!");
    //            console.log(`Group ID String: ${chatId}`);
    //            console.log(`Text sent: ${msg.message.conversation || msg.message.extendedTextMessage?.text}`);
    //            console.log("==============================================\n");
    //        }
    //    } catch (err) {
    //        console.error("Discovery log error:", err);
    //    }
    //});

    sock.ev.on("creds.update", saveCreds);
}

// Start bot
startBot();