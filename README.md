# Communication Desk

Communication Desk is a FastAPI and React application for email campaigns, inbox synchronization, WhatsApp messaging, and CSV/XLSX lead imports.

## Current project status

The project has been migrated from the legacy PHP application to a provider-neutral FastAPI + React platform. The current project folder and recommended Git repository name are:

```text
campaign-management-platform
```

Completed:

- Removed the legacy PHP backend, PHP frontend, and shared-hosting-specific files.
- Added the FastAPI backend with SQLAlchemy, local SQLite support, email queue routes, inbox routes, WhatsApp routes, Sheets upload routes, and Google OAuth configuration.
- Added Brevo SMTP support for outbound email.
- Added optional IMAP synchronization for inbound email.
- Added Meta WhatsApp Cloud API support.
- Added the React/Vite frontend with separate Email Desk and WhatsApp Desk workflows.
- Restored the reference application structure: dashboard, inbox, sent, sheets, mail accounts, integrations, chats, linked device, and settings screens.
- Added the current modern light-blue glassmorphism visual theme.
- Updated the deployment documentation for free cloud hosting options.
- Verified the frontend production build with `npm run build`.

Next steps:

1. Configure `backend-fastapi/.env` with the database, Brevo, IMAP, Meta, and Google credentials that are required.
2. Start FastAPI and connect the React frontend to the running API.
3. Test email sending, inbox synchronization, WhatsApp sending, and spreadsheet uploads with real provider accounts.
4. Add authentication, background scheduling, production database configuration, and deployment secrets before production use.
5. Deploy the frontend and backend using the checklist in the deployment guide.

The interface is ready for continued design changes. Provider credentials and production deployment are not included in this repository.

The legacy application has been removed. The active stack is:

- FastAPI + SQLAlchemy backend
- React + Vite frontend
- SQLite for local development; MySQL-compatible SQL schema for deployment
- Brevo SMTP for outbound email
- Optional IMAP mailbox for inbound email synchronization
- Meta WhatsApp Cloud API for WhatsApp messages
- Google OAuth configuration for Sheets integration

## Project structure

```text
backend-fastapi/
├── app/
│   ├── config.py       # Environment settings
│   ├── db.py           # SQLAlchemy engine and local schema bootstrap
│   ├── main.py         # FastAPI routes
│   └── services.py     # Email, IMAP, Google, and WhatsApp services
├── requirements.txt
└── .env.example

frontend-react/
├── src/main.jsx        # React application and screens
├── src/styles.css
├── package.json
└── vite.config.js

database.sql            # Deployment schema
docs/FASTAPI_FREE_DEPLOYMENT.md
```

## Local setup

### Backend

Use Python 3.13 or a compatible Python 3.11+ environment:

```powershell
cd backend-fastapi
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

The backend initializes a local SQLite database at `backend-fastapi/storage/database.sqlite`.

### Frontend

```powershell
cd frontend-react
npm install
npm run dev
```

Set `VITE_API_URL` when the backend is not running at `http://localhost:8000/api`.

## Provider configuration

Copy `backend-fastapi/.env.example` to `.env` and configure the providers you use.

### Brevo email

```ini
MAIL_DRY_RUN=false
SMTP_HOST=smtp-relay.brevo.com
SMTP_PORT=587
SMTP_USERNAME=your-brevo-smtp-login
SMTP_PASSWORD=your-brevo-smtp-key
MAIL_FROM_ADDRESS=verified-sender@example.com
MAIL_FROM_NAME=Communication Desk
```

The sender address or domain must be verified in Brevo. The free Brevo plan currently includes 300 emails per day.

### Meta WhatsApp Cloud API

```ini
WHATSAPP_API_VERSION=v23.0
WHATSAPP_PHONE_NUMBER_ID=your-phone-number-id
WHATSAPP_ACCESS_TOKEN=your-access-token
WHATSAPP_VERIFY_TOKEN=your-webhook-token
```

The application sends text messages through Meta's official Graph API endpoint. Business-initiated messages may require approved templates and Meta billing.

### Inbound email

Brevo is used for outbound SMTP. If inbox synchronization is needed, configure an IMAP mailbox from a provider such as Gmail or Outlook:

```ini
IMAP_HOST=imap.example.com
IMAP_PORT=993
IMAP_USERNAME=inbox@example.com
IMAP_PASSWORD=app-password
```

Run synchronization with `POST /api/workers/sync-inbox`.

## API routes

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/inbox`
- `POST /api/inbox/{email_id}/read`
- `GET /api/emails`
- `POST /api/emails`
- `POST /api/emails/{email_id}/send`
- `POST /api/workers/process-email`
- `POST /api/workers/sync-inbox`
- `GET|POST /api/whatsapp/settings`
- `GET /api/whatsapp/chats`
- `GET /api/whatsapp/device`
- `POST /api/whatsapp/send`
- `GET /api/whatsapp/overview`
- `GET /api/google/status`
- `POST /api/google/connect`
- `POST /api/google/disconnect`
- `GET /api/sheets`
- `POST /api/sheets/upload`

## Deployment

For a free test deployment, host the React build on Vercel or Render Static Sites and the FastAPI service on Render. Use a managed database for hosted deployments instead of relying on local SQLite storage.

See [docs/FASTAPI_FREE_DEPLOYMENT.md](docs/FASTAPI_FREE_DEPLOYMENT.md) for the deployment checklist and environment variables.

## Git repository handoff

Recommended repository name:

```text
campaign-management-platform
```

From the project folder, create the repository on GitHub with that name, keep it private if it contains company work, and push the code:

```powershell
cd "C:\Campaign project\campaign-management-platform"
git add .
git commit -m "Complete FastAPI and React migration"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/campaign-management-platform.git
git push -u origin main
```

Do not commit `backend-fastapi/.env`, SQLite files, access tokens, or provider credentials. The existing `.gitignore` is intended to exclude local secrets and generated files.

## Validation

```powershell
cd backend-fastapi
python -m compileall -q app

cd ..\frontend-react
npm run build
```

Keep provider credentials in environment variables or the deployment secret store. Do not commit `.env` files or access tokens.
