# Cron workers

## Inbox sync (Hostinger)

Keep the desk inbox fresh even when nobody is signed in.

### Option A — CLI cron (recommended)

In Hostinger hPanel → **Advanced** → **Cron Jobs**:

- Schedule: every **5 minutes** (`*/5 * * * *`)
- Command:

```sh
/opt/alt/php82/usr/bin/php /home/u105084344/domains/rslsolution.com/public_html/mailpilot/backend/scripts/cron/sync-inbox.php
```

Fallback PHP binary if needed:

```sh
/usr/bin/php /home/u105084344/domains/rslsolution.com/public_html/mailpilot/backend/scripts/cron/sync-inbox.php
```

### Option B — URL cron

1. Set `CRON_SECRET` in `backend/.env`.
2. Add a Hostinger cron that fetches:

```text
https://mailpilot.rslsolution.com/api/cron/inbox-sync?token=YOUR_CRON_SECRET
```

Every 5 minutes.

## Email queue worker

```sh
php backend/scripts/cron/process-emails.php
```

## WhatsApp send worker

```sh
php backend/scripts/cron/send-whatsapp.php
```

These are plain PHP workers. No Laravel artisan.
