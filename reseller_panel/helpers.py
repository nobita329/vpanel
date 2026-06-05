import os
import subprocess
import uuid
import smtplib
import re
import socket
import time
from email.mime.text import MIMEText
from functools import wraps
from flask import session, redirect, request, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from .database import get_db

VM_DIR = os.environ.get("VM_DIR", os.path.expanduser("~/vms"))
PIDS_DIR = os.path.join(VM_DIR, "pids")
SNAPSHOT_DIR = os.path.join(VM_DIR, "snapshots")
os.makedirs(VM_DIR, exist_ok=True)
os.makedirs(PIDS_DIR, exist_ok=True)
os.makedirs(SNAPSHOT_DIR, exist_ok=True)

CPU_MODELS = {
    "Host CPU (Passthrough)": "host",
    "QEMU Default (qemu64)": "qemu64",
    "AMD EPYC 7763": "EPYC,vendor=AuthenticAMD,model-id=AMD EPYC 7763 64-Core Processor",
    "AMD Ryzen 9 7950X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 9 7950X 16-Core Processor",
    "Intel Xeon Platinum 8380": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon Platinum 8380",
    "Intel Core i9-13900K": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i9-13900K",
}

OS_IMAGES = {
    "ubuntu": {"24.04": {"name": "Ubuntu 24.04 LTS", "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"},
               "22.04": {"name": "Ubuntu 22.04 LTS", "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img"}},
    "debian": {"10": {"name": "Debian 10 Buster", "url": "https://cloud.debian.org/images/cloud/buster/latest/debian-10-generic-amd64.qcow2"},
               "11": {"name": "Debian 11 Bullseye", "url": "https://cloud.debian.org/images/cloud/bullseye/latest/debian-11-generic-amd64.qcow2"},
               "12": {"name": "Debian 12 Bookworm", "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2"},
               "13": {"name": "Debian 13 Trixie", "url": "https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2"}},
    "centos": {"9": {"name": "CentOS Stream 9", "url": "https://cloud.centos.org/centos/9-stream/x86_64/images/CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2"}},
    "fedora": {"39": {"name": "Fedora 39", "url": "https://download.fedoraproject.org/pub/fedora/linux/releases/39/Cloud/x86_64/images/Fedora-Cloud-Base-39-1.5.x86_64.qcow2"},
               "40": {"name": "Fedora 40", "url": "https://download.fedoraproject.org/pub/fedora/linux/releases/40/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-40-1.14.x86_64.qcow2"}},
    "almalinux": {"9": {"name": "AlmaLinux 9", "url": "https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/AlmaLinux-9-GenericCloud-latest.x86_64.qcow2"}},
    "rockylinux": {"9": {"name": "Rocky Linux 9", "url": "https://download.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud.latest.x86_64.qcow2"}},
    "alpine": {"3.20": {"name": "Alpine 3.20", "url": "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/cloud/nocloud_alpine-3.20.0-x86_64-cloudimg.qcow2"}},
    "proxmox": {"8": {"name": "Proxmox VE 8", "url": "https://enterprise.proxmox.com/iso/proxmox-ve_8.4-1.iso"}},
    "windows": {"10-lite": {"name": "Windows 10 Lite", "url": "https://archive.org/download/windows-10-lite-edition-19h2-x64/Windows%2010%20Lite%20Edition%2019H2%20x64.iso"}},
}


def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect('/login')
        conn = get_db()
        user = conn.execute("SELECT otp_enabled FROM users WHERE id=?", (session['user_id'],)).fetchone()
        conn.close()
        if user and user['otp_enabled'] and not session.get('2fa_verified'):
            endpoint = request.endpoint or ''
            if endpoint not in ('login_2fa', 'logout', 'static'):
                return redirect('/login/2fa')
        return f(*args, **kwargs)
    return decorated


def role_required(*roles):
    def decorator(f):
        @wraps(f)
        def decorated(*args, **kwargs):
            if 'user_id' not in session:
                return redirect('/login')
            conn = get_db()
            user = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
            conn.close()
            if not user or user['role'] not in roles:
                return redirect('/dashboard')
            return f(*args, **kwargs)
        return decorated
    return decorator


