# Putting Machine Locator on the internet

You don't have to. Double-clicking the launcher runs it on your own computer,
which is simpler, free, and keeps your data on your machine. Host it only if you
actually need to reach it from your phone, from the van, or from a second
computer.

If you do, read the next section first — it's short and it matters.

---

## What you're exposing

The database holds:

- your **email account password**, so the app can send on your behalf
- your **prospect list** and everything you've written about each one
- your **outreach queue** — emails scheduled to go out under your name

An unprotected public instance would let anyone who finds the URL send mail from
your account. So a public instance **must** have a password — but you don't have
to set one up front. The first time you open the site it asks you to pick one,
and nothing else works until you do:

> **Pick a password**
> This one's on the internet, so it needs a password before it will do anything.

One password, one operator. There are no accounts to manage.

Do it promptly after deploying: between the deploy finishing and you choosing a
password, whoever opens the URL first gets to set it. There's nothing in the
instance yet at that point, so the worst case is you redeploy — but if you'd
rather close that window entirely, set `MACHINE_LOCATOR_PASSWORD` in your host's
dashboard before the first deploy and the app will use that instead.

---

## Deploy to Render (easiest)

Render reads [`render.yaml`](render.yaml) and does the rest.

1. Push this repository to your own GitHub account.
2. At [render.com](https://render.com), pick **New → Blueprint** and point it at
   the repo. (Or use the deploy button in the README.)
3. Render builds it, attaches a 1 GB disk at `/data`, and gives you a URL.
4. Open the URL. It asks you to pick a password. That's your login from then on.
5. In the app: **Settings** → fill in your business details and mail account.
   For the mail password you can either type it into Settings, or set
   `MACHINE_LOCATOR_SMTP_PASSWORD` in the Render dashboard so it never touches
   the database.

The blueprint uses the **starter** plan on purpose. The free plan has no
persistent disk, so every redeploy would wipe your prospect list and pipeline.

---

## Deploy anywhere else

The [`Dockerfile`](Dockerfile) is plain and portable:

```bash
docker build -t machine-locator .
docker run -p 8000:8000 \
  -e MACHINE_LOCATOR_HTTPS=1 \
  -v machine-locator-data:/data \
  machine-locator
```

Then open `http://localhost:8000` and pick a password when it asks.

Fly.io, Railway and Heroku all work from the same image, or from the
[`Procfile`](Procfile) if the host builds from source.

### Settings that matter

| Variable | What it does |
|---|---|
| `MACHINE_LOCATOR_PASSWORD` | Optional. Sets the login password from the host instead of picking one in the browser. When set, it wins and the in-app password form is disabled. |
| `MACHINE_LOCATOR_SECRET_KEY` | Signs session cookies. Set it, or one is generated and stored in the database. |
| `MACHINE_LOCATOR_HTTPS` | Set to `1` behind HTTPS so the session cookie is marked secure. |
| `MACHINE_LOCATOR_HOME` | Where the database lives. Point it at your mounted disk. |
| `MACHINE_LOCATOR_SMTP_PASSWORD` | Your mail password, kept out of the database. |
| `MACHINE_LOCATOR_IMAP_PASSWORD` | Mailbox password for reply checking, if different. |
| `MACHINE_LOCATOR_CITY` | The metro to search. Defaults to Oklahoma City. |

### Two things that will bite you

**Mount a disk.** The database is a SQLite file. On a host with an ephemeral
filesystem — which is most of them by default — a redeploy resets you to an
empty prospect list. Mount a volume at `MACHINE_LOCATOR_HOME` and point the app
at it.

**Run one worker.** Background scans and sends are threads inside the process
that started them, and SQLite is a single file. Several worker processes would
each hold their own job runner and contend over the same file for nothing:

```
gunicorn machine_locator.wsgi:app --workers 1 --threads 4 --timeout 300
```

The long timeout is because an Overpass scan of a whole metro takes minutes.

---

## Getting the app onto a phone

Once it's hosted, open the URL in your phone's browser and add it to the home
screen. The layout collapses to a single column, and the call buttons on a
prospect dial straight out — which is most of what you want standing in a car
park anyway.
