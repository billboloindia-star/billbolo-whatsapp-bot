// Live client-ledger demo on client-ledger.html.
// Calls the public, rate-limited /api/web/ledger endpoint - scoped to a
// random session id kept in sessionStorage, never a real phone number.
// Underlying demo data expires after a day; nothing is tied to any account.

const LEDGER_DEMO_API = "https://api.billbolo.in/api/web/ledger";
const SESSION_KEY = "billbolo_ledger_demo_session";

document.addEventListener("DOMContentLoaded", () => {
    const chat = document.getElementById("ledgerChat");
    const input = document.getElementById("ledgerDemoInput");
    const sendBtn = document.getElementById("ledgerDemoSend");
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

    sendBtn.addEventListener("click", handleSend);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") handleSend();
    });
});
