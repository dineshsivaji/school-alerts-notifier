require("dotenv").config();

const { default: makeWASocket, useMultiFileAuthState, DisconnectReason } = require("@whiskeysockets/baileys");
const P = require("pino");
const qrcode = require("qrcode-terminal");
const express = require("express");

const PORT = process.env.PORT || 3001;
const GROUP_ID = process.env.GROUP_ID;

if (!GROUP_ID) {
    console.error("❌ GROUP_ID missing in .env");
    process.exit(1);
}

async function startBot() {
    const { state, saveCreds } = await useMultiFileAuthState("auth");

    const sock = makeWASocket({
        auth: state,
        logger: P({ level: "silent" }),
    });

    // =========================
    // CONNECTION HANDLING
    // =========================
    sock.ev.on("connection.update", (update) => {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
            console.log("📱 Scan this QR:");
            qrcode.generate(qr, { small: true });
        }

        if (connection === "open") {
            console.log("✅ WhatsApp Connected!");
        }

        if (connection === "close") {
            const shouldReconnect =
                (lastDisconnect?.error)?.output?.statusCode !== DisconnectReason.loggedOut;

            console.log("❌ Connection closed. Reconnecting:", shouldReconnect);

            if (shouldReconnect) {
                startBot();
            }
        }
    });

    sock.ev.on("creds.update", saveCreds);

    // =========================
    // RECEIVE MESSAGES
    // =========================
    // sock.ev.on("messages.upsert", async (m) => {
    //     try {
    //         const msg = m.messages[0];
    //         if (!msg.message) return;

    //         const from = msg.key.remoteJid;

    //         const text =
    //             msg.message.conversation ||
    //             msg.message.extendedTextMessage?.text ||
    //             "";

    //         console.log("\n📩 Incoming Message");
    //         console.log("FROM:", from);
    //         console.log("Message:", text);

    //         if (from.endsWith("@g.us")) {
    //             const metadata = await sock.groupMetadata(from);
    //             console.log("Group:", metadata.subject);
    //             console.log("Group ID:", from);
    //         }

    //     } catch (err) {
    //         console.error("Error processing message:", err);
    //     }
    // });

    // =========================
    // EXPRESS API
    // =========================
    const app = express();
    app.use(express.json());

    app.get("/", (req, res) => {
        res.send("WhatsApp Bot Running ✅");
    });

    app.post("/send", async (req, res) => {
        const { message, to } = req.body;

        const target = to || GROUP_ID;

        if (!message) {
            return res.status(400).json({ error: "message is required" });
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

    app.listen(PORT, () => {
        console.log(`🚀 API running on http://localhost:${PORT}`);
    });
}

startBot();
