// Live expense-extraction demo on expense-tracking.html.
// Calls the public, rate-limited /api/web/expense endpoint - nothing is
// saved or tied to any account, purely for showing how the real feature works.

const EXPENSE_DEMO_API = "https://api.billbolo.in/api/web/expense";

document.addEventListener("DOMContentLoaded", () => {
    const input = document.getElementById("demoFileInput");
    const dropLabel = document.getElementById("demoDropLabel");
    const dropText = document.getElementById("demoDropText");
    const errorBox = document.getElementById("demoError");
    const resultBox = document.getElementById("demoResult");
    const resultVendor = document.getElementById("demoResultVendor");
    const resultCategory = document.getElementById("demoResultCategory");
    const resultItems = document.getElementById("demoResultItems");
    const resultTotal = document.getElementById("demoResultTotal");

    if (!input) return;

    function formatInr(amount) {
        const n = Number(amount) || 0;
        return n.toLocaleString("en-IN", { maximumFractionDigits: 2 });
    }

    function showError(message) {
        errorBox.textContent = message;
        errorBox.style.display = "block";
        resultBox.style.display = "none";
    }

    function showResult(data) {
        errorBox.style.display = "none";
        resultVendor.textContent = data.vendor || "Expense";
        resultCategory.textContent = data.category || "Other";

        resultItems.innerHTML = "";
        (data.items || []).forEach((item) => {
            if (!item.item_name) return;
            const row = document.createElement("div");
            row.className = "demo-result-item";
            row.innerHTML = `<span>${item.item_name}</span><span>₹${formatInr(item.amount)}</span>`;
            resultItems.appendChild(row);
        });

        resultTotal.textContent = `₹${formatInr(data.grand_total)}`;
        resultBox.style.display = "block";
    }

    input.addEventListener("change", async () => {
        const file = input.files && input.files[0];
        if (!file) return;

        dropText.textContent = "Reading bill…";
        dropLabel.classList.add("demo-drop-loading");
        errorBox.style.display = "none";
        resultBox.style.display = "none";

        try {
            const formData = new FormData();
            formData.append("image", file);

            const res = await fetch(EXPENSE_DEMO_API, { method: "POST", body: formData });
            const data = await res.json();

            if (!res.ok) {
                showError(data.error || "Something went wrong. Please try again.");
            } else {
                showResult(data);
            }
        } catch {
            showError("Couldn't reach the server. Please try again.");
        } finally {
            dropText.textContent = "Tap to upload a bill or receipt (photo or PDF)";
            dropLabel.classList.remove("demo-drop-loading");
            input.value = "";
        }
    });
});
