// Live client-ledger demo on client-ledger.html.
// Calls the public, rate-limited /api/web/ledger endpoint - scoped to a
// random session id kept in sessionStorage, never a real phone number.
// Underlying demo data expires after a day; nothing is tied to any account.

const LEDGER_DEMO_API = "https://api.billbolo.in/api/web/ledger";
const LEDGER_DEMO_AUDIO_API = "https://api.billbolo.in/api/web/ledger/audio";
const SESSION_KEY = "billbolo_ledger_demo_session";

document.addEventListener("DOMContentLoaded", () => {
    const chat = document.getElementById("ledgerChat");
    const input = document.getElementById("ledgerDemoInput");
    const sendBtn = document.getElementById("ledgerDemoSend");
    const micBtn = document.getElementById("ledgerDemoMic");
    const errorBox = document.getElementById("ledgerDemoError");

    if (!sendBtn) return;

    function getSessionId() {
        try {
            let id = sessionStorage.getItem(SESSION_KEY);
            if (!id) {
                id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/-/g, "");
                sessionStorage.setItem(SESSION_KEY, id);
            }
            return id;
        } catch {
            return "fallback" + Date.now();
        }
    }

    function addBubble(text, kind) {
        const bubble = document.createElement("div");
        bubble.className = `ledger-chat-bubble ledger-chat-${kind}`;
        bubble.textContent = text;
        chat.appendChild(bubble);
        chat.scrollTop = chat.scrollHeight;
    }

    async function handleSend() {
        const text = (input.value || "").trim();
        if (!text) return;

        addBubble(text, "out");
        input.value = "";
        sendBtn.disabled = true;
        errorBox.style.display = "none";

        try {
            const res = await fetch(LEDGER_DEMO_API, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text, session_id: getSessionId() }),
            });
            const data = await res.json();

            if (!res.ok) {
                errorBox.textContent = data.error || "Something went wrong. Please try again.";
                errorBox.style.display = "block";
            } else {
                addBubble(data.reply, "in");
            }
        } catch {
            errorBox.textContent = "Couldn't reach the server. Please try again.";
            errorBox.style.display = "block";
        } finally {
            sendBtn.disabled = false;
        }
    }

    async function handleAudioSend(blob) {
        addBubble("🎤 (voice message)", "out");
        sendBtn.disabled = true;
        micBtn.disabled = true;
        errorBox.style.display = "none";

        try {
            const formData = new FormData();
            formData.append("audio", blob, "recording.webm");
            formData.append("session_id", getSessionId());

            const res = await fetch(LEDGER_DEMO_AUDIO_API, { method: "POST", body: formData });
            const data = await res.json();

            if (!res.ok) {
                errorBox.textContent = data.error || "Something went wrong. Please try again.";
                errorBox.style.display = "block";
            } else {
                addBubble(data.reply, "in");
            }
        } catch {
            errorBox.textContent = "Couldn't reach the server. Please try again.";
            errorBox.style.display = "block";
        } finally {
            sendBtn.disabled = false;
            micBtn.disabled = false;
        }
    }

    if (micBtn) {
        let mediaRecorder = null;
        let chunks = [];

        micBtn.addEventListener("click", async () => {
            if (mediaRecorder && mediaRecorder.state === "recording") {
                mediaRecorder.stop();
                return;
            }
            let stream;
            try {
                stream = await navigator.mediaDevices.getUserMedia({ audio: true });
            } catch {
                errorBox.textContent = "Microphone access was denied or isn't available in this browser.";
                errorBox.style.display = "block";
                return;
            }
            const mimeType = ["audio/webm", "audio/ogg", "audio/mp4"].find(
                (t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t)
            );
            mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
            chunks = [];
            mediaRecorder.ondataavailable = (e) => {
                if (e.data.size > 0) chunks.push(e.data);
            };
            mediaRecorder.onstop = () => {
                stream.getTracks().forEach((t) => t.stop());
                micBtn.classList.remove("recording");
                micBtn.textContent = "🎤";
                const blob = new Blob(chunks, { type: mediaRecorder.mimeType || "audio/webm" });
                handleAudioSend(blob);
            };
            mediaRecorder.start();
            micBtn.classList.add("recording");
            micBtn.textContent = "⏹";
        });
    }

    sendBtn.addEventListener("click", handleSend);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleSend();
    });
});
