# SocialMediaAI — Instagram Business AI Planner & Auto-Poster

Ocoya-inspired AI social media management platform for **Instagram Business & Creator** accounts:
- **3-Step Ocoya-Style Onboarding**: Brand auto-fetch from website + Brand Kit + Direct Meta OAuth (`Facebook Login for Business`).
- **Real-Time Analytics & Dynamic 7×24 Best-Time Heatmap**: Computed strictly from time-decayed historical reach/engagement and Meta `online_followers` (zero hardcoded times).
- **AI Growth Copilot & Trending Hashtag Engine**: Powered by Google Gemini (`google-genai`) + Meta `ig_hashtag_search` (with strict 30-unique-hashtags / 7-day rolling quota guard) + account hashtag lift.
- **Full Pre-Publish Preview & Smart Multi-Post Queue**: Preview Feed/Reel mockups, edit captions/hashtags/times, publish immediately, or let AI space multiple posts across peak reach days.

---

## Localhost Quick Start

### 1. Configure Environment
Copy `.env.example` to `.env` at the repo root and fill in your credentials:
- `META_APP_ID` & `META_APP_SECRET` (from `developers.facebook.com`)
- `GEMINI_API_KEY` (from Google AI Studio)
- `TOKEN_ENCRYPTION_KEY` (generate with `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`)
- `MEDIA_STORAGE_PROVIDER=local` for local preview, or `cloudinary` + `CLOUDINARY_URL` for live Instagram publishing.

### 2. Run Backend (FastAPI + APScheduler)
```powershell
cd backend
.\.venv\Scripts\uvicorn.exe app.main:app --reload --port 8000
```

### 3. Run Frontend (Next.js 15)
Copy `frontend/.env.local.example` to `frontend/.env.local`, then:
```powershell
cd frontend
npm.cmd run dev
```
Open `http://localhost:3000`.

---

## Free-Tier Production Deployment (Render + Vercel + Neon + Cloudinary)
To stay well within Render's **750 free instance-hours/month** while keeping auto-posting reliable:
1. **Frontend → Vercel (Free)**: Deploy `frontend/` with `NEXT_PUBLIC_API_URL=https://your-render-api.onrender.com`. (0 Render hours used!)
2. **Database → Neon or Supabase (Free PostgreSQL)**: Set `DATABASE_URL` in Render to your Neon/Supabase connection string.
3. **Media Storage → Cloudinary (Free Tier)**: Set `MEDIA_STORAGE_PROVIDER=cloudinary` and `CLOUDINARY_URL` so Reels/Images survive Render restarts and have public HTTPS URLs for Meta.
4. **Backend → Render Free Web Service** (using `render.yaml`, ≤ 744 hrs/month):
   - Point **UptimeRobot** or **cron-job.org** to `POST https://your-render-api.onrender.com/api/worker/tick?secret=<CRON_SECRET>` every **5–10 minutes** to keep the backend awake and publish due scheduled posts.
