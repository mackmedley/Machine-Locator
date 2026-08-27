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
your account. So the app **refuses to start on a public address without a
password**:

```
$ mloc serve --host 0.0.0.0
Error: Refusing to listen on 0.0.0.0 without a password.
```

Set one and it starts:

```bash
export MACHINE_LOCATOR_PASSWORD='a long random phrase you can remember'
mloc serve --host 0.0.0.0 --port 8000
```

There's one password and one operator. This is a tool for a person running a
vending route, not a service with user accounts.

---

## Deploy to Render (easiest)

Render reads [`render.yaml`](render.yaml) and does the rest.

1. Push this repository to your own GitHub account.
2. At [render.com](https://render.com), pick **New → Blueprint** and point it at
   the repo.
3. Render creates the service, generates `MACHINE_LOCATOR_PASSWORD` and
   `MACHINE_LOCATOR_SECRET_KEY`, and attaches a 1 GB disk at `/data`.
4. Copy the generated password out of the dashboard — that's your login.
5. Add your mail password as `MACHINE_LOCATOR_SMTP_PASSWORD` in the dashboard.
   Don't commit it.

The blueprint uses the **starter** plan on purpose. The free plan has no
persistent disk, so every redeploy would wipe your prospect list and pipeline.

---

## Deploy anywhere else

The [`Dockerfile`](Dockerfile) is plain and portable:

```bash
docker build -t machine-locator .
docker run -p 8000:8000 \
  -e MACHINE_LOCATOR_PASSWORD='a long random phrase' \
  -e MACHINE_LOCATOR_SMTP_PASSWORD='your app password' \
  -e MACHINE_LOCATOR_HTTPS=1 \
  -v machine-locator-data:/data \
  machine-locator
```

Fly.io, Railway and Heroku all work from the same image, or from the
[`Procfile`](Procfile) if the host builds from source.

### Settings that matter

| Variable | What it does |
|---|---|
| `MACHINE_LOCATOR_PASSWORD` | **Required in public.** Turns on the login and is the password you type. |
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