def get_current_user():
    if 'user_id' not in session:
        return None
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    return dict(user) if user else None


def api_token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.headers.get('Authorization', '')
        if not auth.startswith('Bearer '):
            return jsonify({'success': False, 'error': 'Missing or invalid Authorization header'}), 401
        token = auth[7:]
        conn = get_db()
        row = conn.execute("SELECT * FROM api_tokens WHERE token=?", (token,)).fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': 'Invalid token'}), 401
        conn.execute("UPDATE api_tokens SET last_used_at=CURRENT_TIMESTAMP WHERE id=?", (row['id'],))
        conn.commit()
        conn.close()
        return f(*args, **kwargs)
    return decorated


def log_activity(user_id, action, details=''):
    from .database import get_db
    conn = get_db()
    conn.execute("INSERT INTO activity_log (user_id, action, details, ip_address) VALUES (?,?,?,?)",
                 (user_id, action, details, request.remote_addr or ''))
    conn.commit()
    conn.close()


def check_kvm():
    return os.path.exists('/dev/kvm') and os.access('/dev/kvm', os.R_OK | os.W_OK)


def is_vm_running(vm_uuid):
    result = subprocess.run(['pgrep', '-af', f'vpanel-{vm_uuid}'], capture_output=True, text=True, timeout=15)
    return f'vpanel-{vm_uuid}' in result.stdout


def get_available_port(start=5901, end=6000):
    for port in range(start, end):
        result = subprocess.run(['ss', '-tln'], capture_output=True, text=True, timeout=5)
        if f':{port} ' not in result.stdout:
            return port
    return 0


def hash_password(password):
    return generate_password_hash(password)


def check_password(password, pw_hash):
    return check_password_hash(pw_hash, password)


def generate_uuid():
    return str(uuid.uuid4())[:8]


