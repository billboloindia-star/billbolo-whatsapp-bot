// Live quotation-generation demo on quotation-format.html.
// Calls the public, rate-limited /api/web/quotation endpoint - nothing is
// saved or tied to any account, purely for showing how the real feature works.

const QUOTATION_DEMO_API = "https://api.billbolo.in/api/web/quotation";
const QUOTATION_DEMO_AUDIO_API = "https://api.billbolo.in/api/web/quotation/audio";

document.addEventListener("DOMContentLoaded", () => {
    const input = document.getElementById("quoteDemoInput");
    const submitBtn = document.getElementById("quoteDemoSubmit");
    const micBtn = document.getElementById("quoteDemoMic");
    const errorBox = document.getElementById("quoteDemoError");
    const resultBox = document.getElementById("quoteDemoResult");
    const resultClient = document.getElementById("quoteDemoClient");
    const resultItems = document.getElementById("quoteDemoItems");
    const resultTotal = document.getElementById("quoteDemoTotal");
    const pdfLink = document.getElementById("quoteDemoPdfLink");

    if (!submitBtn) return;

    const defaultBtnLabel = submitBtn.textContent;

    function formatInr(amount) {
        const n = Number(amount) || 0;
        return n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    }

    function showError(message) {
        errorBox.textContent = message;
        errorBox.style.display = "block";
        resultBox.style.display = "none";
    }

    function base64ToBlobUrl(base64, mimeType) {
        const bytes = atob(base64);
        const arr = new Uint8Array(bytes.length);
        for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
        return URL.createObjectURL(new Blob([arr], { type: mimeType }));
    }

    function showResult(data) {
        errorBox.style.display = "none";
        resultClient.textContent = data.client_name || "Quotation";

        resultItems.innerHTML = "";
        (data.items || []).forEach((item) => {
            if (!item.item_name) return;
            const row = document.createElement("div");
            row.className = "demo-result-item";
            row.innerHTML = `<span>${item.item_name}</span><span>₹${formatInr(item.amount)}</span>`;
            resultItems.appendChild(row);
        });

        resultTotal.textContent = `₹${formatInr(data.grand_total)}`;

        if (data.pdf_base64) {
            pdfLink.href = base64ToBlobUrl(data.pdf_base64, "application/pdf");
            pdfLink.style.display = "inline-block";
        } else {
            pdfLink.style.display = "none";
        }

        resultBox.style.display = "block";
    }

    async function handleSubmit() {
        const text = (input.value || "").trim();
        if (!text) {
            showError("Please describe the job you'd like to quote.");
            return;
        }

        submitBtn.disabled = true;
        submitBtn.textContent = "Generating…";
        errorBox.style.display = "none";
        resultBox.style.display = "none";

        try {
            const res = await fetch(QUOTATION_DEMO_API, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text }),
            });
            const data = await res.json();

            if (!res.ok) {
                showError(data.error || "Something went wrong. Please try again.");
            } else {
                showResult(data);
            }
        } catch {
            showError("Couldn't reach the server. Please try again.");
        } finally {
            submitBtn.disabled = false;
            submitBtn.textContent = defaultBtnLabel;
        }
    }

    async function handleAudioSubmit(blob) {
        submitBtn.disabled = true;
        micBtn.disabled = true;
        submitBtn.textContent = "Generating…";
        errorBox.style.display = "none";
        resultBox.style.display = "none";

        try {
            const formData = new FormData();
            formData.append("audio", blob, "recording.webm");

            const res = await fetch(QUOTATION_DEMO_AUDIO_API, { method: "POST", body: formData });
            const data = await res.json();

            if (!res.ok) {
                showError(data.error || "Something went wrong. Please try again.");
            } else {
                showResult(data);
            }
        } catch {
            showError("Couldn't reach the server. Please try again.");
        } finally {
            submitBtn.disabled = false;
            micBtn.disabled = false;
            submitBtn.textContent = defaultBtnLabel;
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
                showError("Microphone access was denied or isn't available in this browser.");
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
                handleAudioSubmit(blob);
            };
            mediaRecorder.start();
            micBtn.classList.add("recording");
            micBtn.textContent = "⏹";
        });
    }

    submitBtn.addEventListener("click", handleSubmit);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
    });
});
