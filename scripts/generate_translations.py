"""Generate locale JSON files for the website using Sarvam's translation API.

Source of truth is the EN dict below (must match the data-i18n keys used
in index.html exactly). Run whenever English copy changes:

    .venv312/bin/python scripts/generate_translations.py
"""

import json
import time
from pathlib import Path

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
LOCALES_DIR = ROOT / "locales"

TARGET_LANGUAGES = {
    "hi": "hi-IN",
    "bn": "bn-IN",
    "ta": "ta-IN",
    "te": "te-IN",
    "mr": "mr-IN",
}

# Flat key -> English text. Keys match data-i18n attributes in index.html.
EN = {
    "nav.features": "Features",
    "nav.pricing": "Pricing",
    "nav.privacy": "Privacy",

    "hero.eyebrow": "Built for Indian Contractors, Freelancers & Shop Owners",
    "hero.title1": "Quotations in 5 Seconds.",
    "hero.title2": "Inside WhatsApp.",
    "hero.subtitle": "Send a voice note or snap a bill photo. BillBolo's AI drafts a branded PDF quotation and logs the expense to your Google Sheet — before you've even put your phone down.",
    "hero.ctaPrimary": "Start on WhatsApp →",
    "hero.ctaSecondary": "See pricing",
    "hero.tryWebPrefix": "Prefer a screen first?",
    "hero.tryWebLink": "Try it on the web →",

    "phone.readyLine1": "Quotation ready ✅",
    "phone.readyLine2": "Sharma Ji — Bathroom Tiling",
    "phone.readyLine3": "3 items",

    "trust.speed": "5-second turnaround",
    "trust.noApp": "No app to install",
    "trust.security": "Bank-grade encryption",
    "trust.sheets": "Synced to your own Google Sheet",

    "problem.eyebrow": "About Us",
    "problem.title": "Running a business shouldn't feel like a second job",
    "problem.subtitle": "Three everyday headaches — solved without leaving WhatsApp.",
    "problem.card1.title": "Quotes that take hours, not seconds",
    "problem.card1.body": "By the time you've typed up a quote, your customer has already called the next contractor on their list.",
    "problem.card2.title": "Expenses that vanish into WhatsApp chats",
    "problem.card2.body": "Paper bills get lost. Photos get buried. Come tax season, nobody remembers what was spent where.",
    "problem.card3.title": "Accounting software nobody has time to learn",
    "problem.card3.body": "Tally and Zoho are built for accountants, not for someone running a site or a shop. You need five minutes, not a training course.",

    "features.eyebrow": "Services",
    "features.title": "Everything your business needs. Nothing to install.",
    "features.subtitle": "One WhatsApp number. Six jobs handled automatically.",
    "features.card1.title": "Voice-to-Quotation",
    "features.card1.body": "Record a 10-second voice note describing the job. Get a polished, branded PDF quotation back in under 5 seconds.",
    "features.card2.title": "Snap-to-Expense",
    "features.card2.body": "Photograph any bill or receipt. Our AI reads the vendor, items, amount, and date — no typing, no filing.",
    "features.card3.title": "Auto-Synced Books",
    "features.card3.body": "Every quotation and expense lands in your own Google Sheet in real time. Your accountant (or you, come March) will thank you.",
    "features.card4.title": "Branded, Professional PDFs",
    "features.card4.body": "Every document carries your business name and details — looks like it came from a proper office, not a notebook page.",
    "features.card5.title": "5-Second Turnaround",
    "features.card5.body": "Powered by advanced AI. While the competition is still typing a reply, your quote is already in the customer's inbox.",
    "features.card6.title": "Bank-Grade Security, Zero New Apps",
    "features.card6.body": "Encrypted end-to-end. No downloads, no logins, no training — just the WhatsApp you already check 50 times a day.",
    "features.card7.title": "Try It on the Web, No Signup",
    "features.card7.bodyPrefix": "Not on WhatsApp yet? Generate your first quotation right in the browser at",
    "features.card7.bodySuffix": "— then switch to the bot anytime.",

    "how.title": "From voice note to paid invoice — in one chat",
    "how.subtitle": "No forms. No dashboards. No new habits.",
    "how.step1.title": "Send it",
    "how.step1.body": "Voice note or photo, straight to BillBolo on WhatsApp",
    "how.step2.title": "AI reads it",
    "how.step2.body": "Our AI extracts every item, quantity and price instantly",
    "how.step3.title": "It's done",
    "how.step3.body": "PDF lands in the chat. Expense is already logged.",

    "pricing.title": "Pricing that pays for itself with one saved deal",
    "pricing.subtitle": "Start free. Upgrade only when you outgrow what's included.",
    "pricing.perMonth": "/month",
    "pricing.mostPopular": "Most Popular",
    "pricing.comingSoon": "Coming Soon",

    "pricing.free.name": "Free",
    "pricing.free.feature1": "5 quotations/month",
    "pricing.free.feature2": "Basic expense tracking",
    "pricing.free.feature3": "Google Sheets sync",
    "pricing.free.feature4": "English only",
    "pricing.free.button": "Get Started",

    "pricing.starter.name": "Starter",
    "pricing.starter.discount": "or ₹2,499/year · save 16%",
    "pricing.starter.feature1": "Unlimited quotations",
    "pricing.starter.feature2": "Hindi + English (voice & photo)",
    "pricing.starter.feature3": "Client database",
    "pricing.starter.feature4": "Monthly expense report (PDF)",
    "pricing.starter.button": "Choose Starter",

    "pricing.growth.name": "Growth",
    "pricing.growth.discount": "or ₹4,999/year · save 17%",
    "pricing.growth.feature1": "All 22 Indian languages",
    "pricing.growth.feature2": "GST-compliant invoicing",
    "pricing.growth.feature3": "UPI payment link on invoices",
    "pricing.growth.feature4": "Payment reminders",
    "pricing.growth.feature5": "Udhaar/credit ledger",
    "pricing.growth.feature6": "Rate card memory",
    "pricing.growth.feature7": "Weekly business summary",
    "pricing.growth.button": "Choose Growth",

    "pricing.business.name": "Business",
    "pricing.business.discount": "or ₹9,999/year · save 17%",
    "pricing.business.feature1": "Everything in Growth",
    "pricing.business.feature2": "TDS tracking",
    "pricing.business.feature3": "GST filing reminders",
    "pricing.business.feature4": "MSME/Udyam number on invoices",
    "pricing.business.feature5": "Multiple staff & business profiles",
    "pricing.business.feature6": "Dedicated priority support",
    "pricing.business.button": "Choose Business",

    "cta.title": "Stop losing deals to slow quotes.",
    "cta.body": "Message BillBolo on WhatsApp right now and send your first voice note — watch the PDF land in under 5 seconds.",
    "cta.button": "Message BillBolo →",

    "footer.tagline": "Virtual Munshi inside WhatsApp",
    "footer.aboutUs": "About Us",
    "footer.services": "Services",
    "footer.privacyPolicy": "Privacy Policy",
    "footer.termsConditions": "Terms & Conditions",
    "footer.refundPolicy": "Refund Policy",
    "footer.cancellationPolicy": "Cancellation Policy",
    "footer.contact": "Contact:",
    "footer.whatsappLabel": "WhatsApp:",
    "footer.legalLabel": "Business Information",
    "footer.operatedBy": "Operated by",
    "footer.registeredAddress": "Registered Address",
    "footer.email": "Email",
    "footer.phone": "Phone",
    "footer.udyam": "Udyam Registration",

    "lang.switcherLabel": "Language",
}