def parse_disk_size(disk_str):
    m = re.match(r'(\d+)\s*(G|M|T|GB|MB|TB)?', str(disk_str).upper())
    if m:
        val = int(m.group(1))
        unit = m.group(2) or 'G'
        if unit in ('M', 'MB'):
            return max(1, val // 1024)
        elif unit in ('T', 'TB'):
            return val * 1024
        return val
    return 20


def get_settings():
    conn = get_db()
    rows = conn.execute("SELECT * FROM settings").fetchall()
    conn.close()
    return {row['key']: row['value'] for row in rows}


def send_email(to_email, subject, body):
    settings = get_settings()
    host = settings.get('smtp_host', '')
    if not host:
        return False
    port = int(settings.get('smtp_port', 587))
    user = settings.get('smtp_user', '')
    pw = settings.get('smtp_pass', '')
    from_addr = settings.get('smtp_from', 'noreply@vpanel.com')
    try:
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['Subject'] = subject
        msg['From'] = from_addr
        msg['To'] = to_email
        server = smtplib.SMTP(host, port, timeout=10)
        server.ehlo()
        if server.has_extn('STARTTLS'):
            server.starttls()
            server.ehlo()
        if user and pw:
            server.login(user, pw)
        server.sendmail(from_addr, [to_email], msg.as_string())
        server.quit()
        return True
    except Exception:
        return False


APP_INSTALLERS = {
    "wordpress": {
        "name": "WordPress", "description": "CMS / Blog", "icon": "wordpress",
        "script": "apt-get update && apt-get install -y nginx mariadb-server php php-mysql wget && wget -q -O /tmp/wp.tar.gz https://wordpress.org/latest.tar.gz && tar xzf /tmp/wp.tar.gz -C /var/www/html/ && chown -R www-data:www-data /var/www/html/wordpress"
    },
    "docker": {
        "name": "Docker", "description": "Container Runtime", "icon": "docker",
        "script": "curl -fsSL https://get.docker.com | sh && usermod -aG docker {username}"
    },
    "pterodactyl": {
        "name": "Pterodactyl", "description": "Game Panel", "icon": "gamepad",
        "script": "bash <(curl -s https://pterodactyl-installer.se) <<< $'0\\n{username}\\n{password}\\n{email}\\ny\\n'"
    },
    "nodejs": {
        "name": "Node.js", "description": "JavaScript Runtime", "icon": "node",
        "script": "curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs"
    },
    "nginx": {
        "name": "Nginx Proxy", "description": "Web Server / Proxy", "icon": "server",
        "script": "apt-get update && apt-get install -y nginx certbot python3-certbot-nginx"
    },
    "postgres": {
        "name": "PostgreSQL", "description": "Database Server", "icon": "database",
        "script": "apt-get update && apt-get install -y postgresql postgresql-contrib"
    },
    "redis": {
        "name": "Redis", "description": "Cache / Queue", "icon": "bolt",
        "script": "apt-get update && apt-get install -y redis-server"
    },
    "mailcow": {
        "name": "Mailcow", "description": "Email Server", "icon": "envelope",
        "script": "apt-get update && apt-get install -y git && cd /opt && git clone https://github.com/mailcow/mailcow-dockerized && cd mailcow-dockerized && ./generate_config.sh"
    },
}


def apply_firewall(vm_uuid):
    conn = get_db()
    vm = conn.execute("SELECT ssh_port, uuid FROM vms WHERE uuid=?", (vm_uuid,)).fetchone()
    rules = conn.execute("SELECT * FROM firewall_rules WHERE vm_uuid=? ORDER BY id", (vm_uuid,)).fetchall()
    conn.close()
    if not vm:
        return
    ssh_port = vm['ssh_port']
    comment = f"vpanel-{vm_uuid}"
    subprocess.run(['iptables', '-D', 'INPUT', '-m', 'comment', '--comment', comment, '-j', 'DROP'], capture_output=True, timeout=10)
    try:
        result = subprocess.run(['iptables-save'], capture_output=True, text=True, timeout=5)
        for line in result.stdout.split('\n'):
            if comment in line:
                rule_parts = line.strip()
                if rule_parts.startswith('-A'):
                    rule = rule_parts.replace('-A', '-D', 1)
                    subprocess.run(['iptables'] + rule.split(), capture_output=True, timeout=10)
    except Exception:
        pass
    for rule in rules:
        src = rule['source_ip'] or '0.0.0.0/0'
        proto = rule['protocol'] if rule['protocol'] != 'any' else 'tcp'
        if rule['rule_type'] == 'allow':
            subprocess.run(['iptables', '-A', 'INPUT', '-p', proto, '--dport', str(ssh_port),
                          '-s', src, '-j', 'ACCEPT', '-m', 'comment', '--comment', comment], capture_output=True, timeout=10)
        elif rule['rule_type'] == 'deny':
            subprocess.run(['iptables', '-A', 'INPUT', '-p', proto, '--dport', str(ssh_port),
                          '-s', src, '-j', 'DROP', '-m', 'comment', '--comment', comment], capture_output=True, timeout=10)


def backup_scheduler():
    while True:
        try:
            conn = get_db()
            due = conn.execute("""
                SELECT bs.*, v.ssh_port, v.username, v.password, v.uuid as vm_uuid
                FROM backup_schedules bs
                JOIN vms v ON bs.vm_uuid = v.uuid
                WHERE bs.enabled=1 AND bs.next_run <= datetime('now')
            """).fetchall()
            for schedule in due:
                s = dict(schedule)
                try:
                    create_snapshot(s['vm_uuid'], f"auto-{s['frequency']}-{generate_uuid()}")
                    import datetime
                    now = datetime.datetime.now()
                    if s['frequency'] == 'daily':
                        next_run = now + datetime.timedelta(days=1)
                    elif s['frequency'] == 'weekly':
                        next_run = now + datetime.timedelta(weeks=1)
                    elif s['frequency'] == 'monthly':
                        next_run = now + datetime.timedelta(days=30)
                    else:
                        next_run = now + datetime.timedelta(days=1)
                    next_run = next_run.replace(hour=s['hour'] or 2, minute=0, second=0, microsecond=0)
                    conn.execute("""UPDATE backup_schedules SET last_run=datetime('now'), next_run=? WHERE id=?""",
                                 (next_run.strftime('%Y-%m-%d %H:%M:%S'), s['id']))
                    snaps = conn.execute("SELECT id FROM snapshots WHERE vm_uuid=? ORDER BY created_at DESC",
                                         (s['vm_uuid'],)).fetchall()
                    if len(snaps) > (s['retention_count'] or 7):
                        for snap in snaps[s['retention_count'] or 7:]:
                            conn.execute("DELETE FROM snapshots WHERE id=?", (snap['id'],))
                    conn.commit()
                except Exception as e:
                    print(f"Backup error for {s['vm_uuid']}: {e}")
            conn.close()
        except Exception:
            pass
        time.sleep(60)


def get_theme():
    if 'user_id' in session:
        conn = get_db()
        user = conn.execute("SELECT theme FROM users WHERE id=?", (session['user_id'],)).fetchone()
        conn.close()
        if user and user['theme']:
            return user['theme']
    return get_settings().get('default_theme', 'dark')


def perform_monitor_check(check):
    import socket
    import time
    check_type = check['check_type']
    vm_uuid = check['vm_uuid']
    port = check.get('port')
    timeout = check.get('timeout_sec', 10)
    conn = get_db()
    vm = conn.execute("SELECT ssh_port FROM vms WHERE uuid=?", (vm_uuid,)).fetchone()
    conn.close()
    target_port = port or (vm['ssh_port'] if vm else 22)
    start = time.time()
    error = None
    status = 'up'
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        result = sock.connect_ex(('127.0.0.1', target_port))
        sock.close()
        if result != 0:
            status = 'down'
            error = f'Connection refused on port {target_port}'
    except Exception as e:
        status = 'down'
        error = str(e)
    response_time = int((time.time() - start) * 1000)
    return status, response_time, error


def monitor_loop():
    while True:
        try:
            conn = get_db()
            checks = conn.execute("""
                SELECT mc.* FROM monitoring_checks mc
                JOIN vms v ON mc.vm_uuid = v.uuid
                WHERE mc.enabled = 1
                AND (mc.last_checked IS NULL OR (strftime('%s','now') - strftime('%s',mc.last_checked)) >= mc.interval_sec)
            """).fetchall()
            for check in checks:
                c = dict(check)
                status, rt, err = perform_monitor_check(c)
                conn.execute("INSERT INTO monitoring_logs (check_id, status, response_time_ms, error) VALUES (?,?,?,?)",
                             (c['id'], status, rt, err))
                old_status = c.get('last_status')
                conn.execute("UPDATE monitoring_checks SET last_status=?, last_checked=CURRENT_TIMESTAMP WHERE id=?",
                             (status, c['id']))
                if old_status and old_status != status and status == 'down':
                    from .app import log_activity
                    v = conn.execute("SELECT user_id FROM vms WHERE uuid=?", (c['vm_uuid'],)).fetchone()
                    if v:
                        log_activity(v['user_id'], f'Monitoring alert: VM {c["vm_uuid"]} went DOWN ({err})')
                conn.commit()
            conn.close()
        except Exception:
            pass
        time.sleep(30)


def slugify(text):
    import re
    text = text.lower().strip()
    text = re.sub(r'[^\w\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    return text


def create_monitor_check(vm_uuid, check_type='ping', port=None, interval_sec=300, timeout_sec=10):
    conn = get_db()
    existing = conn.execute("SELECT id FROM monitoring_checks WHERE vm_uuid=?", (vm_uuid,)).fetchone()
    if not existing:
        conn.execute("""INSERT INTO monitoring_checks (vm_uuid, check_type, port, interval_sec, timeout_sec)
                        VALUES (?,?,?,?,?)""", (vm_uuid, check_type, port, interval_sec, timeout_sec))
        conn.commit()
    conn.close()


def create_snapshot(vm_uuid, name):
    vm_dir = os.path.join(VM_DIR, vm_uuid)
    os.makedirs(vm_dir, exist_ok=True)
    snap_file = os.path.join(SNAPSHOT_DIR, f"{vm_uuid}-{generate_uuid()}.qcow2")
    img_file = os.path.join(VM_DIR, f"{vm_uuid}.qcow2")
    if os.path.exists(img_file):
        safe_name = re.sub(r'[^a-zA-Z0-9_\- ]', '', name)[:128]
        result = subprocess.run(['qemu-img', 'snapshot', '-c', safe_name, img_file],
                               capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            subprocess.run(['cp', img_file, snap_file], check=True, timeout=60)
            return snap_file
    return None
