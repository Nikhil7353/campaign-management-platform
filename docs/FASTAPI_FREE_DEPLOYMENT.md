# Free FastAPI + React deployment

This deployment uses managed cloud services and does not require shared hosting.

## Recommended services

- React frontend: Vercel Hobby or Render Static Site.
- FastAPI backend: Render Web Service for testing and small workloads.
- Database: a managed PostgreSQL/MySQL-compatible database. Do not rely on the local SQLite file on a hosted instance because free web instances can restart and lose local files.
- Outbound email: Brevo SMTP (`smtp-relay.brevo.com:587`).
- WhatsApp: Meta WhatsApp Cloud API through `graph.facebook.com`.

## FastAPI environment

Copy `backend-fastapi/.env.example` to `backend-fastapi/.env` and set:

```ini
DATABASE_URL=sqlite:///./storage/database.sqlite
FRONTEND_ORIGIN=https://your-vercel-site.example
MAIL_DRY_RUN=false
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USERNAME=
SMTP_PASSWORD=
MAIL_FROM_ADDRESS=
MAIL_FROM_NAME=Communication Desk
WHATSAPP_API_VERSION=v23.0
WHATSAPP_PHONE_NUMBER_ID=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_VERIFY_TOKEN=
```

Brevo requires the sender domain/address to be verified. Meta requires a WhatsApp Business app, a phone-number ID, an access token, and approved message templates for business-initiated messages.

## Commands

From `backend-fastapi`:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

From `frontend-react`:

```bash
npm run build
```

Set the Vercel environment variable `VITE_API_URL` to the public FastAPI URL plus `/api`, for example `https://your-api.onrender.com/api`.

The email worker can be invoked by a scheduler with `POST /api/workers/process-email`; inbox synchronization is optional and uses the configured IMAP mailbox with `POST /api/workers/sync-inbox`.
