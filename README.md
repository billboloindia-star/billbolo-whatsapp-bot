# BillBolo: WhatsApp-Powered Virtual Munshi for Indian SMBs

**BillBolo** is an AI-powered WhatsApp bot that helps small business owners, contractors, and freelancers generate professional quotations, invoices, and track expenses — all within WhatsApp. No installation, no learning curve. Just send a voice note or bill photo.

## 🎯 Problem It Solves

1. **Slow Quotations**: Contractors lose deals because creating a PDF quote on-site is too hard.
   - **BillBolo Solution**: Speak a voice note → Get a branded PDF in 4 seconds.

2. **Lost Expenses**: Paper bills get lost; owners forget to log cash transactions.
   - **BillBolo Solution**: Snap a bill photo → Auto-logs to Google Sheets.

3. **No Dashboard Friction**: Non-technical users won't learn Tally or Zoho.
   - **BillBolo Solution**: Everything happens inside WhatsApp.

## 🏗️ Architecture Overview

### Technology Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **Runtime** | Python 3.12 + FastAPI | High-performance async API |
| **Compute** | AWS Lambda | Serverless, auto-scaling, ~100ms cold-start |
| **Database** | DynamoDB (On-Demand) | Pay-per-request, no provisioning |
| **Storage** | Amazon S3 | 7-day auto-delete for generated PDFs |
| **AI Engine** | Google Gemini Flash | Native multimodal audio/image parsing |
| **PDF Generation** | ReportLab | Lightweight, fast PDF rendering in Lambda |
| **Messaging** | Meta WhatsApp Cloud API | Official, free tier available |
| **Payments** | Cashfree Payment Links | UPI/card upgrades, webhook integration |
| **Sheets** | Google Sheets API | Auto-log transactions (stub ready for integration) |

### System Design (SOLID Principles)

```
┌─────────────────────────────────────────────────────────┐
│                    WhatsApp Cloud API                    │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│          FastAPI Application (src/main.py)              │
│  ┌────────────────────────────────────────────────────┐ │
│  │           WebhookHandler (Orchestrator)            │ │
│  │  Depends on: WhatsAppClient, GeminiExtractor,     │ │
│  │  PDFGenerator, DynamoDBClient, GoogleSheetsClient │ │
│  └────────────────────────────────────────────────────┘ │
└────────┬──────────────────┬──────────────────┬──────────┘
         │                  │                  │
    ┌────▼────┐      ┌─────▼────┐      ┌─────▼──────┐
    │ Services │      │ Handlers │      │ Config     │
    │ Layer    │      │ Layer    │      │ Module     │
    └──────────┘      └──────────┘      └────────────┘
         │
   ┌─────┴──────────────────────────┐
   │                                │
┌──▼──────┐ ┌──────────┐ ┌─────────▼──┐
│ DynamoDB │ │ S3       │ │ Google Sheets
│ (State)  │ │ (PDFs)   │ │ (Logging)
└──────────┘ └──────────┘ └────────────┘

External APIs:
  ├─ Gemini Flash (Multimodal AI)
  ├─ Cashfree Payment (UPI/Cards)
  └─ Google Sheets API
```

### SOLID Principles Implementation

1. **Single Responsibility Principle**:
   - `WhatsAppClient`: Only handles WhatsApp API communication
   - `GeminiExtractor`: Only parses audio/images → JSON
   - `PDFGenerator`: Only generates PDFs
   - `DynamoDBClient`: Only manages user state
   - `GoogleSheetsClient`: Only handles sheet operations
   - `CashfreeClient`: Only manages payments
   - `WebhookHandler`: Orchestrates the flow (depends on abstractions)

2. **Open/Closed Principle**:
   - Services are open for extension (new backends) but closed for modification
   - Easy to swap DynamoDB for another database without changing handlers

3. **Liskov Substitution Principle**:
   - All service clients follow consistent interfaces
   - Can be mocked for testing

4. **Interface Segregation Principle**:
   - Each service exposes only relevant methods
   - WebhookHandler doesn't depend on unused methods

5. **Dependency Inversion Principle**:
   - `WebhookHandler` depends on service abstractions, not concrete implementations
   - Factory functions (`create_webhook_handler()`) inject dependencies

## 📁 Directory Structure

```
billbolo-whatsapp-bot/
├── serverless.yml              # AWS Lambda + API Gateway infra
├── requirements.txt            # Python dependencies
├── .env.example               # Environment variable template
├── .gitignore                 # Git ignore rules
├── README.md                  # This file
│
├── src/
│   ├── __init__.py
│   ├── main.py                # FastAPI app + Lambda handler
│   ├── config.py              # Environment validation (Pydantic)
│   │
│   ├── services/              # Business logic layer (SRP)
│   │   ├── __init__.py
│   │   ├── whatsapp.py        # Meta Cloud API client
│   │   ├── ai_extractor.py    # Gemini Flash multimodal parser
│   │   ├── pdf_generator.py   # ReportLab quotation builder
│   │   ├── database.py        # DynamoDB user state management
│   │   ├── sheets.py          # Google Sheets integration (stub)
│   │   └── cashfree.py        # Cashfree payment links
│   │
│   └── handlers/              # Request handlers (orchestration)
│       ├── __init__.py
│       ├── webhook.py         # WhatsApp message processing
│       └── payments.py        # Payment webhook processing
```

