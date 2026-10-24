// Live quotation-generation demo on quotation-format.html.
// Calls the public, rate-limited /api/web/quotation endpoint - nothing is
// saved or tied to any account, purely for showing how the real feature works.

const QUOTATION_DEMO_API = "https://api.billbolo.in/api/web/quotation";

document.addEventListener("DOMContentLoaded", () => {
    const input = document.getElementById("quoteDemoInput");
    const submitBtn = document.getElementById("quoteDemoSubmit");
    const errorBox = document.getElementById("quoteDemoError");
    const resultBox = document.getElementById("quoteDemoResult");
    const resultClient = document.getElementById("quoteDemoClient");
    const resultItems = document.getElementById("quoteDemoItems");
    const resultTotal = document.getElementById("quoteDemoTotal");
    const pdfLink = document.getElementById("quoteDemoPdfLink");

    if (!submitBtn) return;

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
            submitBtn.textContent = "Generate Quotation";
        }
    }

    submitBtn.addEventListener("click", handleSubmit);
    input.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit();
    });
});
