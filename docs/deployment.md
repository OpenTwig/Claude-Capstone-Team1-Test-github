# Deployment runbook: self-hosted (Proxmox LXC + Cloudflare Tunnel)

This is how to put the Stock Trading System on the internet from your own Proxmox server: one Ubuntu LXC container holding the app, the price generator, and MySQL, published through a Cloudflare Tunnel. It is stage 1 of the hosting plan (decision D28 in `docs/requirements.md`). The later cloud stage is described in `docs/design.md`, "Production".

Replace these placeholders everywhere:

| Placeholder | Meaning |
|---|---|
| `trading.example.com` | the public hostname you'll use, on a domain in your Cloudflare account |
| `<github-token>` | only if the GitHub repository is private: a fine-grained personal access token with read-only **Contents** access to it, typed as the password when git asks |

> **Run every command in a root shell inside the container.** If you log in as a normal user, start with `sudo -i`: the prompt changes to `root@…#`. Don't put `sudo` in front of single lines. A line like `sudo MIGRATE_PW=…` fails, and the next lines then run as your own user; in particular, Ubuntu's MySQL only lets the system's root user in without a password.

---

## How the pieces fit

`cloudflared` (the tunnel) can run in one of two places. Pick one; the steps below say where they differ.

**A. Tunnel inside the container** (simplest): Gunicorn listens on `127.0.0.1` only, so nothing outside the container can reach it.

```
Visitor -- HTTPS --> Cloudflare == tunnel ==> [ LXC: cloudflared --> Gunicorn 127.0.0.1:8000 -- MySQL (localhost) ]
```

**B. Tunnel on its own VM** (for example one tunnel VM serving several sites): Gunicorn listens on the container's LAN address, such as `10.1.0.14:8000`, and the Proxmox firewall lets **only the tunnel VM** connect to that port (step 9B).

```
Visitor -- HTTPS --> Cloudflare == tunnel ==> [ tunnel VM: cloudflared ] -- LAN, port 8000 only --> [ LXC: Gunicorn 10.1.0.14:8000 -- MySQL (localhost) ]
```

Either way:

- HTTPS ends at Cloudflare, and the container accepts no connections from the internet.
- MySQL listens on `127.0.0.1` only.
- The app runs with `APP_ENV=production`, so it refuses to start with debug on, a weak `SECRET_KEY`, the MySQL `root` account, insecure cookies, or when running as root.
- The app trusts exactly one proxy in front of it (`cloudflared`) for each visitor's address and for `https`. That is why, in layout B, nothing but the tunnel VM may reach port 8000: a direct visitor could fake those headers and would be on plain HTTP.
- Secrets live in one file, `/etc/trading/trading.env`, readable only by root and the `trading` group.

---

## 1. Create the container (Proxmox web UI)