def translate(text: str, target_code: str, api_key: str) -> str:
    """Call Sarvam's translate API for a single string."""
    resp = requests.post(
        "https://api.sarvam.ai/translate",
        json={
            "input": text,
            "source_language_code": "en-IN",
            "target_language_code": target_code,
        },
        headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["translated_text"]


def main() -> None:
    env = dotenv_values(ROOT / ".env")
    api_key = env["SARVAM_API_KEY"]

    LOCALES_DIR.mkdir(exist_ok=True)

    with open(LOCALES_DIR / "en.json", "w", encoding="utf-8") as f:
        json.dump(EN, f, ensure_ascii=False, indent=2)
    print(f"Wrote en.json ({len(EN)} keys)")

    for lang_code, sarvam_code in TARGET_LANGUAGES.items():
        translated = {}
        for i, (key, text) in enumerate(EN.items(), 1):
            try:
                translated[key] = translate(text, sarvam_code, api_key)
            except Exception as e:
                print(f"  FAILED on {lang_code}.{key}: {e}")
                translated[key] = text
            if i % 20 == 0:
                print(f"  {lang_code}: {i}/{len(EN)}")
            time.sleep(0.05)

        out_path = LOCALES_DIR / f"{lang_code}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(translated, f, ensure_ascii=False, indent=2)
        print(f"Wrote {lang_code}.json ({len(translated)} keys)")


if __name__ == "__main__":
    main()
