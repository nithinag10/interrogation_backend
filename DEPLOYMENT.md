# Railway Deployment

## 1) Prerequisites
- A Railway project connected to this repository.
- Your frontend hosted at `https://idea-sharpen.vercel.app`.

## 2) Build/Runtime
This repo is configured to deploy with Docker on Railway:
- `Dockerfile` starts: `uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}`
- `railway.json` sets Dockerfile build + healthcheck.

## 3) Railway Variables
Set these in Railway `Project -> Variables`:
- `OPENAI_API_KEY`
- `LANGSMITH_TRACING` (optional, e.g. `true`)
- `LANGSMITH_ENDPOINT` (optional, default: `https://api.smith.langchain.com`)
- `LANGSMITH_API_KEY` (optional)
- `LANGSMITH_PROJECT` (optional, e.g. `interrogation`)
- `CORS_ALLOW_ORIGINS`
  - Example: `https://idea-sharpen.vercel.app`
  - For multiple frontends, comma-separate values.

## 4) Deploy
1. Push these files to your Git branch.
2. In Railway, trigger deploy from that branch (or enable auto-deploy).
3. After deployment, verify:
   - `GET /health` returns `{"status":"ok"}`
   - `GET /api/stakeholders` returns the stakeholders payload.

## 5) Frontend configuration
Point your frontend API base URL to the Railway service domain:
- Example: `https://<your-service>.up.railway.app`

## 6) SSE notes
`/api/simulations/{id}/events` is Server-Sent Events and should work behind Railway with current headers.
