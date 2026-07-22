# 🏃‍♂️ Gemini Health Agent

Personal AI health tracker yang menggabungkan data lari dari Strava, analisis nutrisi via foto makanan, dan coaching berbasis AI — semua terhubung ke dashboard web dan Telegram bot.

## Features

- **Auto-sync Strava** — tarik data lari otomatis setiap 15 menit ke Supabase
- **Nutrition AI** — foto makanan → analisis kalori & makro instan via Gemini Vision
- **AI Running Coach** — zona latihan & evaluasi performa personal berdasarkan data aktual
- **Readiness Score** — kalkulasi ACWR (Acute:Chronic Workload Ratio) untuk cek kondisi tubuh
- **Telegram Bot** — sync Strava & log nutrisi langsung dari HP
- **Multi-user** — auth via Google (Supabase), data ter-isolasi per user

## Tech Stack

| Layer | Tech |
|---|---|
| Backend | FastAPI (Python) |
| AI | Google Gemini 2.5 Flash |
| Database | Supabase (PostgreSQL) |
| Running Data | Strava API |
| Bot | pyTelegramBotAPI |
| Scheduler | APScheduler |
| Frontend | Jinja2 + Tailwind CSS |

## Getting Started

### 1. Clone repo

```bash
git clone https://github.com/coretail/gemini-health-agent.git
cd gemini-health-agent
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Setup environment variables

Buat file `.env` di root project:

```env
GEMINI_API_KEY=your_gemini_api_key
TELEGRAM_TOKEN=your_telegram_bot_token
STRAVA_CLIENT_ID=your_strava_client_id
STRAVA_CLIENT_SECRET=your_strava_client_secret
STRAVA_REFRESH_TOKEN=your_strava_refresh_token
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_supabase_anon_key
BASE_URL=http://localhost:8000
```

### 4. Jalankan app

```bash
uvicorn app:app --reload
```

Buka `http://localhost:8000` di browser.

## Deployment (Railway / Render)

1. Set semua env variables di platform dashboard
2. Set `BASE_URL` ke domain production (contoh: `https://your-app.railway.app`)
3. Daftarkan domain tersebut di Strava API settings sebagai redirect URI
4. Deploy menggunakan `Procfile`:

## Author

**Abdullah Dzaki** — [github.com/coretail](https://github.com/coretail) · [linkedin.com/in/abdullah-dzaki](https://linkedin.com/in/abdullah-dzaki)