1. **Get the template:** your storage → **CT Templates** → **Templates** → download **ubuntu-24.04-standard**.
2. **Create CT**, with these settings:
   - **General:** hostname `trading`; **Unprivileged container: on**; **Nesting: on** (systemd's service hardening needs it).
   - **Template:** ubuntu-24.04-standard.
   - **Disks:** 16 GB or more.
   - **CPU:** 2 cores. **Memory:** 2048 MB, swap 512 MB.
   - **Network:** a bridge on your LAN. Layout A needs outbound internet access only, so DHCP is fine. Layout B needs a **static** address (for example `10.1.0.14/24`, with your gateway), because the tunnel VM connects to it.
   - **DNS:** use the host settings.
3. Tick **Start after created**, then open the container's **Console**.
4. Optional: under **Options**, turn on **Start at boot**.

Check it's up:

```bash
apt update && apt full-upgrade -y
timedatectl        # the container clock must be right; it follows the Proxmox host
```

The system time zone can stay UTC. The app keeps market hours in Arizona time itself (`MARKET_TIMEZONE`).

---

## 2. Install the software

```bash
apt install -y git curl ca-certificates software-properties-common mysql-server
```

**Python 3.14.** The project is developed and tested on 3.14; Ubuntu 24.04 ships 3.12, so install 3.14 alongside it:

```bash
add-apt-repository -y ppa:deadsnakes/ppa
apt update
apt install -y python3.14 python3.14-venv
python3.14 --version
```

If the PPA doesn't offer 3.14 for your Ubuntu release, use `uv` instead:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # installs to /root/.local/bin
/root/.local/bin/uv python install 3.14
# then, in step 4, create the venv with:  /root/.local/bin/uv venv --python 3.14 /opt/trading/.venv
```

---

## 3. Create the service user

The app and the price generator run as `trading`, a system user with no login shell and no home directory:

```bash
useradd --system --no-create-home --shell /usr/sbin/nologin trading
```

---

## 4. Get the code

```bash
git clone https://github.com/OpenTwig/Claude-Capstone-Team1-Test-github.git /opt/trading   # private repo: your GitHub username, then <github-token> as the password
python3.14 -m venv /opt/trading/.venv
/opt/trading/.venv/bin/pip install --upgrade pip
/opt/trading/.venv/bin/pip install -r /opt/trading/requirements.txt
```

The code stays owned by root: the `trading` user can read and run it, but not change it.

```bash
chown -R root:root /opt/trading
chmod -R go-w /opt/trading
```

---

## 5. Set up MySQL

Ubuntu's MySQL already listens on `127.0.0.1` only. Check it:

```bash
grep -E '^\s*bind-address' /etc/mysql/mysql.conf.d/mysqld.cnf    # expect 127.0.0.1
```

Create the database and the two application accounts. On Ubuntu, `mysql` run **as root** logs in as the MySQL root account without a password (socket authentication). Run as any other user, it fails with `ERROR 1045 … Access denied`, so check your prompt ends in `#` (see the note at the top):

```bash
MIGRATE_PW=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
APP_PW=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
mysql <<SQL
CREATE DATABASE IF NOT EXISTS trading_db CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS 'trading_migrate'@'localhost' IDENTIFIED BY '${MIGRATE_PW}';
GRANT ALL PRIVILEGES ON trading_db.* TO 'trading_migrate'@'localhost' WITH GRANT OPTION;
CREATE USER IF NOT EXISTS 'trading_app'@'localhost' IDENTIFIED BY '${APP_PW}';
SQL
echo "migrate: $MIGRATE_PW"; echo "app: $APP_PW"    # you'll paste these in step 6
```

- `trading_migrate` creates and changes tables (`flask db-upgrade`). It needs `GRANT OPTION`, because after migrating it gives `trading_app` its exact table permissions.
- `trading_app` is what the app uses day to day. It gets only the permissions it needs: for example, no `DELETE` except on positions, and no changes at all to History or Transaction (R17.2).

**Binary logs:** MySQL keeps a copy of every change for 30 days by default, which allows point-in-time recovery but uses disk. Seven days is plenty here:

```bash
echo -e "[mysqld]\nbinlog_expire_logs_seconds = 604800" > /etc/mysql/mysql.conf.d/trading.cnf
systemctl restart mysql
```

---

## 6. Write the settings file

Generate the two secrets:

```bash
/opt/trading/.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"                            # SECRET_KEY
/opt/trading/.venv/bin/python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # FIELD_ENCRYPTION_KEYS
```

Create `/etc/trading/trading.env`:

```bash
install -d -m 0750 -o root -g trading /etc/trading
install -m 0640 -o root -g trading /dev/null /etc/trading/trading.env
nano /etc/trading/trading.env
```

Its contents (one `KEY=value` per line, no quotes):

```ini
APP_ENV=production
SECRET_KEY=<the first value above>
FIELD_ENCRYPTION_KEYS=<the second value above>

DB_HOST=localhost
DB_PORT=3306
DB_NAME=trading_db
DB_USER=trading_app
DB_PASSWORD=<app password from step 5>
DB_MIGRATE_USER=trading_migrate
DB_MIGRATE_PASSWORD=<migrate password from step 5>

# Layout B only (tunnel on another VM): listen on the container's LAN address instead of 127.0.0.1
# GUNICORN_BIND=10.1.0.14:8000

# Shown on the Transfer Funds page (D24)
SUPPORT_EMAIL=support@example.com
SUPPORT_HOURS=Monday–Friday, 9:00 AM–5:00 PM Arizona time
```

> **Keep a copy of `SECRET_KEY` and `FIELD_ENCRYPTION_KEYS` in a password manager, outside the server.** The encryption key is the only way to read the stored SSNs and bank account numbers: if it is lost, they are gone for good, even with a database backup. A new `SECRET_KEY` only signs everyone out.

---

## 7. A helper for running app commands

App commands (`db-upgrade`, `create-admin`, …) must run as `trading`, with the settings file read **the way systemd reads it** (values may contain spaces, like `SUPPORT_HOURS`). `systemd-run` does exactly that. Create `/usr/local/bin/trading-flask`:

```bash
cat > /usr/local/bin/trading-flask <<'EOF'
#!/bin/sh
# Run a Flask CLI command as the trading user, with the production settings read exactly as systemd reads them.
exec systemd-run --quiet --pty --wait --collect --uid=trading --gid=trading   -p EnvironmentFile=/etc/trading/trading.env -p WorkingDirectory=/opt/trading   -E FLASK_SKIP_DOTENV=1   /opt/trading/.venv/bin/flask --app app "$@"
EOF
chmod 0755 /usr/local/bin/trading-flask
```

> Don't create a `.env` file in `/opt/trading`: that is for local development only (README quickstart). In production the app never reads one; all settings come from `/etc/trading/trading.env`.

Create the tables, then the first administrator:

```bash
trading-flask db-upgrade       # applies every migration and trading_app's table permissions
trading-flask create-admin     # asks for name, email, date of birth, SSN, and a password (12+ characters)
```

`flask seed` (sample stocks and a demo customer) refuses to run outside development, on purpose. On this site, an administrator adds stocks from the Administration page.

---

## 8. Start the app and the price generator

```bash
cp /opt/trading/deploy/systemd/trading-web.service /opt/trading/deploy/systemd/trading-price-generator.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now trading-web trading-price-generator
systemctl status trading-web trading-price-generator --no-pager
```

Check the app answers (layout B: use the LAN address, such as `10.1.0.14`, instead of `127.0.0.1`):

```bash
curl -s http://127.0.0.1:8000/health          # {"database":"ok","status":"ok"}
curl -sI http://127.0.0.1:8000/signin | grep -i '^server'   # "Server: webserver": no software or version
```

Logs:

```bash
journalctl -u trading-web -f                  # requests and errors
journalctl -u trading-price-generator -f      # "price generator started", then ticks while the market is open
```

---

## 9. Publish it with a Cloudflare Tunnel

### 9A. Tunnel inside the container

The dashboard-managed tunnel is the simplest:

1. In the Cloudflare dashboard, open **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel** → **Cloudflared**, and name it `trading`.
2. Choose **Debian**, 64-bit, and run the install commands it shows, inside the container. The last one is `cloudflared service install <token>`, which installs and starts `cloudflared` as a service.
3. **Public hostname:** subdomain and domain for `trading.example.com`; **Service**: type `HTTP`, URL `127.0.0.1:8000`.
4. Save. Cloudflare creates the DNS record for you.

(If you prefer a tunnel managed from a file, `deploy/cloudflared/config.yml.example` has the equivalent configuration.)

### 9B. Tunnel on its own VM

1. In the container, the settings file has `GUNICORN_BIND=10.1.0.14:8000` (step 6). After changing it, run `systemctl restart trading-web`.
2. **Firewall port 8000 so only the tunnel VM can connect (strongly recommended).** Until this is done, anyone on your LAN can open the site directly over plain HTTP, and can fake the forwarded-address header the login rate limit relies on; visitors coming through Cloudflare are unaffected. Use Proxmox's firewall: Ubuntu's `ufw` often doesn't work inside unprivileged containers.
   1. **Datacenter** → **Firewall** → **Options**: **Firewall** must be **Yes**.
      Careful: this also switches on Proxmox's host rules. By default they still allow the web interface (port 8006) and SSH from your local network. If you have changed Proxmox's firewall before, check first that you won't lock yourself out.
   2. **The container** → **Firewall** → **Add** these two rules, in this order:
      - Direction `in`, Action `ACCEPT`, Protocol `tcp`, Dest. port `8000`, Source `<tunnel-vm-ip>`, Enable ✓
      - Direction `in`, Action `DROP`, Protocol `tcp`, Dest. port `8000`, Enable ✓
   3. **The container** → **Firewall** → **Options**: **Firewall** **Yes**.
   4. **The container** → **Network** → `net0`: tick **Firewall**.
3. **Test the firewall:**
   - From the tunnel VM, `curl -s http://10.1.0.14:8000/health` answers `{"database":"ok","status":"ok"}`.
   - From any other machine, `curl -m 5 http://10.1.0.14:8000/health` **times out**.
4. **In the Cloudflare dashboard,** on the tunnel that runs on your tunnel VM, add a public hostname for `trading.example.com` with **Service** type `HTTP`, URL `10.1.0.14:8000`.

### Both layouts

Then, in the Cloudflare dashboard for your domain:

- **SSL/TLS** → **Edge Certificates**: turn on **Always Use HTTPS**, and set **Minimum TLS Version** to 1.2.
- **Security** → **WAF**: turn on the managed rules your plan includes.
- Optional: add a rate-limiting rule for `/signin`, on top of the app's own login limit.

Check from outside:

```bash
curl -sI https://trading.example.com/signin | grep -iE '^(server|strict-transport-security|content-security-policy)'
```

Expect `server: cloudflare`, plus the app's `strict-transport-security` and `content-security-policy` headers.

**Check that visitors' real addresses reach the app.** The login rate limit and the access log depend on it. Open the site from your phone on mobile data, then:

```bash
journalctl -u trading-web -n 20 --no-pager    # the first field of each request line should be your phone's address, not 127.0.0.1
```

---

## 10. First login and a smoke test

1. Open `https://trading.example.com` and sign in as the administrator from step 7.
2. **Administration** → **Market Time**: set the hours you want (default 9:00 AM–5:00 PM Arizona time, weekdays, except market holidays).
3. **Add Stocks**: add a few, with **Randomize History** on so the charts have a year of prices.
4. **Add User**: create a customer, and note the temporary password shown once.
5. In a private window, sign in as that customer, choose a new password, create a portfolio, link a bank account, deposit, and buy a stock while the market is open.
6. After 5 minutes of market hours, the Stocks page prices should have moved. If "Prices may be delayed" shows, check the price generator's log.

---

## 11. Monitoring

`/health` answers **200** `{"database":"ok","status":"ok"}` when the app and its database both work, and **503** `{"database":"unavailable","status":"error"}` when the database doesn't. It needs no login and reveals nothing else; the error itself goes to the journal (`journalctl -u trading-web`).

Point an uptime monitor at `https://trading.example.com/health`, expecting status 200. For example, Uptime Kuma in its own container on the same Proxmox host, or a free external service, so you hear when the site or its database goes down. A monitor outside your network also notices if the tunnel or your internet connection drops.

---

## 12. Updating to a new version

```bash
cd /opt/trading
git pull
.venv/bin/pip install -r requirements.txt
trading-flask db-upgrade
systemctl restart trading-web trading-price-generator
curl -s http://127.0.0.1:8000/health
```

Before a big update, take a Proxmox snapshot of the container (**Snapshots** → **Take Snapshot**), so you can roll back in one click. Always take one when the update adds migrations (new files in `migrations/`): MySQL can't undo a migration that fails part-way.

`git pull` asks for credentials if the repository is private (your GitHub username, then `<github-token>`): the token is never stored in the container.

### Moving a container cloned from Gitea to GitHub (once)

The code moved from Gitea to GitHub on 2026-10-18 with a fresh history (its first commit is `60c549d`, "initial push"), so a container cloned from Gitea can't `git pull` from GitHub ("refusing to merge unrelated histories"). Point it at GitHub and jump to GitHub's version instead, then carry on with the update steps above from `pip install`:

```bash
cd /opt/trading
git status --short            # must print nothing: the container's code is never edited. If it lists files, stop and look.
git remote set-url origin https://github.com/OpenTwig/Claude-Capstone-Team1-Test-github.git
git fetch origin
git reset --hard origin/main  # replaces the old checkout with GitHub's main; .venv and other ignored files are kept
git branch --set-upstream-to=origin/main main
```

---

## 13. Backups (3-2-1)

Three copies, on two kinds of storage, one of them off site:

1. **The live database** in the container.
2. **Proxmox backups** of the whole container (on the Proxmox backup storage). In **Datacenter** → **Backup**, add a daily job for the container: mode **Snapshot**, and keep at least 7.
3. **A daily database dump copied off the server**, for example to another machine, a NAS, or cloud storage.

Set up the daily dump. A read-only MySQL account for backups:

```bash
BACKUP_PW=$(openssl rand -base64 30 | tr -d '/+=' | cut -c1-32)
mysql -e "CREATE USER IF NOT EXISTS 'trading_backup'@'localhost' IDENTIFIED BY '${BACKUP_PW}';
          GRANT SELECT, SHOW VIEW, TRIGGER, LOCK TABLES ON trading_db.* TO 'trading_backup'@'localhost';"
install -m 0600 /dev/null /etc/trading/backup.cnf
printf '[mysqldump]\nuser=trading_backup\npassword=%s\n' "$BACKUP_PW" > /etc/trading/backup.cnf
install -d -m 0700 /var/backups/trading
```

The backup script, `/usr/local/sbin/trading-backup`:

```bash
cat > /usr/local/sbin/trading-backup <<'EOF'
#!/bin/sh
# Daily dump of trading_db, kept for 14 days. Copy /var/backups/trading off the server (see below).
set -eu
file="/var/backups/trading/trading_db-$(date -u +%Y%m%dT%H%MZ).sql.gz"
mysqldump --defaults-extra-file=/etc/trading/backup.cnf --single-transaction --no-tablespaces \
          --routines --triggers trading_db | gzip > "$file"
chmod 0600 "$file"
find /var/backups/trading -name 'trading_db-*.sql.gz' -mtime +14 -delete
EOF
chmod 0700 /usr/local/sbin/trading-backup
/usr/local/sbin/trading-backup && ls -l /var/backups/trading
```

Run it every night with a systemd timer:

```bash
cat > /etc/systemd/system/trading-backup.service <<'EOF'
[Unit]
Description=Dump the trading database
[Service]
Type=oneshot
ExecStart=/usr/local/sbin/trading-backup
EOF
cat > /etc/systemd/system/trading-backup.timer <<'EOF'
[Unit]
Description=Nightly trading database dump
[Timer]
OnCalendar=*-*-* 03:30:00
Persistent=true
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload && systemctl enable --now trading-backup.timer
systemctl list-timers trading-backup.timer --no-pager
```

**Off site:** copy `/var/backups/trading` to another machine every night after the dump. For example, from a machine that can reach the container: `rsync -a root@<container-address>:/var/backups/trading/ /backups/trading/`. Or use `rclone` to cloud storage. Where it goes is your choice; the rule is that it isn't on the same Proxmox host.

The dumps contain SSNs and bank account numbers **encrypted**, so they're useless without `FIELD_ENCRYPTION_KEYS`. Keep that key in your password manager, not next to the dumps.

---

## 14. Test a restore (do this once now, then every few months)

A backup you haven't restored isn't a backup yet. This restores the latest dump into a scratch database and compares it with the live one, without touching the live data:

```bash
latest=$(ls -t /var/backups/trading/trading_db-*.sql.gz | head -1)
mysql -e "DROP DATABASE IF EXISTS trading_restore_test; CREATE DATABASE trading_restore_test;"
gunzip -c "$latest" | mysql trading_restore_test
for t in User Portfolio Issuer Inventory Transaction Transfer History BankAccount StockPrice; do
  live=$(mysql -N -e "SELECT COUNT(*) FROM trading_db.\`$t\`")
  restored=$(mysql -N -e "SELECT COUNT(*) FROM trading_restore_test.\`$t\`")
  echo "$t: live $live, restored $restored"
done
mysql -e "DROP DATABASE trading_restore_test;"
```

The counts should match, or be slightly lower in the restored copy if people used the site since the dump. Note the date and result.

To restore for real, into the live database (this replaces everything since the dump):

```bash
systemctl stop trading-web trading-price-generator
gunzip -c /var/backups/trading/trading_db-<timestamp>.sql.gz | mysql trading_db
systemctl start trading-web trading-price-generator
```

The same `FIELD_ENCRYPTION_KEYS` must be in `/etc/trading/trading.env`, or encrypted fields can't be read.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `trading-flask: …/etc/trading/trading.env: 9:00: not found` | An older `trading-flask` read the settings with the shell, which chokes on values with spaces. Recreate it from step 7 (it uses `systemd-run`). |
| `PermissionError: … '/opt/trading/.env'` | A `.env` file from the development quickstart is in `/opt/trading`. Remove it (`mv /opt/trading/.env /root/`); production settings live only in `/etc/trading/trading.env`. |
| `ERROR 1045 (28000): Access denied for user '<you>'@'localhost' (using password: NO)` in step 5 | `mysql` ran as your normal user. Run `sudo -i` first, then the whole block again. |
| A service fails with `status=226/NAMESPACE` | systemd's sandboxing needs **Nesting** on the container (Proxmox: container → **Options** → **Features** → **Nesting**), then reboot the container. |
| `trading-web` exits with "The app can't start in production" or "Invalid configuration" | The message lists every problem: for example a weak `SECRET_KEY`, `FLASK_DEBUG` set, or a missing setting. Fix `/etc/trading/trading.env`, then `systemctl restart trading-web`. |
| `Control server error: [Errno 13] Permission denied: '/home/trading'` in the web log | Harmless, but fixed: the service now starts Gunicorn with `--no-control-socket`. `git pull`, copy the service files again (step 8), `systemctl daemon-reload`, then `systemctl restart trading-web`. |
| "Refusing to run Gunicorn as root" | The service must run as `User=trading`, as in `deploy/systemd/trading-web.service`. |
| `db-upgrade` fails with 1142 "GRANT command denied" | `trading_migrate` is missing `WITH GRANT OPTION`: rerun the `GRANT ALL PRIVILEGES … WITH GRANT OPTION` line from step 5. |
| The site shows Cloudflare error 1033 or 502 | `cloudflared` can't reach Gunicorn. Check `systemctl status trading-web` in the container, and run `curl http://<gunicorn-address>:8000/health` from wherever `cloudflared` runs. Layout B: check the Proxmox firewall rule allows the tunnel VM's address, and that `GUNICORN_BIND` is set. |
| "Prices may be delayed" during market hours | The price generator isn't ticking: `journalctl -u trading-price-generator`. "Another price generator is already running" means a second copy is running somewhere against the same database. |
| Every request line starts with `-` or the tunnel's address | The visitor address isn't reaching the app. Make sure nothing else sits between `cloudflared` and Gunicorn (one proxy hop is trusted). |
| Anyone on the LAN can open `http://10.1.0.14:8000` (layout B) | The Proxmox firewall isn't on for the container. Check all four switches in step 9B, including **Firewall** on `net0`. |
| The disk is filling up | Check `/var/backups/trading` (kept 14 days) and MySQL's binary logs (`binlog_expire_logs_seconds`, step 5). |
