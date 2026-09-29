"""Gunicorn settings for production (task 6.1; design: Deployment; N3.1, N3.6; D28).

The site runs in a Proxmox LXC behind a Cloudflare Tunnel: cloudflared, in the same container, connects
out to Cloudflare and forwards requests to Gunicorn on 127.0.0.1. Gunicorn listens on the loopback
address only, so nothing in the container is reachable from the network directly.

Started by deploy/systemd/trading-web.service as:
    gunicorn -c deploy/gunicorn.conf.py "app:create_app()"
"""
import multiprocessing
import os

bind = os.environ.get("GUNICORN_BIND", "127.0.0.1:8000")   # loopback only: cloudflared is the one way in
workers = int(os.environ.get("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))
worker_class = "sync"
timeout = 30                 # a request taking longer than this is stuck: the worker is restarted
graceful_timeout = 30
max_requests = 1000          # recycle workers now and then, so a slow leak can't grow forever
max_requests_jitter = 100
proc_name = "trading-web"

# Gunicorn's control socket (for its `gunicornc` tool) is turned off with --no-control-socket on the command line
# in deploy/systemd/trading-web.service: this file can't do it (Gunicorn 26 ignores it here, found on the first
# deploy). It would otherwise try to create the socket in the trading user's home, which doesn't exist.

# Logs go to stderr, where systemd's journal keeps them. The access log records the method, path, status,
# size, and time, never form bodies (passwords, SSNs, and account numbers are only ever POSTed).
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%({x-forwarded-for}i)s "%(m)s %(U)s" %(s)s %(b)s %(M)sms'


def on_starting(server):
    """Refuse to run as root (N3.6): systemd starts Gunicorn as the dedicated 'trading' user."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise RuntimeError("Refusing to run Gunicorn as root: start it as the dedicated 'trading' user.")


# No "Server: gunicorn/<version>" header: say nothing about the software or its version.
try:
    import gunicorn

    gunicorn.SERVER = "webserver"
except ImportError:  # Gunicorn isn't installed on Windows development machines
    pass
