// Live Estimates -> Invoice demo on estimates-to-invoice.html.
// Two-step: generate a quotation, then convert it to an invoice. Both
// calls are scoped to a random session id kept in sessionStorage, never a
// real phone number. Underlying demo data expires after a day.

const ESTIMATE_DEMO_API = "https://api.billbolo.in/api/web/estimate";
const ESTIMATE_CONVERT_API = "https://api.billbolo.in/api/web/estimate/convert";
const ESTIMATE_SESSION_KEY = "billbolo_estimate_demo_session";

document.addEventListener("DOMContentLoaded", () => {
    const input = document.getElementById("estDemoInput");
    const generateBtn = document.getElementById("estDemoGenerate");
    const errorBox = document.getElementById("estDemoError");
    const quoteResult = document.getElementById("estDemoQuoteResult");
    const quoteClient = document.getElementById("estDemoQuoteClient");
    const quoteItems = document.getElementById("estDemoQuoteItems");
    const quoteTotal = document.getElementById("estDemoQuoteTotal");
    const step2 = document.getElementById("estStep2");
    const dueDateInput = document.getElementById("estDemoDueDate");
    const paymentModeInput = document.getElementById("estDemoPaymentMode");
    const convertBtn = document.getElementById("estDemoConvert");
    const invoiceResult = document.getElementById("estDemoInvoiceResult");
    const invoiceClient = document.getElementById("estDemoInvoiceClient");
    const invoiceItems = document.getElementById("estDemoInvoiceItems");
    const invoiceTotal = document.getElementById("estDemoInvoiceTotal");
    const pdfLink = document.getElementById("estDemoPdfLink");

    if (!generateBtn) return;

    function getSessionId() {
        try {
            let id = sessionStorage.getItem(ESTIMATE_SESSION_KEY);
            if (!id) {
                id = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()) + Math.random()).replace(/-/g, "");
                sessionStorage.setItem(ESTIMATE_SESSION_KEY, id);
            }
            return id;
        } catch {
            return "fallback" + Date.now();
        }
    }

    function formatInr(amount) {
        const n = Number(amount) || 0;
        return n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    }

    function showError(message) {
        errorBox.textContent = message;
        errorBox.style.display = "block";
    }

    function renderItems(container, items) {
        container.innerHTML = "";
        (items || []).forEach((item) => {
            if (!item.item_name) return;
            const row = document.createElement("div");
            row.className = "demo-result-item";
            row.innerHTML = `<span>${item.item_name}</span><span>₹${formatInr(item.amount)}</span>`;
            container.appendChild(row);
        });
    }

    function base64ToBlobUrl(base64, mimeType) {
        const bytes = atob(base64);
        const arr = new Uint8Array(bytes.length);
        for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
        return URL.createObjectURL(new Blob([arr], { type: mimeType }));
    }

    async function handleGenerate() {
        const text = (input.value || "").trim();
        if (!text) {
            showError("Please describe the job you'd like to quote.");
            return;
        }

        generateBtn.disabled = true;
        generateBtn.textContent = "Generating…";
        errorBox.style.display = "none";
        quoteResult.style.display = "none";
        step2.style.display = "none";
        invoiceResult.style.display = "none";

        try {
            const res = await fetch(ESTIMATE_DEMO_API, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text, session_id: getSessionId() }),
            });
            const data = await res.json();

            if (!res.ok) {
                showError(data.error || "Something went wrong. Please try again.");
            } else {
                quoteClient.textContent = data.client_name || "Quotation";
                renderItems(quoteItems, data.items);
                quoteTotal.textContent = `₹${formatInr(data.grand_total)}`;
                quoteResult.style.display = "block";
                step2.style.display = "block";
            }
        } catch {
            showError("Couldn't reach the server. Please try again.");
        } finally {
            generateBtn.disabled = false;
            generateBtn.textContent = "Generate Quotation";
        }
    }

    async function handleConvert() {
        convertBtn.disabled = true;
        convertBtn.textContent = "Converting…";
        errorBox.style.display = "none";
        invoiceResult.style.display = "none";

        try {
            const res = await fetch(ESTIMATE_CONVERT_API, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    session_id: getSessionId(),
                    due_date: (dueDateInput.value || "").trim() || null,
                    payment_mode: (paymentModeInput.value || "").trim() || null,
                }),
            });
            const data = await res.json();

            if (!res.ok) {
                showError(data.error || "Something went wrong. Please try again.");
            } else {
                invoiceClient.textContent = data.client_name || "Invoice";
                renderItems(invoiceItems, data.items);
                invoiceTotal.textContent = `₹${formatInr(data.grand_total)}`;
                if (data.pdf_base64) {
                    pdfLink.href = base64ToBlobUrl(data.pdf_base64, "application/pdf");
                    pdfLink.style.display = "inline-block";
                }
                invoiceResult.style.display = "block";
            }
        } catch {
            showError("Couldn't reach the server. Please try again.");
        } finally {
            convertBtn.disabled = false;
            convertBtn.textContent = "Convert to Invoice";
        }
    }

    generateBtn.addEventListener("click", handleGenerate);
    convertBtn.addEventListener("click", handleConvert);
});