## 🚀 Quick Start

### Prerequisites

- Python 3.12+
- AWS Account with Lambda access
- Google Cloud Project (Gemini API key)
- Meta Business Account (WhatsApp Cloud API token)
- Cashfree Account (API keys)

### Local Setup

```bash
# 1. Clone and enter project
git clone https://github.com/yourusername/billbolo-whatsapp-bot.git
cd billbolo-whatsapp-bot

# 2. Create virtual environment
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy .env.example to .env and fill in your credentials
cp .env.example .env
# Edit .env with your API keys

# 5. Run locally (for development)
uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

### Deploy to AWS Lambda

```bash
# 1. Install Serverless Framework
npm install -g serverless
npm install --save-dev serverless-python-requirements

# 2. Configure AWS credentials
aws configure

# 3. Deploy
serverless deploy --stage prod

# Output will show your API Gateway endpoints
```

## 📊 User Flow

```
User (contractor, designer, etc)
   │
   ├─ Sends WhatsApp message: "Hi"
   │  └─ Bot: "What's your business name?"
   │
   ├─ Replies: "ABC Interior Design"
   │  └─ System: Creates DynamoDB user profile with 5 free credits
   │
   ├─ Sends voice note: "Quotation for Sharma Ji, 80 sq ft wardrobe at 1,600 per sq ft"
   │  └─ System:
   │     1. Downloads .ogg file from WhatsApp
   │     2. Sends to Gemini Flash API
   │     3. Extracts: {client: "Sharma Ji", items: [{qty: 80, rate: 1600, unit: "sq ft"}]}
   │     4. Validates credit balance (used 0 < 5 free)
   │     5. Generates professional PDF with business name
   │     6. Uploads to S3
   │     7. Sends PDF to WhatsApp with watermark: "Made in 5s on BillBolo"
   │     8. Logs row to user's Google Sheet (async)
   │     9. Increments credits_used to 1
   │
   ├─ Repeats for 4 more free quotations
   │
   ├─ Sixth attempt: "Out of free credits! Upgrade to Pro for ₹499/month"
   │  └─ User clicks Cashfree payment link
   │
   └─ Payment success webhook
      └─ System:
         1. Verifies HMAC signature from Cashfree
         2. Updates DynamoDB: is_pro = true
         3. Sends confirmation: "🎉 Unlimited bills unlocked!"
```

## 💰 Pricing Model

| Tier | Credits | Price | Use Case |
|------|---------|-------|----------|
| **Free** | 5/month | ₹0 | Try before buying |
| **Pro** | Unlimited | ₹499/mo | Active users |
| **Pro Yearly** | Unlimited | ₹4,999/yr | Save 16% |

**Monetization**: Cashfree payment links → Stripe (UPI/Cards) → 70% net margin

## 🔐 Security & Best Practices

- ✅ **Environment variables** validated with Pydantic at startup
- ✅ **Defensive API error handling** (all external calls wrapped in try-catch)
- ✅ **HMAC signature verification** for Cashfree webhooks
- ✅ **Meta webhook token verification** on first request
- ✅ **DynamoDB On-Demand billing** (no over-provisioning)
- ✅ **S3 7-day auto-delete** (no stale PDFs lingering)
- ✅ **No hardcoded secrets** (all from .env)
- ✅ **Logging with correlation IDs** (track issues per user)
- ✅ **SOLID principles** (easy to test, swap, extend)

## 📈 Observability

Logs go to AWS CloudWatch by default. Key metrics to track:

- `messages_received`: Count of WhatsApp messages
- `quotations_generated`: Count of successful PDFs
- `credits_used`: Distribution of free vs Pro users
- `api_latency_ms`: Gemini API + PDF generation time
- `payment_conversions`: Free → Pro conversion rate

## 🧪 Testing (Roadmap)

```bash
# Unit tests
pytest tests/unit/

# Integration tests (with LocalStack/Docker)
pytest tests/integration/

# Load testing
locust -f tests/load/locustfile.py
```

## 📝 Environment Variables

See `.env.example` for all required variables. Key ones:

```bash
# WhatsApp
WHATSAPP_API_TOKEN=your_token
WHATSAPP_BUSINESS_ACCOUNT_ID=your_account_id
WHATSAPP_WEBHOOK_VERIFY_TOKEN=your_verify_token

# Google Gemini
GEMINI_API_KEY=your_api_key

# Cashfree
CASHFREE_API_KEY=your_key
CASHFREE_SECRET_KEY=your_secret

# AWS (auto-set by Serverless)
AWS_REGION=us-east-1
DYNAMODB_TABLE=billbolo-whatsapp-bot-prod
S3_BUCKET=billbolo-pdfs-prod
```

## 🤝 Contributing

1. Fork the repo
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Write tests
4. Follow SOLID principles
5. Submit PR

## 📞 Support

- **Issues**: GitHub Issues
- **Feature Requests**: Discussions
- **Email**: support@billbolo.io (placeholder)

## 📄 License

MIT License - See LICENSE file for details

---

**Built with ❤️ for Indian entrepreneurs.**

*"Virtual Munshi inside WhatsApp"* — BillBolo Team
