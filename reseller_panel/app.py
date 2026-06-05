import os
import json
import re
import time
import secrets
import subprocess
import shutil
import threading
import mimetypes
import datetime
from pathlib import Path

from flask import Flask, render_template_string, request, jsonify, redirect, session, flash, Response, send_file
from jinja2 import Environment, FileSystemLoader
from flask_sock import Sock
import pyotp
import qrcode
import qrcode.image.svg

from .database import init_db, get_db
from .helpers import (
    login_required, role_required, api_token_required, get_current_user, log_activity,
    check_kvm, is_vm_running, get_available_port, hash_password, check_password,
    generate_uuid, parse_disk_size, get_settings, send_email,
    create_snapshot, apply_firewall, backup_scheduler, get_theme,
    perform_monitor_check, monitor_loop, create_monitor_check, slugify,
    APP_INSTALLERS, CPU_MODELS, OS_IMAGES, VM_DIR, SNAPSHOT_DIR
)

app = Flask(__name__)
app.secret_key = os.urandom(32).hex()
app.jinja_loader = FileSystemLoader(Path(__file__).parent / 'templates')
app.jinja_env.globals.update(max=max, min=min, get_theme=get_theme)

sock = Sock(app)

ISO_DIR = os.path.expanduser("~/isos")
os.makedirs(ISO_DIR, exist_ok=True)
MAX_ISO_SIZE = 10 * 1024 * 1024 * 1024  # 10 GB

init_db()


@app.template_global()
def settings_get(key, default=''):
    s = get_settings()
    return s.get(key, default)


# ========== AUTH ==========

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'user_id' in session:
        return redirect('/dashboard')
    error = ''
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        ip = request.remote_addr or ''
        ua = request.headers.get('User-Agent', '')
        conn = get_db()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if user and check_password(password, user['password']):
            u = dict(user)
            conn.execute("INSERT INTO login_history (user_id,username,ip_address,user_agent,status) VALUES (?,?,?,?,'success')",
                         (u['id'], username, ip, ua))
            conn.commit()
            conn.close()
            session['user_id'] = u['id']
            session['username'] = u['username']
            session['role'] = u['role']
            if u.get('otp_enabled'):
                return redirect('/login/2fa')
            log_activity(user['id'], 'logged in')
            return redirect('/dashboard')
        else:
            conn.execute("INSERT INTO login_history (user_id,username,ip_address,user_agent,status) VALUES (NULL,?,?,?,'failed')",
                         (username, ip, ua))
            conn.commit()
            conn.close()
            error = 'Invalid username or password'
    return render_template_string(open(Path(__file__).parent / 'templates' / 'login.html').read(),
                                  title='Sign In', error=error, current_user=None)


@app.route('/logout')
def logout():
    if 'user_id' in session:
        log_activity(session['user_id'], 'logged out')
    session.clear()
    return redirect('/login')


@app.route('/')
@login_required
def index():
    return redirect('/dashboard')


# ========== DASHBOARD ==========

@app.route('/dashboard')
@login_required
def dashboard():
    user = get_current_user()
    conn = get_db()

    if user['role'] == 'admin':
        total_vms = conn.execute("SELECT COUNT(*) as c FROM vms").fetchone()['c']
        running_vms = conn.execute("SELECT COUNT(*) as c FROM vms WHERE status='running'").fetchone()['c']
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()['c']
        resellers = conn.execute("SELECT COUNT(*) as c FROM users WHERE role='reseller'").fetchone()['c']
        clients = conn.execute("SELECT COUNT(*) as c FROM users WHERE role='client'").fetchone()['c']
        revenue = conn.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid'").fetchone()[0]
        pending_inv = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='pending'").fetchone()[0]
        paid_inv = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='paid'").fetchone()[0]
        cpus_total = conn.execute("SELECT COALESCE(SUM(max_cpus),0) FROM users").fetchone()[0]
        cpus_used = conn.execute("SELECT COALESCE(SUM(cpus),0) FROM vms").fetchone()[0]
        ram_total = conn.execute("SELECT COALESCE(SUM(max_ram),0) FROM users").fetchone()[0]
        ram_used = conn.execute("SELECT COALESCE(SUM(ram),0) FROM vms").fetchone()[0]
        disk_total = conn.execute("SELECT COALESCE(SUM(max_disk),0) FROM users").fetchone()[0]
        disk_used = conn.execute("SELECT COALESCE(SUM(disk_gb),0) FROM vms").fetchone()[0]
        rows = conn.execute("SELECT v.*,u.username as owner FROM vms v JOIN users u ON v.user_id=u.id ORDER BY v.created_at DESC LIMIT 5").fetchall()
        acts = conn.execute("SELECT a.*,u.username FROM activity_log a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.created_at DESC LIMIT 10").fetchall()
    elif user['role'] == 'reseller':
        total_vms = conn.execute("SELECT COUNT(*) FROM vms WHERE user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        running_vms = conn.execute("SELECT COUNT(*) FROM vms WHERE status='running' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        total_users = conn.execute("SELECT COUNT(*) FROM users WHERE parent_id=?", (user['id'],)).fetchone()[0]
        resellers = 0
        clients = total_users
        revenue = conn.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        pending_inv = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='pending' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        paid_inv = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='paid' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        cpus_total = user['max_cpus']
        cpus_used = conn.execute("SELECT COALESCE(SUM(cpus),0) FROM vms WHERE user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        ram_total = user['max_ram']
        ram_used = conn.execute("SELECT COALESCE(SUM(ram),0) FROM vms WHERE user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        disk_total = user['max_disk']
        disk_used = conn.execute("SELECT COALESCE(SUM(disk_gb),0) FROM vms WHERE user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        rows = conn.execute("SELECT v.*,u.username as owner FROM vms v JOIN users u ON v.user_id=u.id WHERE v.user_id IN (SELECT id FROM users WHERE parent_id=?) ORDER BY v.created_at DESC LIMIT 5", (user['id'],)).fetchall()
        acts = conn.execute("SELECT * FROM activity_log WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (user['id'],)).fetchall()
    else:
        total_vms = conn.execute("SELECT COUNT(*) FROM vms WHERE user_id=?", (user['id'],)).fetchone()[0]
        running_vms = conn.execute("SELECT COUNT(*) FROM vms WHERE status='running' AND user_id=?", (user['id'],)).fetchone()[0]
        total_users = resellers = clients = 0
        revenue = pending_inv = 0
        paid_inv = conn.execute("SELECT COUNT(*) FROM invoices WHERE status='paid' AND user_id=?", (user['id'],)).fetchone()[0]
        cpus_total = user['max_cpus']
        cpus_used = conn.execute("SELECT COALESCE(SUM(cpus),0) FROM vms WHERE user_id=?", (user['id'],)).fetchone()[0]
        ram_total = user['max_ram']
        ram_used = conn.execute("SELECT COALESCE(SUM(ram),0) FROM vms WHERE user_id=?", (user['id'],)).fetchone()[0]
        disk_total = user['max_disk']
        disk_used = conn.execute("SELECT COALESCE(SUM(disk_gb),0) FROM vms WHERE user_id=?", (user['id'],)).fetchone()[0]
        rows = conn.execute("SELECT * FROM vms WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (user['id'],)).fetchall()
        acts = conn.execute("SELECT * FROM activity_log WHERE user_id=? ORDER BY created_at DESC LIMIT 10", (user['id'],)).fetchall()

    stopped_vms = total_vms - running_vms
    conn.close()

    recent_vms = []
    for row in rows:
        d = dict(row)
        d['running'] = is_vm_running(d.get('uuid', ''))
        d['status'] = 'running' if d['running'] else 'stopped'
        recent_vms.append(d)

    cpu_pct = min(int(cpus_used / max(cpus_total, 1) * 100), 100)
    ram_pct = min(int(ram_used / max(ram_total, 1) * 100), 100)
    disk_pct = min(int(disk_used / max(disk_total, 1) * 100), 100)

    template = open(Path(__file__).parent / 'templates' / 'dashboard.html').read()
    return render_template_string(template,
        title='Dashboard', active_page='dashboard', current_user=user,
        stats={
            'total_vms': total_vms, 'running_vms': running_vms, 'stopped_vms': stopped_vms,
            'total_users': total_users, 'resellers': resellers, 'clients': clients,
            'revenue': revenue, 'pending_invoices': pending_inv, 'paid_invoices': paid_inv,
            'cpus_used': cpus_used, 'cpus_total': cpus_total, 'cpu_percent': cpu_pct,
            'ram_used': ram_used, 'ram_total': ram_total, 'ram_percent': ram_pct,
            'disk_used': disk_used, 'disk_total': disk_total, 'disk_percent': disk_pct,
        },
        recent_vms=recent_vms, recent_activity=[dict(a) for a in acts])


# ========== PROFILE ==========

@app.route('/profile', methods=['GET', 'POST'])
@login_required
def profile():
    user = get_current_user()
    conn = get_db()
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'password':
            cur = request.form.get('current_password', '')
            new = request.form.get('new_password', '')
            if not check_password(cur, user['password']):
                flash('Current password is incorrect', 'error')
            elif len(new) < 6:
                flash('New password too short', 'error')
            else:
                conn.execute("UPDATE users SET password=? WHERE id=?", (hash_password(new), user['id']))
                conn.commit()
                log_activity(user['id'], 'changed password')
                flash('Password updated', 'success')
        elif action == 'api_key':
            new_key = generate_uuid() + secrets.token_hex(16)
            conn.execute("UPDATE users SET api_key=? WHERE id=?", (new_key, user['id']))
            conn.commit()
            log_activity(user['id'], 'regenerated API key')
            flash('API key regenerated', 'success')
            user = get_current_user()
        elif action == 'enable_2fa':
            secret = pyotp.random_base32()
            conn.execute("UPDATE users SET otp_secret=? WHERE id=?", (secret, user['id']))
            conn.commit()
            session.pop('2fa_verified', None)
            user = get_current_user()
            user['otp_secret'] = secret
            flash('2FA setup started. Scan the QR code and verify.', 'success')
        elif action == 'verify_2fa':
            code = request.form.get('code', '').strip()
            secret = request.form.get('secret', user.get('otp_secret', ''))
            totp = pyotp.TOTP(secret)
            if totp.verify(code):
                conn.execute("UPDATE users SET otp_secret=?, otp_enabled=1 WHERE id=?", (secret, user['id']))
                conn.commit()
                log_activity(user['id'], 'enabled 2FA')
                flash('2FA enabled successfully', 'success')
                user = get_current_user()
            else:
                flash('Invalid code. Try again.', 'error')
        elif action == 'disable_2fa':
            conn.execute("UPDATE users SET otp_secret=NULL, otp_enabled=0 WHERE id=?", (user['id'],))
            conn.commit()
            session.pop('2fa_verified', None)
            log_activity(user['id'], 'disabled 2FA')
            flash('2FA disabled', 'success')
            user = get_current_user()
    conn.close()
    totp_uri = ''
    qr_svg = ''
    if user.get('otp_secret') and not user.get('otp_enabled'):
        totp_uri = pyotp.TOTP(user['otp_secret']).provisioning_uri(name=user['username'], issuer_name=get_settings().get('site_name', 'vPanel'))
        img = qrcode.make(totp_uri, image_factory=qrcode.image.svg.SvgImage)
        import io
        buf = io.BytesIO()
        img.save(buf)
        qr_svg = buf.getvalue().decode()
    user_2fa = {'enabled': user.get('otp_enabled', 0) == 1, 'secret': user.get('otp_secret', ''), 'qr_svg': qr_svg, 'totp_uri': totp_uri}
    template = open(Path(__file__).parent / 'templates' / 'profile.html').read()
    return render_template_string(template, title='Profile', active_page='profile', current_user=user, user_2fa=user_2fa)


# ========== RESELLERS ==========

@app.route('/resellers')
@login_required
@role_required('admin')
def resellers():
    user = get_current_user()
    conn = get_db()
    rows = conn.execute("""
        SELECT u.*,
            (SELECT COUNT(*) FROM users WHERE parent_id=u.id) as client_count,
            (SELECT COUNT(*) FROM vms WHERE user_id IN (SELECT id FROM users WHERE parent_id=u.id)) as vm_count,
            (SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid' AND user_id IN (SELECT id FROM users WHERE parent_id=u.id)) as revenue
        FROM users u WHERE u.role='reseller' ORDER BY u.username
    """).fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'resellers.html').read()
    return render_template_string(template, title='Resellers', active_page='resellers', current_user=user, resellers=[dict(r) for r in rows])


# ========== VMS ==========

@app.route('/vms')
@login_required
@role_required('admin', 'reseller')
def vms_list():
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        rows = conn.execute("SELECT v.*,u.username as owner, n.name as node_name FROM vms v JOIN users u ON v.user_id=u.id LEFT JOIN nodes n ON v.node_id=n.id ORDER BY v.created_at DESC").fetchall()
        all_users = conn.execute("SELECT id,username FROM users ORDER BY username").fetchall()
    else:
        rows = conn.execute("SELECT v.*,u.username as owner, n.name as node_name FROM vms v JOIN users u ON v.user_id=u.id LEFT JOIN nodes n ON v.node_id=n.id WHERE v.user_id IN (SELECT id FROM users WHERE parent_id=?) ORDER BY v.created_at DESC", (user['id'],)).fetchall()
        all_users = conn.execute("SELECT id,username FROM users WHERE parent_id=?", (user['id'],)).fetchall()
    conn.close()
    vms = []
    for row in rows:
        d = dict(row)
        d['running'] = is_vm_running(d.get('uuid', ''))
        d['status'] = 'running' if d['running'] else 'stopped'
        vms.append(d)
    template = open(Path(__file__).parent / 'templates' / 'vms.html').read()
    return render_template_string(template, title='VMs', active_page='vms', current_user=user, vms=vms, all_users=[dict(u) for u in all_users])


@app.route('/vms/create')
@login_required
@role_required('admin', 'reseller')
def create_vm_page():
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        users = conn.execute("SELECT id,username,role FROM users ORDER BY username").fetchall()
        nodes = conn.execute("SELECT id,name FROM nodes WHERE status='active'").fetchall()
    else:
        users = conn.execute("SELECT id,username,role FROM users WHERE parent_id=?", (user['id'],)).fetchall()
        nodes = []
    plans = conn.execute("SELECT * FROM vm_plans WHERE status='active'").fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'create_vm.html').read()
    return render_template_string(template, title='Create VM', active_page='vms', current_user=user,
                                  users=[dict(u) for u in users], plans=[dict(p) for p in plans],
                                  nodes=[dict(n) for n in nodes],
                                  cpu_models=list(CPU_MODELS.keys()), os_images=OS_IMAGES,
                                  os_images_json=json.dumps(OS_IMAGES))


@app.route('/vms/<uuid>')
@login_required
def vm_detail(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT v.*,u.username as owner, n.name as node_name FROM vms v JOIN users u ON v.user_id=u.id LEFT JOIN nodes n ON v.node_id=n.id WHERE v.uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT v.*,u.username as owner, n.name as node_name FROM vms v JOIN users u ON v.user_id=u.id LEFT JOIN nodes n ON v.node_id=n.id WHERE v.uuid=? AND v.user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT v.*,u.username as owner, n.name as node_name FROM vms v LEFT JOIN nodes n ON v.node_id=n.id WHERE v.uuid=? AND v.user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return "VM not found", 404
    vm = dict(row)
    vm['running'] = is_vm_running(uuid)
    vm['status'] = 'running' if vm['running'] else 'stopped'
    custom_isos = conn.execute("SELECT * FROM custom_isos ORDER BY created_at DESC").fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'vm_detail.html').read()
    return render_template_string(template, title=vm['name'], active_page='vms' if user['role'] in ['admin','reseller'] else 'my_vms', current_user=user, vm=vm,
                                  os_images=OS_IMAGES, os_images_json=json.dumps(OS_IMAGES),
                                  custom_isos=[dict(iso) for iso in custom_isos])


@app.route('/my-vms')
@login_required
@role_required('client')
def my_vms():
    user = get_current_user()
    conn = get_db()
    rows = conn.execute("SELECT * FROM vms WHERE user_id=? ORDER BY created_at DESC", (user['id'],)).fetchall()
    conn.close()
    vms = []
    for row in rows:
        d = dict(row)
        d['running'] = is_vm_running(d.get('uuid', ''))
        d['status'] = 'running' if d['running'] else 'stopped'
        vms.append(d)
    template = open(Path(__file__).parent / 'templates' / 'vms.html').read()
    return render_template_string(template, title='My VMs', active_page='my_vms', current_user=user, vms=vms, all_users=[])


# ========== VPS MANAGEMENT CONSOLE ==========

@app.route('/manage/<uuid>')
@login_required
def vps_management(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT v.*,u.username as owner, n.name as node_name FROM vms v JOIN users u ON v.user_id=u.id LEFT JOIN nodes n ON v.node_id=n.id WHERE v.uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT v.*,u.username as owner, n.name as node_name FROM vms v JOIN users u ON v.user_id=u.id LEFT JOIN nodes n ON v.node_id=n.id WHERE v.uuid=? AND v.user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT v.*,u.username as owner, n.name as node_name FROM vms v LEFT JOIN nodes n ON v.node_id=n.id WHERE v.uuid=? AND v.user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return "VM not found", 404
    vm = dict(row)
    vm['running'] = is_vm_running(uuid)
    vm['status'] = 'running' if vm['running'] else 'stopped'
    nodes = conn.execute("SELECT * FROM nodes ORDER BY name").fetchall()
    users_list = conn.execute("SELECT id, username FROM users WHERE role='client' ORDER BY username").fetchall() if user['role'] == 'admin' else []
    ssh_keys = conn.execute("SELECT * FROM ssh_keys WHERE user_id=?", (user['id'],)).fetchall()
    templates = conn.execute("SELECT * FROM templates WHERE user_id=? OR user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'], user['id'])).fetchall()
    custom_isos = conn.execute("SELECT * FROM custom_isos ORDER BY created_at DESC").fetchall()
    notifications = conn.execute("SELECT * FROM notifications WHERE user_id=?", (user['id'],)).fetchall()
    scripts = conn.execute("SELECT * FROM startup_scripts WHERE user_id=?", (user['id'],)).fetchall()
    alerts = conn.execute("SELECT * FROM resource_alerts WHERE vm_uuid=?", (uuid,)).fetchall()
    storage = conn.execute("SELECT * FROM additional_storage WHERE vm_uuid=?", (uuid,)).fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'vps_management.html').read()
    return render_template_string(template, title=f'Manage {vm["name"]}', active_page='vms', current_user=user, vm=vm,
                                  os_images=OS_IMAGES, os_images_json=json.dumps(OS_IMAGES),
                                  nodes=[dict(n) for n in nodes],
                                  users_list=[dict(u) for u in users_list],
                                  ssh_keys=[dict(k) for k in ssh_keys],
                                  templates=[dict(t) for t in templates],
                                  custom_isos=[dict(iso) for iso in custom_isos],
                                  notifications=[dict(n) for n in notifications],
                                  startup_scripts=[dict(s) for s in scripts],
                                  resource_alerts=[dict(a) for a in alerts],
                                  additional_storage=[dict(s) for s in storage])


@app.route('/vnc/<uuid>')
@login_required
def vnc_viewer(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    conn.close()
    if not row:
        return "VM not found", 404
    vm = dict(row)
    vm['running'] = is_vm_running(uuid)
    vm['status'] = 'running' if vm['running'] else 'stopped'
    host = request.host.split(':')[0]
    ws_port = vm.get('ws_port') or 0
    template = open(Path(__file__).parent / 'templates' / 'vnc.html').read()
    return render_template_string(template, title=f'Console - {vm["name"]}', current_user=user, vm=vm, host=host)


@app.route('/terminal/<uuid>')
@login_required
def terminal_view(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    conn.close()
    if not row:
        return "VM not found", 404
    vm = dict(row)
    vm['running'] = is_vm_running(uuid)
    vm['status'] = 'running' if vm['running'] else 'stopped'
    template = open(Path(__file__).parent / 'templates' / 'terminal.html').read()
    return render_template_string(template, title=f'Terminal - {vm["name"]}', current_user=user, vm=vm)


@sock.route('/ssh/<uuid>')
def ssh_websocket(ws, uuid):
    user = get_current_user()
    if not user:
        ws.close()
        return
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    conn.close()
    if not row:
        ws.close()
        return
    vm = dict(row)
    ssh_port = vm['ssh_port'] or 22
    username = vm['username'] or 'root'
    password = vm['password'] or ''
    import pty
    master_fd, slave_fd = pty.openpty()
    proc = subprocess.Popen([
        'sshpass', '-p', password,
        'ssh', '-o', 'StrictHostKeyChecking=no',
        '-o', 'UserKnownHostsFile=/dev/null',
        '-p', str(ssh_port),
        f'{username}@localhost',
        '-t', 'bash --login'
    ], stdin=slave_fd, stdout=slave_fd, stderr=slave_fd, close_fds=True)
    os.close(slave_fd)
    import select
    running = True
    def reader():
        nonlocal running
        while running:
            try:
                r, _, _ = select.select([master_fd], [], [], 0.1)
                if r:
                    data = os.read(master_fd, 4096)
                    if not data:
                        break
                    ws.send(data)
            except (OSError, ValueError):
                break
            except Exception:
                break
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        while running:
            try:
                message = ws.receive()
                if message is None:
                    break
                if isinstance(message, bytes):
                    os.write(master_fd, message)
                elif isinstance(message, str):
                    try:
                        msg = json.loads(message)
                        if 'resize' in msg:
                            import fcntl
                            import struct
                            import termios
                            cols, rows = msg['resize']
                            fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                                        struct.pack('HHHH', rows, cols, 0, 0))
                            continue
                    except (json.JSONDecodeError, ValueError):
                        pass
                    os.write(master_fd, message.encode())
            except Exception:
                break
    finally:
        running = False
        try:
            os.close(master_fd)
        except OSError:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


@app.route('/api/vm/<uuid>/upgrade', methods=['POST'])
@login_required
def api_upgrade_vm(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    running = is_vm_running(uuid)
    new_cpus = int(request.form.get('cpus', vm['cpus']))
    new_ram = int(request.form.get('ram', vm['ram']))
    new_disk = request.form.get('disk_size', '')
    try:
        conn.execute("UPDATE vms SET cpus=?, ram=? WHERE uuid=?", (new_cpus, new_ram, uuid))
        if new_disk:
            disk_gb = parse_disk_size(new_disk)
            img_file = vm['img_file']
            if img_file and os.path.exists(img_file):
                subprocess.run(['qemu-img', 'resize', img_file, new_disk], check=True, timeout=60)
            conn.execute("UPDATE vms SET disk_size=?, disk_gb=? WHERE uuid=?", (new_disk, disk_gb, uuid))
        conn.commit()
        log_activity(user['id'], f'upgraded VM: {vm["name"]} (cpus={new_cpus}, ram={new_ram})')
        conn.close()
        msg = 'VM upgraded'
        if running:
            msg += ' (restart to apply CPU/RAM changes)'
        return jsonify({'success': True, 'message': msg})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/vm/<uuid>/firewall', methods=['GET', 'POST'])
@login_required
def api_firewall(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT id FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT id FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT id FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    if request.method == 'GET':
        rules = conn.execute("SELECT * FROM firewall_rules WHERE vm_uuid=? ORDER BY created_at DESC", (uuid,)).fetchall()
        conn.close()
        return jsonify({'success': True, 'rules': [dict(r) for r in rules]})
    rule_type = request.form.get('rule_type', 'allow')
    protocol = request.form.get('protocol', 'tcp')
    port_start = request.form.get('port_start', type=int)
    port_end = request.form.get('port_end', type=int)
    source_ip = request.form.get('source_ip', '0.0.0.0/0')
    description = request.form.get('description', '')
    conn.execute("""INSERT INTO firewall_rules (vm_uuid, rule_type, protocol, port_start, port_end, source_ip, description)
                    VALUES (?,?,?,?,?,?,?)""",
                 (uuid, rule_type, protocol, port_start, port_end, source_ip, description))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'added firewall rule for VM {uuid}')
    try:
        threading.Thread(target=apply_firewall, args=(uuid,), daemon=True).start()
    except Exception:
        pass
    return jsonify({'success': True, 'message': 'Rule added'})


@app.route('/api/vm/<uuid>/firewall/<int:rule_id>/delete', methods=['POST'])
@login_required
@role_required('admin', 'reseller')
def api_delete_firewall_rule(uuid, rule_id):
    conn = get_db()
    conn.execute("DELETE FROM firewall_rules WHERE id=? AND vm_uuid=?", (rule_id, uuid))
    conn.commit()
    conn.close()
    try:
        threading.Thread(target=apply_firewall, args=(uuid,), daemon=True).start()
    except Exception:
        pass
    return jsonify({'success': True, 'message': 'Rule deleted'})


@app.route('/api/vm/<uuid>/backup/now', methods=['POST'])
@login_required
def api_backup_now(uuid):
    user = get_current_user()
    name = f"manual-{generate_uuid()}"
    conn = get_db()
    vm = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    if user['role'] == 'reseller' and vm['user_id'] not in [r['id'] for r in conn.execute("SELECT id FROM users WHERE parent_id=?", (user['id'],)).fetchall()]:
        conn.close()
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    if user['role'] == 'client' and vm['user_id'] != user['id']:
        conn.close()
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    conn.close()
    log_activity(user['id'], f'manual backup for VM {uuid}')
    snap_file = create_snapshot(uuid, name)
    size = os.path.getsize(snap_file) if snap_file and os.path.exists(snap_file) else 0
    conn = get_db()
    conn.execute("INSERT INTO snapshots (vm_uuid, name, size) VALUES (?,?,?)", (uuid, name, size))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Backup started'})


@app.route('/api/vm/<uuid>/backup/schedule', methods=['GET', 'POST'])
@login_required
def api_backup_schedule(uuid):
    user = get_current_user()
    conn = get_db()
    vm = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    if user['role'] == 'reseller' and vm['user_id'] not in [r['id'] for r in conn.execute("SELECT id FROM users WHERE parent_id=?", (user['id'],)).fetchall()]:
        conn.close()
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    if user['role'] == 'client' and vm['user_id'] != user['id']:
        conn.close()
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    if request.method == 'GET':
        sched = conn.execute("SELECT * FROM backup_schedules WHERE vm_uuid=?", (uuid,)).fetchone()
        conn.close()
        return jsonify({'success': True, 'schedule': dict(sched) if sched else None})
    enabled = int(request.form.get('enabled', 1))
    frequency = request.form.get('frequency', 'daily')
    hour = int(request.form.get('hour', 2))
    day_of_week = int(request.form.get('day_of_week', 0))
    retention_count = int(request.form.get('retention_count', 7))
    now = datetime.datetime.now()
    if frequency == 'daily':
        next_run = now + datetime.timedelta(days=1)
    elif frequency == 'weekly':
        next_run = now + datetime.timedelta(weeks=1)
    elif frequency == 'monthly':
        next_run = now + datetime.timedelta(days=30)
    else:
        next_run = now + datetime.timedelta(days=1)
    next_run = next_run.replace(hour=hour, minute=0, second=0, microsecond=0)
    existing = conn.execute("SELECT id FROM backup_schedules WHERE vm_uuid=?", (uuid,)).fetchone()
    if existing:
        conn.execute("""UPDATE backup_schedules SET enabled=?, frequency=?, hour=?, day_of_week=?, retention_count=?, next_run=? WHERE vm_uuid=?""",
                     (enabled, frequency, hour, day_of_week, retention_count, next_run.strftime('%Y-%m-%d %H:%M:%S'), uuid))
    else:
        conn.execute("""INSERT INTO backup_schedules (vm_uuid, enabled, frequency, hour, day_of_week, retention_count, next_run) VALUES (?,?,?,?,?,?,?)""",
                     (uuid, enabled, frequency, hour, day_of_week, retention_count, next_run.strftime('%Y-%m-%d %H:%M:%S')))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'updated backup schedule for VM {uuid}')
    return jsonify({'success': True, 'message': 'Schedule saved'})


@app.route('/api/vm/<uuid>/reinstall', methods=['POST'])
@login_required
def api_reinstall_vm(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    if is_vm_running(uuid):
        conn.close()
        return jsonify({'success': False, 'error': 'VM must be stopped to reinstall'})
    os_type = request.form.get('os_type', vm['os_type'])
    os_version = request.form.get('os_version', vm['os_version'])
    password = request.form.get('password', vm['password'])
    hostname = request.form.get('hostname', vm['hostname'])
    username = request.form.get('username', vm['username'])
    img_file = vm['img_file']
    seed_file = vm['seed_file']
    if os.path.exists(img_file):
        os.remove(img_file)
    conn.execute("""UPDATE vms SET status='stopped', os_type=?, os_version=?, password=?, hostname=?, username=?, notes='Reinstalling...' WHERE uuid=?""",
                 (os_type, os_version, password, hostname, username, uuid))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'reinstalling VM: {vm["name"]}')
    img_url = OS_IMAGES.get(os_type, {}).get(os_version, {}).get('url', '')
    threading.Thread(target=background_create_vm,
                     args=(uuid, img_url, img_file, seed_file, vm['disk_size'],
                           username, password, hostname, vm['name']),
                     daemon=True).start()
    return jsonify({'success': True, 'message': 'Reinstalling OS...'})


@app.route('/api/vm/<uuid>/iso/mount', methods=['POST'])
@login_required
def api_mount_iso(uuid):
    user = get_current_user()
    iso_path = request.form.get('iso_path', '').strip()
    if not iso_path or not os.path.exists(iso_path):
        return jsonify({'success': False, 'error': 'ISO file not found'})
    conn = get_db()
    vm = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    if user['role'] not in ('admin', 'reseller') and vm['user_id'] != user['id']:
        conn.close()
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    if user['role'] == 'reseller' and vm['user_id'] not in [r['id'] for r in conn.execute("SELECT id FROM users WHERE parent_id=?", (user['id'],)).fetchall()]:
        conn.close()
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    if is_vm_running(uuid):
        subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
        time.sleep(1)
        conn.execute("UPDATE vms SET status='stopped' WHERE uuid=?", (uuid,))
    existing = conn.execute("SELECT id FROM iso_mounts WHERE vm_uuid=? AND mounted=1", (uuid,)).fetchone()
    if existing:
        conn.execute("UPDATE iso_mounts SET mounted=0 WHERE vm_uuid=?", (uuid,))
    conn.execute("INSERT INTO iso_mounts (vm_uuid, iso_path, mounted) VALUES (?,?,1)", (uuid, iso_path))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'mounted ISO {iso_path} on VM {uuid}')
    return jsonify({'success': True, 'message': 'ISO mounted. Start VM to apply.'})


@app.route('/api/vm/<uuid>/iso/unmount', methods=['POST'])
@login_required
def api_unmount_iso(uuid):
    user = get_current_user()
    conn = get_db()
    vm = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    if user['role'] not in ('admin', 'reseller') and vm['user_id'] != user['id']:
        conn.close()
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    if user['role'] == 'reseller' and vm['user_id'] not in [r['id'] for r in conn.execute("SELECT id FROM users WHERE parent_id=?", (user['id'],)).fetchall()]:
        conn.close()
        return jsonify({'success': False, 'error': 'Access denied'}), 403
    if is_vm_running(uuid):
        subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
        time.sleep(1)
        conn.execute("UPDATE iso_mounts SET mounted=0 WHERE vm_uuid=?", (uuid,))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'unmounted ISO from VM {uuid}')
    return jsonify({'success': True, 'message': 'ISO unmounted'})


@app.route('/api/isos')
@login_required
@role_required('admin', 'reseller')
def api_list_isos():
    conn = get_db()
    rows = conn.execute("""
        SELECT c.*, u.username as uploaded_by_name
        FROM custom_isos c
        LEFT JOIN users u ON c.uploaded_by = u.id
        ORDER BY c.created_at DESC
    """).fetchall()
    conn.close()
    return jsonify({'success': True, 'isos': [dict(r) for r in rows]})


@app.route('/api/isos/upload', methods=['POST'])
@login_required
@role_required('admin', 'reseller')
def api_upload_iso():
    user = get_current_user()
    if 'file' not in request.files:
        return jsonify({'success': False, 'error': 'No file provided'})
    f = request.files['file']
    if not f.filename:
        return jsonify({'success': False, 'error': 'No file selected'})
    if not f.filename.lower().endswith('.iso'):
        return jsonify({'success': False, 'error': 'Only .iso files allowed'})
    f.seek(0, os.SEEK_END)
    size = f.tell()
    f.seek(0)
    if size > MAX_ISO_SIZE:
        return jsonify({'success': False, 'error': f'File too large. Maximum size is 10GB.'})
    name = f.filename
    dest = os.path.join(ISO_DIR, name)
    if os.path.exists(dest):
        base, ext = os.path.splitext(name)
        counter = 1
        while os.path.exists(os.path.join(ISO_DIR, f"{base}_{counter}{ext}")):
            counter += 1
        dest = os.path.join(ISO_DIR, f"{base}_{counter}{ext}")
        name = os.path.basename(dest)
    f.save(dest)
    conn = get_db()
    conn.execute("INSERT INTO custom_isos (name, file_path, size, uploaded_by) VALUES (?,?,?,?)",
                 (name, dest, size, user['id']))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'uploaded custom ISO: {name} ({size} bytes)')
    return jsonify({'success': True, 'message': f'ISO {name} uploaded', 'name': name})


@app.route('/api/isos/<int:iso_id>/delete', methods=['POST'])
@login_required
@role_required('admin')
def api_delete_iso(iso_id):
    conn = get_db()
    iso = conn.execute("SELECT * FROM custom_isos WHERE id=?", (iso_id,)).fetchone()
    if not iso:
        conn.close()
        return jsonify({'success': False, 'error': 'ISO not found'}), 404
    if os.path.exists(iso['file_path']):
        os.remove(iso['file_path'])
    conn.execute("DELETE FROM custom_isos WHERE id=?", (iso_id,))
    conn.commit()
    conn.close()
    log_activity(session['user_id'], f'deleted custom ISO: {iso["name"]}')
    return jsonify({'success': True, 'message': 'ISO deleted'})


@app.route('/api/vm/<uuid>/apps')
@login_required
def api_list_apps(uuid):
    return jsonify({'success': True, 'apps': APP_INSTALLERS})


@app.route('/api/vm/<uuid>/apps/install', methods=['POST'])
@login_required
def api_install_app(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    if not is_vm_running(uuid):
        conn.close()
        return jsonify({'success': False, 'error': 'VM must be running to install apps'})
    app_key = request.form.get('app_key', '')
    if app_key not in APP_INSTALLERS:
        conn.close()
        return jsonify({'success': False, 'error': 'Unknown app'})
    app_def = APP_INSTALLERS[app_key]
    script = app_def['script'].format(username=vm['username'], password=vm['password'], email='admin@localhost')
    ssh_port = vm['ssh_port'] or 22
    username = vm['username'] or 'root'
    password = vm['password'] or ''
    conn.close()
    log_activity(user['id'], f'installing {app_key} on VM {uuid}')
    def run_install():
        try:
            subprocess.run([
                'sshpass', '-p', password,
                'ssh', '-o', 'StrictHostKeyChecking=no',
                '-o', 'UserKnownHostsFile=/dev/null',
                '-p', str(ssh_port),
                f'{username}@localhost',
                script
            ], check=True, timeout=600)
        except Exception as e:
            log_activity(user['id'], f'app install {app_key} failed on VM {uuid}: {e}')
    threading.Thread(target=run_install, daemon=True).start()
    return jsonify({'success': True, 'message': f'Installing {app_def["name"]}...'})


@app.route('/login/2fa', methods=['GET', 'POST'])
def login_2fa():
    if 'user_id' not in session:
        return redirect('/login')
    if session.get('2fa_verified'):
        return redirect('/dashboard')
    conn = get_db()
    user = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    if not user or not user['otp_enabled']:
        return redirect('/dashboard')
    error = ''
    if request.method == 'POST':
        code = request.form.get('code', '').strip()
        if not code:
            error = 'Code is required'
        else:
            totp = pyotp.TOTP(user['otp_secret'])
            if totp.verify(code):
                session['2fa_verified'] = True
                log_activity(user['id'], '2FA verified')
                return redirect('/dashboard')
            else:
                error = 'Invalid code'
    template = open(Path(__file__).parent / 'templates' / 'login_2fa.html').read()
    return render_template_string(template, title='Two-Factor Auth', error=error, current_user=None)


@app.route('/novnc/<path:filename>')
def novnc_static(filename):
    novnc_dir = '/usr/share/novnc'
    filepath = os.path.normpath(os.path.join(novnc_dir, filename))
    if not filepath.startswith(os.path.normpath(novnc_dir)):
        return "Forbidden", 403
    if not os.path.isfile(filepath):
        return "Not found", 404
    mime = mimetypes.guess_type(filepath)[0] or 'application/octet-stream'
    with open(filepath, 'rb') as f:
        content = f.read()
    return Response(content, mimetype=mime)


# ========== USERS ==========

@app.route('/users')
@login_required
@role_required('admin', 'reseller')
def users_list():
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        rows = conn.execute("""SELECT u.*,p.username as parent_name,(SELECT COUNT(*) FROM vms WHERE user_id=u.id) as vm_count FROM users u LEFT JOIN users p ON u.parent_id=p.id ORDER BY u.role,u.username""").fetchall()
        stats = {'total': conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                 'admins': conn.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0],
                 'resellers': conn.execute("SELECT COUNT(*) FROM users WHERE role='reseller'").fetchone()[0],
                 'clients': conn.execute("SELECT COUNT(*) FROM users WHERE role='client'").fetchone()[0]}
    else:
        rows = conn.execute("SELECT u.*,p.username as parent_name,(SELECT COUNT(*) FROM vms WHERE user_id=u.id) as vm_count FROM users u LEFT JOIN users p ON u.parent_id=p.id WHERE u.parent_id=? ORDER BY u.username", (user['id'],)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM users WHERE parent_id=?", (user['id'],)).fetchone()[0]
        stats = {'total': total, 'admins': 0, 'resellers': 0, 'clients': total}
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'users.html').read()
    return render_template_string(template, title='Users', active_page='users', current_user=user, users=[dict(r) for r in rows], stats=stats)


@app.route('/users/create', methods=['GET','POST'])
@app.route('/users/create/<role>', methods=['GET','POST'])
@login_required
@role_required('admin', 'reseller')
def create_user(role='client'):
    user = get_current_user()
    conn = get_db()
    resellers = conn.execute("SELECT id,username FROM users WHERE role='reseller' ORDER BY username").fetchall()
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', 'vpanel@123')
        email = request.form.get('email', '')
        urole = request.form.get('role', 'client')
        parent_id = request.form.get('parent_id', '')
        balance = float(request.form.get('balance', 0))
        max_vms = int(request.form.get('max_vms', 5))
        max_cpus = int(request.form.get('max_cpus', 8))
        max_ram = int(request.form.get('max_ram', 16384))
        max_disk = int(request.form.get('max_disk', 200))
        allowed_os = request.form.get('allowed_os', 'all')
        status = request.form.get('status', 'active')
        if user['role'] == 'reseller':
            parent_id, urole = user['id'], 'client'
        pw_hash = hash_password(password)
        try:
            conn.execute("""INSERT INTO users (username,password,email,role,parent_id,balance,max_vms,max_cpus,max_ram,max_disk,allowed_os,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (username, pw_hash, email, urole, parent_id or None, balance, max_vms, max_cpus, max_ram, max_disk, allowed_os, status))
            conn.commit()
            log_activity(user['id'], f'Created {urole}: {username}')
            conn.close()
            flash(f'{urole} created', 'success')
            return redirect('/users')
        except Exception as e:
            conn.close()
            flash(str(e), 'error')
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'create_user.html').read()
    return render_template_string(template, title=f'Create {role.title()}', active_page='users', current_user=user, role=role, resellers=[dict(r) for r in resellers])


@app.route('/users/<int:uid>/edit', methods=['GET','POST'])
@login_required
@role_required('admin')
def edit_user(uid):
    user = get_current_user()
    conn = get_db()
    target = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if not target:
        conn.close()
        return "User not found", 404
    if request.method == 'POST':
        balance = float(request.form.get('balance', 0))
        max_vms = int(request.form.get('max_vms', 5))
        max_cpus = int(request.form.get('max_cpus', 8))
        max_ram = int(request.form.get('max_ram', 16384))
        max_disk = int(request.form.get('max_disk', 200))
        status = request.form.get('status', 'active')
        conn.execute("UPDATE users SET balance=?,max_vms=?,max_cpus=?,max_ram=?,max_disk=?,status=? WHERE id=?",
                     (balance, max_vms, max_cpus, max_ram, max_disk, status, uid))
        conn.commit()
        log_activity(user['id'], f'Updated user {uid}')
        conn.close()
        flash('User updated', 'success')
        return redirect('/users')
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'edit_user.html').read()
    return render_template_string(template, title='Edit User', active_page='users', current_user=user, target=dict(target))


# ========== PLANS ==========

@app.route('/plans')
@login_required
@role_required('admin', 'reseller')
def plans():
    user = get_current_user()
    conn = get_db()
    rows = conn.execute("SELECT * FROM vm_plans ORDER BY price_monthly").fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'plans.html').read()
    return render_template_string(template, title='Plans', active_page='plans', current_user=user, plans=[dict(p) for p in rows])


# ========== NODES ==========

@app.route('/nodes')
@login_required
@role_required('admin')
def nodes():
    user = get_current_user()
    conn = get_db()
    rows = conn.execute("""
        SELECT n.*,
            (SELECT COUNT(*) FROM vms WHERE node_id=n.id) as vm_count,
            (SELECT COUNT(*) FROM vms WHERE node_id=n.id AND status='running') as running_vms
        FROM nodes n ORDER BY n.name
    """).fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'nodes.html').read()
    return render_template_string(template, title='Nodes', active_page='nodes', current_user=user, nodes=[dict(r) for r in rows])


@app.route('/nodes/create', methods=['GET','POST'])
@login_required
@role_required('admin')
def create_node():
    user = get_current_user()
    conn = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        hostname = request.form.get('hostname', '').strip()
        ip_address = request.form.get('ip_address', '')
        total_cpus = int(request.form.get('total_cpus', 0))
        total_ram = int(request.form.get('total_ram', 0))
        total_disk = int(request.form.get('total_disk', 0))
        try:
            conn.execute("INSERT INTO nodes (name,hostname,ip_address,total_cpus,total_ram,total_disk) VALUES (?,?,?,?,?,?)",
                         (name, hostname, ip_address, total_cpus, total_ram, total_disk))
            conn.commit()
            log_activity(user['id'], f'created node: {name}')
            conn.close()
            flash('Node created', 'success')
            return redirect('/nodes')
        except Exception as e:
            conn.close()
            flash(str(e), 'error')
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'create_node.html').read()
    return render_template_string(template, title='Create Node', active_page='nodes', current_user=user)


@app.route('/nodes/<int:nid>')
@login_required
@role_required('admin')
def node_detail(nid):
    user = get_current_user()
    conn = get_db()
    node = conn.execute("SELECT * FROM nodes WHERE id=?", (nid,)).fetchone()
    if not node:
        conn.close()
        return "Node not found", 404
    vms = conn.execute("SELECT v.*,u.username as owner FROM vms v JOIN users u ON v.user_id=u.id WHERE v.node_id=? ORDER BY v.created_at DESC", (nid,)).fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'node_detail.html').read()
    return render_template_string(template, title=node['name'], active_page='nodes', current_user=user, node=dict(node), vms=[dict(v) for v in vms])


# ========== INVOICES ==========

@app.route('/invoices')
@login_required
def invoices():
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        rows = conn.execute("SELECT i.*,u.username FROM invoices i JOIN users u ON i.user_id=u.id ORDER BY i.created_at DESC LIMIT 50").fetchall()
        stats = {'total_revenue': conn.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid'").fetchone()[0],
                 'pending': conn.execute("SELECT COUNT(*) FROM invoices WHERE status='pending'").fetchone()[0],
                 'paid': conn.execute("SELECT COUNT(*) FROM invoices WHERE status='paid'").fetchone()[0],
                 'overdue': conn.execute("SELECT COUNT(*) FROM invoices WHERE status='overdue'").fetchone()[0]}
    elif user['role'] == 'reseller':
        rows = conn.execute("SELECT i.*,u.username FROM invoices i JOIN users u ON i.user_id=u.id WHERE i.user_id IN (SELECT id FROM users WHERE parent_id=?) ORDER BY i.created_at DESC LIMIT 50", (user['id'],)).fetchall()
        stats = {'total_revenue': conn.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0],
                 'pending': conn.execute("SELECT COUNT(*) FROM invoices WHERE status='pending' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0],
                 'paid': conn.execute("SELECT COUNT(*) FROM invoices WHERE status='paid' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0],
                 'overdue': 0}
    else:
        rows = conn.execute("SELECT * FROM invoices WHERE user_id=? ORDER BY created_at DESC LIMIT 50", (user['id'],)).fetchall()
        stats = {'total_revenue': conn.execute("SELECT COALESCE(SUM(total),0) FROM invoices WHERE status='paid' AND user_id=?", (user['id'],)).fetchone()[0],
                 'pending': conn.execute("SELECT COUNT(*) FROM invoices WHERE status='pending' AND user_id=?", (user['id'],)).fetchone()[0],
                 'paid': conn.execute("SELECT COUNT(*) FROM invoices WHERE status='paid' AND user_id=?", (user['id'],)).fetchone()[0],
                 'overdue': 0}
    conn.close()
    stripe_key = ''
    if user['role'] in ('admin', 'reseller'):
        stripe_key = get_settings().get('stripe_publishable_key', '')
    template = open(Path(__file__).parent / 'templates' / 'invoices.html').read()
    return render_template_string(template, title='Invoices', active_page='invoices', current_user=user, invoices=[dict(r) for r in rows], stats=stats, stripe_key=stripe_key)


# ========== TICKETS ==========

@app.route('/tickets')
@login_required
def tickets():
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        rows = conn.execute("SELECT t.*,u.username FROM tickets t JOIN users u ON t.user_id=u.id ORDER BY t.created_at DESC").fetchall()
        tot = conn.execute("SELECT COUNT(*) FROM tickets").fetchone()[0]
        op = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open'").fetchone()[0]
        ip = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='in_progress'").fetchone()[0]
        cl = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='closed'").fetchone()[0]
    elif user['role'] == 'reseller':
        rows = conn.execute("SELECT t.*,u.username FROM tickets t JOIN users u ON t.user_id=u.id WHERE t.user_id IN (SELECT id FROM users WHERE parent_id=?) ORDER BY t.created_at DESC", (user['id'],)).fetchall()
        tot = conn.execute("SELECT COUNT(*) FROM tickets WHERE user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        op = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        ip = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='in_progress' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
        cl = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='closed' AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (user['id'],)).fetchone()[0]
    else:
        rows = conn.execute("SELECT * FROM tickets WHERE user_id=? ORDER BY created_at DESC", (user['id'],)).fetchall()
        tot = conn.execute("SELECT COUNT(*) FROM tickets WHERE user_id=?", (user['id'],)).fetchone()[0]
        op = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='open' AND user_id=?", (user['id'],)).fetchone()[0]
        ip = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='in_progress' AND user_id=?", (user['id'],)).fetchone()[0]
        cl = conn.execute("SELECT COUNT(*) FROM tickets WHERE status='closed' AND user_id=?", (user['id'],)).fetchone()[0]
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'tickets.html').read()
    return render_template_string(template, title='Tickets', active_page='tickets', current_user=user, tickets=[dict(r) for r in rows], stats={'total': tot, 'open': op, 'in_progress': ip, 'closed': cl})


# ========== API: TEST EMAIL ==========

@app.route('/api/test-email', methods=['POST'])
@login_required
@role_required('admin')
def api_test_email():
    to_email = request.form.get('email', '').strip()
    if not to_email:
        return jsonify({'success': False, 'error': 'Email required'})
    ok = send_email(to_email, 'Test from vPanel', 'This is a test email from your vPanel installation.\n\nIf you received this, SMTP is working correctly.')
    return jsonify({'success': ok, 'message': 'Email sent' if ok else 'Failed to send email'})


@app.route('/tickets/create', methods=['GET','POST'])
@login_required
def create_ticket():
    user = get_current_user()
    if request.method == 'POST':
        subject = request.form.get('subject', '')
        message = request.form.get('message', '')
        priority = request.form.get('priority', 'normal')
        department = request.form.get('department', 'support')
        uid = generate_uuid()
        conn = get_db()
        conn.execute("INSERT INTO tickets (uuid,user_id,subject,message,priority,department) VALUES (?,?,?,?,?,?)",
                     (uid, user['id'], subject, message, priority, department))
        conn.commit()
        log_activity(user['id'], f'created ticket: {subject[:30]}')
        conn.close()
        flash('Ticket created', 'success')
        return redirect('/tickets')
    template = open(Path(__file__).parent / 'templates' / 'create_ticket.html').read()
    return render_template_string(template, title='New Ticket', active_page='tickets', current_user=user)


@app.route('/tickets/<uuid>')
@login_required
def ticket_view(uuid):
    user = get_current_user()
    conn = get_db()
    row = conn.execute("SELECT t.*,u.username FROM tickets t JOIN users u ON t.user_id=u.id WHERE t.uuid=?", (uuid,)).fetchone()
    if not row:
        conn.close()
        return "Ticket not found", 404
    replies = conn.execute("SELECT r.*,u.username FROM ticket_replies r JOIN users u ON r.user_id=u.id WHERE r.ticket_id=? ORDER BY r.created_at", (row['id'],)).fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'ticket_view.html').read()
    return render_template_string(template, title=f'Ticket: {row["subject"][:30]}', active_page='tickets', current_user=user, ticket=dict(row), replies=[dict(r) for r in replies])


# ========== ACTIVITY ==========

@app.route('/activity')
@login_required
def activity():
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        rows = conn.execute("SELECT a.*,u.username FROM activity_log a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.created_at DESC LIMIT 100").fetchall()
    elif user['role'] == 'reseller':
        rows = conn.execute("SELECT a.*,u.username FROM activity_log a LEFT JOIN users u ON a.user_id=u.id WHERE a.user_id IN (SELECT id FROM users WHERE parent_id=?) OR a.user_id=? ORDER BY a.created_at DESC LIMIT 100", (user['id'], user['id'])).fetchall()
    else:
        rows = conn.execute("SELECT * FROM activity_log WHERE user_id=? ORDER BY created_at DESC LIMIT 100", (user['id'],)).fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'activity.html').read()
    return render_template_string(template, title='Activity', active_page='activity', current_user=user, activities=[dict(r) for r in rows])


# ========== SETTINGS ==========

@app.route('/settings', methods=['GET','POST'])
@login_required
@role_required('admin')
def settings():
    user = get_current_user()
    conn = get_db()
    if request.method == 'POST':
        keys = ['site_name', 'currency', 'profit_margin',
                'smtp_host', 'smtp_port', 'smtp_user', 'smtp_pass', 'smtp_from',
                'stripe_secret_key', 'stripe_publishable_key', 'stripe_webhook_secret',
                'site_logo', 'footer_text', 'default_theme', 'custom_css', 'branding_show']
        for key in keys:
            conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, request.form.get(key, '')))
        conn.commit()
        log_activity(user['id'], 'updated settings')
        conn.close()
        flash('Settings saved', 'success')
        return redirect('/settings')
    settings_data = {row['key']: row['value'] for row in conn.execute("SELECT * FROM settings").fetchall()}
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'settings.html').read()
    return render_template_string(template, title='Settings', active_page='settings', current_user=user, settings=settings_data)


# ========== API: VM ==========

@app.route('/api/vm/create', methods=['POST'])
@login_required
def api_create_vm():
    user = get_current_user()
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Name required'})
    vm_uuid = generate_uuid()
    os_type = request.form.get('os_type', 'ubuntu')
    os_version = request.form.get('os_version', '22.04')
    cpus = int(request.form.get('cpus', 1))
    ram = int(request.form.get('ram', 1024))
    disk_size = request.form.get('disk_size', '20G')
    ssh_port = int(request.form.get('ssh_port', 2222))
    username = request.form.get('username', 'root')
    password = request.form.get('password', 'vpanel@123')
    hostname = request.form.get('hostname', name)
    cpu_model = request.form.get('cpu_model', 'Host CPU (Passthrough)')
    bandwidth = int(request.form.get('bandwidth', 1000))
    plan_id = request.form.get('plan_id')
    node_id = request.form.get('node_id')
    uid = request.form.get('user_id', user['id'])
    disk_gb = parse_disk_size(disk_size)
    img_file = os.path.join(VM_DIR, f"{vm_uuid}.qcow2")
    seed_file = os.path.join(VM_DIR, f"{vm_uuid}-seed.qcow2")
    hourly_rates = {1: 0.005, 2: 0.01, 4: 0.02, 6: 0.04, 8: 0.08}
    hourly_cost = hourly_rates.get(cpus, 0.01)
    conn = get_db()
    try:
        conn.execute("""INSERT INTO vms (uuid,name,user_id,plan_id,node_id,os_type,os_version,hostname,username,password,cpus,ram,disk_size,disk_gb,ssh_port,cpu_model,img_file,seed_file,bandwidth_limit,hourly_cost,status) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'stopped')""",
                     (vm_uuid, name, uid, plan_id or None, node_id or None,
                      os_type, os_version, hostname, username, password,
                      cpus, ram, disk_size, disk_gb, ssh_port, cpu_model,
                      img_file, seed_file, bandwidth, hourly_cost))
        if node_id:
            conn.execute("UPDATE nodes SET used_cpus=used_cpus+?, used_ram=used_ram+?, used_disk=used_disk+? WHERE id=?",
                        (cpus, ram, disk_gb, node_id))
        conn.commit()
        log_activity(user['id'], f'created VM: {name}')
        conn.close()
        img_url = OS_IMAGES.get(os_type, {}).get(os_version, {}).get('url', '')
        threading.Thread(target=background_create_vm, args=(vm_uuid, img_url, img_file, seed_file, disk_size, username, password, hostname, name), daemon=True).start()
        return jsonify({'success': True, 'uuid': vm_uuid, 'message': 'VM created'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})


def background_create_vm(vm_uuid, img_url, img_file, seed_file, disk_size, username, password, hostname, vm_name):
    try:
        if img_url:
            base_img = os.path.join(VM_DIR, f"base-{os.path.basename(img_url)}")
            if not os.path.exists(base_img):
                subprocess.run(['wget', '-q', '-O', base_img, img_url], check=True, timeout=300)
            disk_sz = disk_size if disk_size.endswith(('G','M','T')) else f"{disk_size}G"
            subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-b', base_img, '-F', 'qcow2', img_file, disk_sz], check=True, capture_output=True, timeout=60)
        user_data = f"""#cloud-config\nhostname: {hostname}\nusers:\n  - name: {username}\n    sudo: ALL=(ALL) NOPASSWD:ALL\n    shell: /bin/bash\n    lock_passwd: false\nchpasswd:\n  list: |\n    {username}:{password}\n  expire: false\nssh_pwauth: true\npackage_update: false\n"""
        with open('/tmp/seed-user-data', 'w') as f: f.write(user_data)
        with open('/tmp/seed-meta-data', 'w') as f: f.write(f"instance-id: {vm_uuid}\nlocal-hostname: {hostname}\n")
        subprocess.run(['cloud-localds', seed_file, '/tmp/seed-user-data', '/tmp/seed-meta-data'], check=True, capture_output=True, timeout=30)
        os.remove('/tmp/seed-user-data')
        os.remove('/tmp/seed-meta-data')
        conn = get_db()
        conn.execute("UPDATE vms SET notes='Ready' WHERE uuid=?", (vm_uuid,))
        vm_row = conn.execute("SELECT * FROM vms WHERE uuid=?", (vm_uuid,)).fetchone()
        user_row = conn.execute("SELECT u.email,u.username FROM vms v JOIN users u ON v.user_id=u.id WHERE v.uuid=?", (vm_uuid,)).fetchone()
        conn.commit()
        if user_row and user_row['email']:
            send_email(user_row['email'], 'VM Created',
                       f'Hi {user_row["username"]},\n\nYour VM "{vm_name}" has been created.\n\nThanks,\nvpanel')
        # Auto-start VM after provisioning if auto_start is enabled
        if vm_row and vm_row['auto_start']:
            try:
                _start_vm_internal(vm_uuid, conn)
            except Exception as start_err:
                conn.execute("UPDATE vms SET notes=? WHERE uuid=?", (f'Provisioned, auto-start failed: {start_err}', vm_uuid))
                conn.commit()
        conn.close()
    except Exception as e:
        conn = get_db()
        conn.execute("UPDATE vms SET notes=? WHERE uuid=?", (f'Error: {str(e)}', vm_uuid))
        conn.commit()
        conn.close()


def _start_vm_internal(uuid, conn=None):
    """Start a VM without requiring user context (for auto-start/provisioning)."""
    if is_vm_running(uuid):
        return False
    close_conn = False
    if conn is None:
        conn = get_db()
        close_conn = True
    try:
        vm = dict(conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone())
        if not vm or vm.get('suspended'):
            if close_conn: conn.close()
            return False
        vnc_port = get_available_port()
        ws_port = get_available_port(vnc_port + 1)
        cpu_string = CPU_MODELS.get(vm['cpu_model'], 'host')
        kvm = check_kvm()
        cmd = ['qemu-system-x86_64', '-name', f'vpanel-{uuid}',
               '-machine', 'type=q35,accel=kvm' if kvm else 'type=q35',
               '-cpu', cpu_string, '-smp', str(vm['cpus']), '-m', str(vm['ram']),
               '-drive', f'file={vm["img_file"]},format=qcow2,if=virtio',
               '-drive', f'file={vm["seed_file"]},format=raw,if=virtio',
               '-netdev', f'user,id=net0,hostfwd=tcp::{vm["ssh_port"]}-:22',
               '-device', 'virtio-net-pci,netdev=net0',
               '-vnc', f':{vnc_port - 5900}', '-vga', 'virtio', '-display', 'none',
               '-usb', '-device', 'usb-tablet', '-k', 'en-us',
               '-rtc', 'base=localtime,clock=host', '-msg', 'timestamp=on']
        iso_mount = conn.execute("SELECT iso_path FROM iso_mounts WHERE vm_uuid=? AND mounted=1 ORDER BY id DESC LIMIT 1", (uuid,)).fetchone()
        if iso_mount and os.path.exists(iso_mount['iso_path']):
            cmd.extend(['-cdrom', iso_mount['iso_path']])
        log_file = os.path.join(VM_DIR, f'{uuid}.log')
        with open(log_file, 'w') as logf:
            subprocess.Popen(cmd, stdout=logf, stderr=logf)
        ws_log = os.path.join(VM_DIR, f'{uuid}-ws.log')
        with open(ws_log, 'w') as wsf:
            subprocess.Popen(['websockify', str(ws_port), f'127.0.0.1:{vnc_port}'],
                           stdout=wsf, stderr=wsf)
        conn.execute("UPDATE vms SET status='running',vnc_port=?,ws_port=?,started_at=CURRENT_TIMESTAMP WHERE uuid=?", (vnc_port, ws_port, uuid))
        conn.commit()
        try:
            apply_firewall(uuid)
        except Exception:
            pass
        return True
    except Exception:
        return False
    finally:
        if close_conn:
            conn.close()


@app.route('/api/vm/<uuid>/<action>', methods=['POST'])
@login_required
def api_control_vm(uuid, action):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)

    if action == 'start':
        if is_vm_running(uuid):
            conn.close()
            return jsonify({'success': False, 'error': 'Already running'})
        if vm.get('suspended'):
            conn.close()
            return jsonify({'success': False, 'error': 'VM is suspended'})
        success = _start_vm_internal(uuid, conn)
        conn.close()
        if success:
            log_activity(user['id'], f'started VM: {vm["name"]}')
            return jsonify({'success': True, 'message': 'VM started'})
        return jsonify({'success': False, 'error': 'Failed to start VM'})

    elif action == 'stop':
        subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
        subprocess.run(['pkill', '-f', f'websockify.*{vm.get("ws_port", "")}'], capture_output=True, timeout=10)
        time.sleep(1)
        conn.execute("UPDATE vms SET status='stopped',vnc_port=NULL,ws_port=NULL WHERE uuid=?", (uuid,))
        conn.commit()
        conn.close()
        log_activity(user['id'], f'stopped VM: {vm["name"]}')
        return jsonify({'success': True, 'message': 'VM stopped'})

    elif action == 'restart':
        subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
        subprocess.run(['pkill', '-f', f'websockify.*{vm.get("ws_port", "")}'], capture_output=True, timeout=10)
        time.sleep(2)
        conn.close()
        return api_control_vm(uuid, 'start')

    elif action == 'delete':
        subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
        subprocess.run(['pkill', '-f', f'websockify.*{vm.get("ws_port", "")}'], capture_output=True, timeout=10)
        time.sleep(1)
        for f in [vm.get('img_file'), vm.get('seed_file')]:
            if f and os.path.exists(f):
                os.remove(f)
        conn.execute("DELETE FROM snapshots WHERE vm_uuid=?", (uuid,))
        conn.execute("DELETE FROM firewall_rules WHERE vm_uuid=?", (uuid,))
        conn.execute("DELETE FROM iso_mounts WHERE vm_uuid=?", (uuid,))
        conn.execute("DELETE FROM backup_schedules WHERE vm_uuid=?", (uuid,))
        conn.execute("DELETE FROM monitoring_checks WHERE vm_uuid=?", (uuid,))
        conn.execute("DELETE FROM monitoring_logs WHERE check_id NOT IN (SELECT id FROM monitoring_checks) OR check_id IS NULL")
        conn.execute("DELETE FROM vms WHERE uuid=?", (uuid,))
        conn.commit()
        conn.close()
        log_activity(user['id'], f'deleted VM: {vm["name"]}')
        return jsonify({'success': True, 'message': 'VM deleted'})

    elif action == 'suspend':
        subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
        subprocess.run(['pkill', '-f', f'websockify.*{vm.get("ws_port", "")}'], capture_output=True, timeout=10)
        time.sleep(1)
        conn.execute("UPDATE vms SET status='stopped',vnc_port=NULL,ws_port=NULL,suspended=1 WHERE uuid=?", (uuid,))
        conn.commit()
        conn.close()
        log_activity(user['id'], f'suspended VM: {vm["name"]}')
        return jsonify({'success': True, 'message': 'VM suspended'})

    elif action == 'unsuspend':
        conn.execute("UPDATE vms SET suspended=0,vnc_port=NULL,ws_port=NULL WHERE uuid=?", (uuid,))
        conn.commit()
        conn.close()
        log_activity(user['id'], f'unsuspended VM: {vm["name"]}')
        return jsonify({'success': True, 'message': 'VM unsuspended. Start it to use.'})

    conn.close()
    return jsonify({'success': False, 'error': 'Unknown action'})


def run_ssh_command(vm_uuid, command):
    try:
        conn = get_db()
        vm = dict(conn.execute("SELECT * FROM vms WHERE uuid=?", (vm_uuid,)).fetchone())
        conn.close()
        if not vm or not is_vm_running(vm_uuid):
            return
        import paramiko
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect('127.0.0.1', port=vm['ssh_port'], username=vm['username'], password=vm['password'], timeout=10)
        ssh.exec_command(command, timeout=30)
        ssh.close()
    except Exception:
        pass


@app.route('/api/vm/<uuid>/rename', methods=['POST'])
@login_required
def api_rename_vm(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Name required'})
    conn.execute("UPDATE vms SET name=? WHERE uuid=?", (name, uuid))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'renamed VM to: {name}')
    return jsonify({'success': True, 'message': 'VM renamed'})


@app.route('/api/vm/<uuid>/hostname', methods=['POST'])
@login_required
def api_change_hostname(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    hostname = request.form.get('hostname', '').strip()
    if not hostname:
        return jsonify({'success': False, 'error': 'Hostname required'})
    conn.execute("UPDATE vms SET hostname=? WHERE uuid=?", (hostname, uuid))
    conn.commit()
    conn.close()
    if is_vm_running(uuid):
        threading.Thread(target=run_ssh_command, args=(uuid, f"hostnamectl set-hostname {hostname}"), daemon=True).start()
    log_activity(user['id'], f'changed hostname for VM {uuid}')
    return jsonify({'success': True, 'message': 'Hostname updated'})


@app.route('/api/vm/<uuid>/reset-password', methods=['POST'])
@login_required
def api_reset_password(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    new_password = request.form.get('password', '').strip()
    if not new_password or len(new_password) < 6:
        return jsonify({'success': False, 'error': 'Password must be at least 6 characters'})
    conn.execute("UPDATE vms SET password=? WHERE uuid=?", (new_password, uuid))
    conn.commit()
    conn.close()
    if is_vm_running(uuid):
        threading.Thread(target=run_ssh_command, args=(uuid, f"echo 'root:{new_password}' | chpasswd"), daemon=True).start()
    log_activity(user['id'], f'reset password for VM {uuid}')
    return jsonify({'success': True, 'message': 'Password reset'})


@app.route('/api/vm/<uuid>/transfer', methods=['POST'])
@login_required
@role_required('admin')
def api_transfer_vm(uuid):
    new_user_id = request.form.get('user_id', type=int)
    if not new_user_id:
        return jsonify({'success': False, 'error': 'User ID required'})
    conn = get_db()
    target = conn.execute("SELECT id, username FROM users WHERE id=?", (new_user_id,)).fetchone()
    if not target:
        conn.close()
        return jsonify({'success': False, 'error': 'Target user not found'})
    conn.execute("UPDATE vms SET user_id=? WHERE uuid=?", (new_user_id, uuid))
    conn.commit()
    conn.close()
    log_activity(session['user_id'], f'transferred VM {uuid} to user {target["username"]}')
    return jsonify({'success': True, 'message': f'VM transferred to {target["username"]}'})


@app.route('/api/vm/<uuid>/clone', methods=['POST'])
@login_required
def api_clone_vm(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    new_name = request.form.get('name', '').strip()
    if not new_name:
        return jsonify({'success': False, 'error': 'New VM name required'})
    if is_vm_running(uuid):
        conn.close()
        return jsonify({'success': False, 'error': 'VM must be stopped to clone'})
    new_uuid = generate_uuid()
    new_img = os.path.join(VM_DIR, f'{new_uuid}.qcow2')
    try:
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-b', vm['img_file'], '-F', 'qcow2', new_img], check=True, capture_output=True, timeout=120)
        conn.execute("""INSERT INTO vms (uuid, name, user_id, plan_id, node_id, os_type, os_version, hostname, username, password, cpus, ram, disk_size, disk_gb, ssh_port, status, img_file, notes)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                     (new_uuid, new_name, vm['user_id'], vm['plan_id'], vm['node_id'], vm['os_type'], vm['os_version'],
                      vm['hostname'], vm['username'], vm['password'], vm['cpus'], vm['ram'], vm['disk_size'], vm['disk_gb'],
                      get_available_port(), 'stopped', new_img, f'Cloned from {vm["name"]}'))
        conn.commit()
        conn.close()
        log_activity(user['id'], f'cloned VM {vm["name"]} -> {new_name}')
        return jsonify({'success': True, 'message': 'VM cloned', 'new_uuid': new_uuid})
    except Exception as e:
        if os.path.exists(new_img):
            os.remove(new_img)
        conn.close()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/vm/<uuid>/migrate', methods=['POST'])
@login_required
@role_required('admin')
def api_migrate_vm(uuid):
    node_id = request.form.get('node_id', type=int)
    if not node_id:
        return jsonify({'success': False, 'error': 'Node ID required'})
    conn = get_db()
    node = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
    if not node:
        conn.close()
        return jsonify({'success': False, 'error': 'Node not found'})
    if is_vm_running(uuid):
        conn.close()
        return jsonify({'success': False, 'error': 'VM must be stopped to migrate'})
    conn.execute("UPDATE vms SET node_id=? WHERE uuid=?", (node_id, uuid))
    conn.commit()
    conn.close()
    log_activity(session['user_id'], f'migrated VM {uuid} to node {node["name"]}')
    return jsonify({'success': True, 'message': f'VM migrated to {node["name"]}'})


@app.route('/api/vm/<uuid>/tags', methods=['POST'])
@login_required
def api_update_tags(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    tags = request.form.get('tags', '')
    conn.execute("UPDATE vms SET tags=? WHERE uuid=?", (tags, uuid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Tags updated'})


@app.route('/api/vm/<uuid>/favorite', methods=['POST'])
@login_required
def api_toggle_favorite(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone())
    new_val = 0 if vm.get('favorite') else 1
    conn.execute("UPDATE vms SET favorite=? WHERE uuid=?", (new_val, uuid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'favorite': bool(new_val)})


@app.route('/api/vm/<uuid>/notes', methods=['POST'])
@login_required
def api_update_notes(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    notes = request.form.get('notes', '')
    conn.execute("UPDATE vms SET notes=? WHERE uuid=?", (notes, uuid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Notes updated'})


@app.route('/api/vm/<uuid>/force-reboot', methods=['POST'])
@login_required
def api_force_reboot(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    subprocess.run(['pkill', '-9', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
    subprocess.run(['pkill', '-f', f'websockify.*{vm.get("ws_port", "")}'], capture_output=True, timeout=10)
    time.sleep(2)
    conn.execute("UPDATE vms SET status='stopped',vnc_port=NULL,ws_port=NULL WHERE uuid=?", (uuid,))
    conn.commit()
    conn.close()
    time.sleep(1)
    return api_control_vm(uuid, 'start')


@app.route('/api/vm/<uuid>/force-shutdown', methods=['POST'])
@login_required
def api_force_shutdown(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    subprocess.run(['pkill', '-9', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
    subprocess.run(['pkill', '-9', '-f', f'websockify.*{vm.get("ws_port", "")}'], capture_output=True, timeout=10)
    conn.execute("UPDATE vms SET status='stopped',vnc_port=NULL,ws_port=NULL WHERE uuid=?", (uuid,))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'force shutdown VM {uuid}')
    return jsonify({'success': True, 'message': 'Force shutdown executed'})


@app.route('/api/vm/<uuid>/rescue-mode', methods=['POST'])
@login_required
def api_rescue_mode(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    enable = int(request.form.get('enable', 1))
    conn.execute("UPDATE vms SET rescue_mode=? WHERE uuid=?", (enable, uuid))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'{"enabled" if enable else "disabled"} rescue mode for VM {uuid}')
    return jsonify({'success': True, 'message': f'Rescue mode {"enabled" if enable else "disabled"}'})


@app.route('/api/vm/<uuid>/boot-order', methods=['POST'])
@login_required
def api_boot_order(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    boot_order = request.form.get('boot_order', 'disk')
    conn.execute("UPDATE vms SET boot_order=? WHERE uuid=?", (boot_order, uuid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Boot order updated'})


@app.route('/api/vm/<uuid>/bios-mode', methods=['POST'])
@login_required
def api_bios_mode(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    bios_mode = request.form.get('bios_mode', 'bios')
    conn.execute("UPDATE vms SET bios_mode=? WHERE uuid=?", (bios_mode, uuid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'BIOS mode updated'})


@app.route('/api/vm/<uuid>/dns', methods=['POST'])
@login_required
def api_dns_config(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    dns_servers = request.form.get('dns_servers', '')
    conn.execute("UPDATE vms SET dns_servers=? WHERE uuid=?", (dns_servers, uuid))
    conn.commit()
    conn.close()
    if is_vm_running(uuid) and dns_servers:
        resolv_content = dns_servers.replace(',', '\nnameserver ')
        threading.Thread(target=run_ssh_command, args=(uuid, f"echo 'nameserver {resolv_content}' > /etc/resolv.conf"), daemon=True).start()
    return jsonify({'success': True, 'message': 'DNS servers updated'})


@app.route('/api/vm/<uuid>/protect', methods=['POST'])
@login_required
def api_toggle_protection(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    protected = int(request.form.get('protected', 1))
    conn.execute("UPDATE vms SET protected=? WHERE uuid=?", (protected, uuid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'protected': bool(protected)})


@app.route('/api/vm/<uuid>/auto-renew', methods=['POST'])
@login_required
def api_toggle_auto_renew(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    auto_renew = int(request.form.get('auto_renew', 1))
    conn.execute("UPDATE vms SET auto_renew=? WHERE uuid=?", (auto_renew, uuid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'auto_renew': bool(auto_renew)})


@app.route('/api/vm/<uuid>/restart-network', methods=['POST'])
@login_required
def api_restart_network(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    if is_vm_running(uuid):
        threading.Thread(target=run_ssh_command, args=(uuid, "systemctl restart networking || systemctl restart NetworkManager || /etc/init.d/networking restart"), daemon=True).start()
        log_activity(user['id'], f'restarted network for VM {uuid}')
        return jsonify({'success': True, 'message': 'Network restart initiated'})
    return jsonify({'success': False, 'error': 'VM not running'})


@app.route('/api/vm/<uuid>/connection-details')
@login_required
def api_connection_details(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    conn.close()
    details = f"""vPanel Connection Details
==========================
Name: {vm['name']}
OS: {vm['os_type']} {vm['os_version']}
Status: {vm['status']}

SSH:
  Host: {request.host.split(':')[0]}
  Port: {vm['ssh_port']}
  User: {vm['username']}
  Password: {vm['password']}
  Command: ssh {vm['username']}@{request.host.split(':')[0]} -p {vm['ssh_port']}

VNC: {request.host_url.rstrip('/')}/vnc/{uuid}
Terminal: {request.host_url.rstrip('/')}/terminal/{uuid}

Notes: {vm.get('notes', 'N/A')}
"""
    return Response(details, mimetype='text/plain',
                    headers={'Content-Disposition': f'attachment; filename=vm-{uuid}-details.txt'})


def _ownership_vm(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not row:
        conn.close()
        return None, jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    return user, conn, vm


def _ownership(uuid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return None, result[1], result[2]
    return result


@app.route('/api/vm/<uuid>/ips', methods=['GET', 'POST'])
@login_required
def api_vm_ips(uuid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    if request.method == 'GET':
        conn.close()
        return jsonify({'success': True, 'ipv4': vm.get('ipv4_addresses',''), 'ipv6': vm.get('ipv6_addresses',''), 'floating_ip': vm.get('floating_ip',''), 'reverse_dns': vm.get('reverse_dns','')})
    ipv4 = request.form.get('ipv4', '')
    ipv6 = request.form.get('ipv6', '')
    floating = request.form.get('floating_ip', '')
    rdns = request.form.get('reverse_dns', '')
    conn.execute("UPDATE vms SET ipv4_addresses=?, ipv6_addresses=?, floating_ip=?, reverse_dns=? WHERE uuid=?", (ipv4, ipv6, floating, rdns, uuid))
    conn.commit(); conn.close()
    log_activity(user['id'], f'updated IP config for VM {uuid}')
    return jsonify({'success': True, 'message': 'IP configuration updated'})


@app.route('/api/ssh-keys', methods=['GET'])
@login_required
def api_list_ssh_keys():
    conn = get_db()
    keys = conn.execute("SELECT * FROM ssh_keys WHERE user_id=?", (session['user_id'],)).fetchall()
    conn.close()
    return jsonify({'success': True, 'keys': [dict(k) for k in keys]})


@app.route('/api/ssh-keys/create', methods=['POST'])
@login_required
def api_create_ssh_key():
    name = request.form.get('name', '').strip()
    public_key = request.form.get('public_key', '').strip()
    if not name or not public_key:
        return jsonify({'success': False, 'error': 'Name and public key required'})
    if 'ssh-rsa' not in public_key and 'ssh-ed25519' not in public_key and 'ecdsa' not in public_key:
        return jsonify({'success': False, 'error': 'Invalid SSH public key format'})
    fingerprint = public_key.split()[-1] if len(public_key.split()) >= 2 else public_key[-40:]
    conn = get_db()
    conn.execute("INSERT INTO ssh_keys (user_id, name, public_key, fingerprint) VALUES (?,?,?,?)", (session['user_id'], name, public_key, fingerprint[:64]))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'SSH key added'})


@app.route('/api/ssh-keys/<int:kid>/delete', methods=['POST'])
@login_required
def api_delete_ssh_key(kid):
    conn = get_db()
    conn.execute("DELETE FROM ssh_keys WHERE id=? AND user_id=?", (kid, session['user_id']))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'SSH key deleted'})


@app.route('/api/vm/<uuid>/ssh-keys', methods=['GET', 'POST'])
@login_required
def api_vm_ssh_keys(uuid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    if request.method == 'GET':
        keys = conn.execute("""SELECT sk.* FROM ssh_keys sk
            JOIN vm_ssh_keys vsk ON sk.id=vsk.ssh_key_id
            WHERE vsk.vm_uuid=?""", (uuid,)).fetchall()
        all_keys = conn.execute("SELECT * FROM ssh_keys WHERE user_id=?", (user['id'],)).fetchall()
        conn.close()
        return jsonify({'success': True, 'keys': [dict(k) for k in keys], 'all_keys': [dict(k) for k in all_keys]})
    key_id = request.form.get('key_id', type=int)
    if not key_id:
        conn.close()
        return jsonify({'success': False, 'error': 'Key ID required'})
    existing = conn.execute("SELECT id FROM vm_ssh_keys WHERE vm_uuid=? AND ssh_key_id=?", (uuid, key_id)).fetchone()
    if existing:
        conn.execute("DELETE FROM vm_ssh_keys WHERE id=?", (existing['id'],))
    else:
        conn.execute("INSERT INTO vm_ssh_keys (vm_uuid, ssh_key_id) VALUES (?,?)", (uuid, key_id))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'SSH key association updated'})


@app.route('/api/startup-scripts', methods=['GET'])
@login_required
def api_list_startup_scripts():
    conn = get_db()
    scripts = conn.execute("SELECT * FROM startup_scripts WHERE user_id=?", (session['user_id'],)).fetchall()
    conn.close()
    return jsonify({'success': True, 'scripts': [dict(s) for s in scripts]})


@app.route('/api/startup-scripts/create', methods=['POST'])
@login_required
def api_create_startup_script():
    name = request.form.get('name', '').strip()
    content = request.form.get('content', '').strip()
    if not name or not content:
        return jsonify({'success': False, 'error': 'Name and content required'})
    conn = get_db()
    conn.execute("INSERT INTO startup_scripts (user_id, name, content) VALUES (?,?,?)", (session['user_id'], name, content))
    conn.commit()
    script_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.close()
    return jsonify({'success': True, 'message': 'Script created', 'script_id': script_id})


@app.route('/api/startup-scripts/<int:sid>/delete', methods=['POST'])
@login_required
def api_delete_startup_script(sid):
    conn = get_db()
    conn.execute("DELETE FROM startup_scripts WHERE id=? AND user_id=?", (sid, session['user_id']))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Script deleted'})


@app.route('/api/vm/<uuid>/startup-script', methods=['POST'])
@login_required
def api_set_startup_script(uuid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    script_id = request.form.get('script_id', type=int, default=0)
    conn.execute("UPDATE vms SET startup_script_id=? WHERE uuid=?", (script_id, uuid))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Startup script assigned'})


@app.route('/api/vm/<uuid>/execute-script', methods=['POST'])
@login_required
def api_execute_script(uuid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    script = request.form.get('script', '').strip()
    if not script:
        return jsonify({'success': False, 'error': 'Script content required'})
    if not is_vm_running(uuid):
        conn.close()
        return jsonify({'success': False, 'error': 'VM must be running to execute scripts'})
    threading.Thread(target=run_ssh_command, args=(uuid, script), daemon=True).start()
    log_activity(user['id'], f'executed script on VM {uuid}')
    conn.close()
    return jsonify({'success': True, 'message': 'Script execution started'})


@app.route('/api/vm/<uuid>/save-template', methods=['POST'])
@login_required
def api_save_template(uuid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Template name required'})
    conn.execute("""INSERT INTO templates (name, user_id, source_vm_uuid, os_type, os_version, cpus, ram, disk_size, disk_gb)
                    VALUES (?,?,?,?,?,?,?,?,?)""",
                 (name, user['id'], uuid, vm['os_type'], vm['os_version'], vm['cpus'], vm['ram'], vm['disk_size'], vm['disk_gb']))
    conn.commit(); conn.close()
    log_activity(user['id'], f'saved VM as template: {name}')
    return jsonify({'success': True, 'message': 'Template saved'})


@app.route('/api/templates', methods=['GET'])
@login_required
def api_list_templates():
    conn = get_db()
    rows = conn.execute("SELECT * FROM templates WHERE user_id=? OR user_id IN (SELECT id FROM users WHERE parent_id=?)", (session['user_id'], session['user_id'])).fetchall()
    conn.close()
    return jsonify({'success': True, 'templates': [dict(r) for r in rows]})


@app.route('/api/templates/<int:tid>/delete', methods=['POST'])
@login_required
def api_delete_template(tid):
    conn = get_db()
    conn.execute("DELETE FROM templates WHERE id=?", (tid,))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Template deleted'})


@app.route('/api/templates/<int:tid>/deploy', methods=['POST'])
@login_required
def api_deploy_template(tid):
    user = get_current_user()
    conn = get_db()
    template = conn.execute("SELECT * FROM templates WHERE id=?", (tid,)).fetchone()
    if not template:
        conn.close()
        return jsonify({'success': False, 'error': 'Template not found'}), 404
    new_name = request.form.get('name', template['name'] + ' (deployed)')
    new_uuid = generate_uuid()
    new_img = os.path.join(VM_DIR, f'{new_uuid}.qcow2')
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', new_img, str(template['disk_gb']) + 'G'], check=True, timeout=60)
    conn.execute("""INSERT INTO vms (uuid, name, user_id, os_type, os_version, cpus, ram, disk_size, disk_gb, ssh_port, status, img_file, notes)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                 (new_uuid, new_name, user['id'], template['os_type'], template['os_version'],
                  template['cpus'], template['ram'], template['disk_size'], template['disk_gb'],
                  get_available_port(), 'stopped', new_img, f'Deployed from template: {template["name"]}'))
    conn.commit(); conn.close()
    log_activity(user['id'], f'deployed VM from template: {template["name"]}')
    return jsonify({'success': True, 'message': 'VM deployed from template', 'new_uuid': new_uuid})


@app.route('/api/vm/<uuid>/storage', methods=['GET'])
@login_required
def api_list_storage(uuid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    disks = conn.execute("SELECT * FROM additional_storage WHERE vm_uuid=?", (uuid,)).fetchall()
    conn.close()
    return jsonify({'success': True, 'storage': [dict(d) for d in disks]})


@app.route('/api/vm/<uuid>/storage/add', methods=['POST'])
@login_required
def api_add_storage(uuid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    size_gb = int(request.form.get('size_gb', 10))
    mount_point = request.form.get('mount_point', '/mnt/storage').strip()
    if size_gb < 1 or size_gb > 500:
        return jsonify({'success': False, 'error': 'Size must be 1-500 GB'})
    disk_path = os.path.join(VM_DIR, f'{uuid}-disk-{int(time.time())}.qcow2')
    subprocess.run(['qemu-img', 'create', '-f', 'qcow2', disk_path, f'{size_gb}G'], check=True, timeout=60)
    conn.execute("INSERT INTO additional_storage (vm_uuid, file_path, size_gb, mount_point) VALUES (?,?,?,?)", (uuid, disk_path, size_gb, mount_point))
    conn.commit(); conn.close()
    log_activity(user['id'], f'added {size_gb}GB storage to VM {uuid}')
    return jsonify({'success': True, 'message': f'{size_gb}GB storage added'})


@app.route('/api/vm/<uuid>/storage/<int:sid>/delete', methods=['POST'])
@login_required
def api_delete_storage(uuid, sid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    disk = conn.execute("SELECT * FROM additional_storage WHERE id=? AND vm_uuid=?", (sid, uuid)).fetchone()
    if disk and os.path.exists(disk['file_path']):
        os.remove(disk['file_path'])
    conn.execute("DELETE FROM additional_storage WHERE id=?", (sid,))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Storage removed'})


@app.route('/api/notifications', methods=['GET'])
@login_required
def api_list_notifications():
    conn = get_db()
    rows = conn.execute("SELECT * FROM notifications WHERE user_id=?", (session['user_id'],)).fetchall()
    conn.close()
    return jsonify({'success': True, 'notifications': [dict(r) for r in rows]})


@app.route('/api/notifications/create', methods=['POST'])
@login_required
def api_create_notification():
    channel = request.form.get('channel', 'email')
    label = request.form.get('label', '').strip()
    config = json.dumps({k: v for k, v in request.form.items() if k not in ('channel', 'label')})
    conn = get_db()
    conn.execute("INSERT INTO notifications (user_id, channel, label, config) VALUES (?,?,?,?)", (session['user_id'], channel, label, config))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Notification channel added'})


@app.route('/api/notifications/<int:nid>/delete', methods=['POST'])
@login_required
def api_delete_notification(nid):
    conn = get_db()
    conn.execute("DELETE FROM notifications WHERE id=? AND user_id=?", (nid, session['user_id']))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Notification channel deleted'})


@app.route('/api/notification-rules', methods=['GET'])
@login_required
def api_list_notification_rules():
    conn = get_db()
    rows = conn.execute("""SELECT nr.*, n.channel, n.label FROM notification_rules nr
        JOIN notifications n ON nr.notification_id=n.id
        WHERE nr.user_id=?""", (session['user_id'],)).fetchall()
    conn.close()
    return jsonify({'success': True, 'rules': [dict(r) for r in rows]})


@app.route('/api/notification-rules/create', methods=['POST'])
@login_required
def api_create_notification_rule():
    event_type = request.form.get('event_type', 'vm_down')
    notification_id = int(request.form.get('notification_id', 0))
    vm_uuid = request.form.get('vm_uuid', '')
    conn = get_db()
    conn.execute("INSERT INTO notification_rules (user_id, event_type, notification_id, vm_uuid) VALUES (?,?,?,?)", (session['user_id'], event_type, notification_id, vm_uuid))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Rule created'})


@app.route('/api/notification-rules/<int:rid>/delete', methods=['POST'])
@login_required
def api_delete_notification_rule(rid):
    conn = get_db()
    conn.execute("DELETE FROM notification_rules WHERE id=? AND user_id=?", (rid, session['user_id']))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Rule deleted'})


@app.route('/api/vm/<uuid>/alerts', methods=['GET', 'POST'])
@login_required
def api_vm_alerts(uuid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    conn = get_db()
    if request.method == 'GET':
        alerts = conn.execute("SELECT * FROM resource_alerts WHERE vm_uuid=?", (uuid,)).fetchall()
        conn.close()
        return jsonify({'success': True, 'alerts': [dict(a) for a in alerts]})
    metric = request.form.get('metric', 'cpu')
    threshold = float(request.form.get('threshold', 90))
    enabled = int(request.form.get('enabled', 1))
    existing = conn.execute("SELECT id FROM resource_alerts WHERE vm_uuid=? AND metric=?", (uuid, metric)).fetchone()
    if existing:
        conn.execute("UPDATE resource_alerts SET threshold=?, enabled=? WHERE id=?", (threshold, enabled, existing['id']))
    else:
        conn.execute("INSERT INTO resource_alerts (vm_uuid, metric, threshold, enabled) VALUES (?,?,?,?)", (uuid, metric, threshold, enabled))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Alert saved'})


@app.route('/api/vm/<uuid>/alerts/<int:aid>/delete', methods=['POST'])
@login_required
def api_delete_alert(uuid, aid):
    conn = get_db()
    conn.execute("DELETE FROM resource_alerts WHERE id=? AND vm_uuid=?", (aid, uuid))
    conn.commit(); conn.close()
    return jsonify({'success': True, 'message': 'Alert deleted'})


@app.route('/api/vm/<uuid>/backup/<int:sid>/download')
@login_required
def api_download_backup(uuid, sid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    snap = conn.execute("SELECT * FROM snapshots WHERE id=? AND vm_uuid=?", (sid, uuid)).fetchone()
    conn.close()
    if not snap:
        return jsonify({'success': False, 'error': 'Backup not found'}), 404
    if not snap['file_path'] or not os.path.exists(snap['file_path']):
        return jsonify({'success': False, 'error': 'Backup file not found'}), 404
    return send_file(snap['file_path'], as_attachment=True, download_name=f'backup-{uuid}-{snap["name"]}.qcow2')


@app.route('/api/vm/<uuid>/backup/<int:sid>/restore', methods=['POST'])
@login_required
def api_restore_backup(uuid, sid):
    result = _ownership_vm(uuid)
    if result[0] is None:
        return result[1], result[2]
    user, conn, vm = result
    if is_vm_running(uuid):
        conn.close()
        return jsonify({'success': False, 'error': 'VM must be stopped to restore'})
    snap = conn.execute("SELECT * FROM snapshots WHERE id=? AND vm_uuid=?", (sid, uuid)).fetchone()
    if not snap:
        conn.close()
        return jsonify({'success': False, 'error': 'Backup not found'}), 404
    if not snap['file_path'] or not os.path.exists(snap['file_path']):
        conn.close()
        return jsonify({'success': False, 'error': 'Backup file not found'}), 404
    try:
        shutil.copy2(snap['file_path'], vm['img_file'])
        conn.close()
        return jsonify({'success': True, 'message': 'Backup restored'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/vm/<uuid>/status')
@login_required
def api_vm_status(uuid):
    running = is_vm_running(uuid)
    return jsonify({'running': running, 'uuid': uuid})


@app.route('/api/vm/mass', methods=['POST'])
@login_required
@role_required('admin')
def api_vm_mass():
    user = get_current_user()
    data = request.get_json(silent=True) or {}
    action = data.get('action', '')
    uuids = data.get('uuids', [])
    if action not in ('start', 'stop', 'restart', 'delete'):
        return jsonify({'success': False, 'error': 'Invalid action'}), 400
    if not uuids or not isinstance(uuids, list):
        return jsonify({'success': False, 'error': 'uuids must be a non-empty array'}), 400
    conn = get_db()
    results = []
    for uuid in uuids:
        try:
            if action == 'start':
                row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
                if not row:
                    results.append({'uuid': uuid, 'success': False, 'error': 'VM not found'})
                    continue
                vm = dict(row)
                if is_vm_running(uuid):
                    results.append({'uuid': uuid, 'success': False, 'error': 'Already running'})
                    continue
                if vm.get('suspended'):
                    results.append({'uuid': uuid, 'success': False, 'error': 'VM is suspended'})
                    continue
                ok = _start_vm_internal(uuid, conn)
                results.append({'uuid': uuid, 'success': ok, 'error': None if ok else 'Failed to start'})
                if ok:
                    log_activity(user['id'], f'[mass] started VM: {vm["name"]}')
            elif action == 'stop':
                subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
                vm_row = conn.execute("SELECT ws_port, name FROM vms WHERE uuid=?", (uuid,)).fetchone()
                if vm_row:
                    subprocess.run(['pkill', '-f', f'websockify.*{vm_row["ws_port"]}'], capture_output=True, timeout=10)
                time.sleep(1)
                conn.execute("UPDATE vms SET status='stopped',vnc_port=NULL,ws_port=NULL WHERE uuid=?", (uuid,))
                conn.commit()
                results.append({'uuid': uuid, 'success': True, 'error': None})
                if vm_row:
                    log_activity(user['id'], f'[mass] stopped VM: {vm_row["name"]}')
            elif action == 'restart':
                subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
                vm_row = conn.execute("SELECT ws_port, name FROM vms WHERE uuid=?", (uuid,)).fetchone()
                if vm_row:
                    subprocess.run(['pkill', '-f', f'websockify.*{vm_row["ws_port"]}'], capture_output=True, timeout=10)
                time.sleep(2)
                row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
                if not row:
                    results.append({'uuid': uuid, 'success': False, 'error': 'VM not found'})
                    continue
                vm = dict(row)
                if vm.get('suspended'):
                    results.append({'uuid': uuid, 'success': False, 'error': 'VM is suspended'})
                    continue
                ok = _start_vm_internal(uuid, conn)
                results.append({'uuid': uuid, 'success': ok, 'error': None if ok else 'Failed to restart'})
                if ok:
                    log_activity(user['id'], f'[mass] restarted VM: {vm["name"]}')
            elif action == 'delete':
                subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
                vm_row = conn.execute("SELECT img_file, seed_file, ws_port, name FROM vms WHERE uuid=?", (uuid,)).fetchone()
                if vm_row:
                    subprocess.run(['pkill', '-f', f'websockify.*{vm_row["ws_port"]}'], capture_output=True, timeout=10)
                time.sleep(1)
                if vm_row:
                    for f in [vm_row['img_file'], vm_row['seed_file']]:
                        if f and os.path.exists(f):
                            os.remove(f)
                conn.execute("DELETE FROM snapshots WHERE vm_uuid=?", (uuid,))
                conn.execute("DELETE FROM firewall_rules WHERE vm_uuid=?", (uuid,))
                conn.execute("DELETE FROM iso_mounts WHERE vm_uuid=?", (uuid,))
                conn.execute("DELETE FROM backup_schedules WHERE vm_uuid=?", (uuid,))
                conn.execute("DELETE FROM monitoring_checks WHERE vm_uuid=?", (uuid,))
                conn.execute("DELETE FROM monitoring_logs WHERE check_id NOT IN (SELECT id FROM monitoring_checks) OR check_id IS NULL")
                conn.execute("DELETE FROM vms WHERE uuid=?", (uuid,))
                conn.commit()
                results.append({'uuid': uuid, 'success': True, 'error': None})
                log_activity(user['id'], f'[mass] deleted VM: {vm_row["name"] if vm_row else uuid}')
        except Exception as e:
            results.append({'uuid': uuid, 'success': False, 'error': str(e)})
    conn.close()
    return jsonify({'success': True, 'results': results})


@app.route('/api/vm/<uuid>/console')
@login_required
def api_vm_console(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        row = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        row = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    conn.close()
    if not row:
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    vm = dict(row)
    host = request.host.split(':')[0]
    vnc_port = vm.get('vnc_port', 5900)
    ws_port = vnc_port + 10000
    return jsonify({
        'success': True,
        'running': is_vm_running(uuid),
        'vnc_port': vnc_port,
        'websocket_url': f'ws://{host}:{ws_port}/vnc' if is_vm_running(uuid) else None
    })


# ========== API: SNAPSHOTS ==========

@app.route('/api/vm/<uuid>/snapshots')
@login_required
def api_vm_snapshots(uuid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        vm = conn.execute("SELECT id FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        vm = conn.execute("SELECT id FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        vm = conn.execute("SELECT id FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    rows = conn.execute("SELECT * FROM snapshots WHERE vm_uuid=? ORDER BY created_at DESC", (uuid,)).fetchall()
    conn.close()
    return jsonify({'success': True, 'snapshots': [dict(r) for r in rows]})


@app.route('/api/vm/<uuid>/snapshots/create', methods=['POST'])
@login_required
def api_create_snapshot(uuid):
    user = get_current_user()
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Snapshot name is required'})
    conn = get_db()
    if user['role'] == 'admin':
        vm = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        vm = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        vm = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    if is_vm_running(uuid):
        conn.close()
        return jsonify({'success': False, 'error': 'VM must be stopped to create a snapshot'})
    img_file = vm['img_file']
    if not img_file or not os.path.exists(img_file):
        conn.close()
        return jsonify({'success': False, 'error': 'VM disk file not found'})
    safe_name = re.sub(r'[^a-zA-Z0-9_\- ]', '', name)[:128]
    try:
        result = subprocess.run(['qemu-img', 'snapshot', '-c', safe_name, img_file],
                               capture_output=True, text=True, timeout=60)
        if result.returncode != 0:
            conn.close()
            return jsonify({'success': False, 'error': result.stderr.strip() or 'Snapshot creation failed'})
        size = os.path.getsize(img_file)
        conn.execute("INSERT INTO snapshots (vm_uuid, name, file_path, size) VALUES (?,?,?,?)",
                     (uuid, name, img_file, size))
        conn.commit()
        log_activity(user['id'], f'created snapshot: {name} for VM {uuid}')
        conn.close()
        return jsonify({'success': True, 'message': 'Snapshot created'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/vm/<uuid>/snapshots/<int:sid>/revert', methods=['POST'])
@login_required
def api_revert_snapshot(uuid, sid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        vm = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        vm = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        vm = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    if is_vm_running(uuid):
        conn.close()
        return jsonify({'success': False, 'error': 'VM must be stopped to revert to a snapshot'})
    snap = conn.execute("SELECT * FROM snapshots WHERE id=? AND vm_uuid=?", (sid, uuid)).fetchone()
    if not snap:
        conn.close()
        return jsonify({'success': False, 'error': 'Snapshot not found'}), 404
    img_file = vm['img_file']
    if not img_file or not os.path.exists(img_file):
        conn.close()
        return jsonify({'success': False, 'error': 'VM disk file not found'})
    try:
        result = subprocess.run(['qemu-img', 'snapshot', '-a', snap['name'], img_file],
                               capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            conn.close()
            return jsonify({'success': False, 'error': result.stderr.strip() or 'Snapshot revert failed'})
        log_activity(user['id'], f'reverted VM {uuid} to snapshot: {snap["name"]}')
        conn.close()
        return jsonify({'success': True, 'message': 'Snapshot reverted'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/vm/<uuid>/snapshots/<int:sid>/delete', methods=['POST'])
@login_required
def api_delete_snapshot(uuid, sid):
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        vm = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    elif user['role'] == 'reseller':
        vm = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id IN (SELECT id FROM users WHERE parent_id=?)", (uuid, user['id'])).fetchone()
    else:
        vm = conn.execute("SELECT * FROM vms WHERE uuid=? AND user_id=?", (uuid, user['id'])).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    if is_vm_running(uuid):
        conn.close()
        return jsonify({'success': False, 'error': 'VM must be stopped to delete a snapshot'})
    snap = conn.execute("SELECT * FROM snapshots WHERE id=? AND vm_uuid=?", (sid, uuid)).fetchone()
    if not snap:
        conn.close()
        return jsonify({'success': False, 'error': 'Snapshot not found'}), 404
    img_file = vm['img_file']
    try:
        if img_file and os.path.exists(img_file):
            subprocess.run(['qemu-img', 'snapshot', '-d', snap['name'], img_file],
                          capture_output=True, text=True, timeout=60)
        conn.execute("DELETE FROM snapshots WHERE id=?", (sid,))
        conn.commit()
        conn.close()
        log_activity(user['id'], f'deleted snapshot: {snap["name"]} for VM {uuid}')
        return jsonify({'success': True, 'message': 'Snapshot deleted'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})


# ========== API: REBUILD ==========

@app.route('/api/vm/<uuid>/rebuild', methods=['POST'])
@login_required
@role_required('admin', 'reseller')
def api_rebuild_vm(uuid):
    user = get_current_user()
    conn = get_db()
    vm = conn.execute("SELECT * FROM vms WHERE uuid=?", (uuid,)).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    subprocess.run(['pkill', '-f', f'vpanel-{uuid}'], capture_output=True, timeout=10)
    time.sleep(1)
    os_type = request.form.get('os_type', vm['os_type'])
    os_version = request.form.get('os_version', vm['os_version'])
    password = request.form.get('password', vm['password'])
    hostname = request.form.get('hostname', vm['hostname'])
    img_file = vm['img_file']
    seed_file = vm['seed_file']
    if os.path.exists(img_file):
        os.remove(img_file)
    conn.execute("UPDATE vms SET status='stopped',os_type=?,os_version=?,password=?,hostname=?,notes='Rebuilding...' WHERE uuid=?",
                 (os_type, os_version, password, hostname, uuid))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'rebuilding VM: {vm["name"]}')
    img_url = OS_IMAGES.get(os_type, {}).get(os_version, {}).get('url', '')
    threading.Thread(target=background_create_vm,
                     args=(uuid, img_url, img_file, seed_file, vm['disk_size'],
                           vm['username'], password, hostname, vm['name']),
                     daemon=True).start()
    return jsonify({'success': True, 'message': 'Rebuilding VM'})


# ========== API: BACKUP TOGGLE ==========

@app.route('/api/vm/<uuid>/backup/toggle', methods=['POST'])
@login_required
@role_required('admin', 'reseller')
def api_toggle_backup(uuid):
    conn = get_db()
    vm = conn.execute("SELECT backup_enabled FROM vms WHERE uuid=?", (uuid,)).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    new_val = 0 if vm['backup_enabled'] else 1
    conn.execute("UPDATE vms SET backup_enabled=? WHERE uuid=?", (new_val, uuid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'backup_enabled': bool(new_val)})


# ========== API: TRAFFIC ==========

@app.route('/api/vm/<uuid>/traffic')
@login_required
def api_vm_traffic(uuid):
    conn = get_db()
    vm = conn.execute("SELECT bandwidth_used, bandwidth_limit, name FROM vms WHERE uuid=?", (uuid,)).fetchone()
    conn.close()
    if not vm:
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    log_file = os.path.join(VM_DIR, f'{uuid}.log')
    traffic = 0
    if os.path.exists(log_file):
        try:
            result = subprocess.run(['grep', '-oP', r'traffic=\K\d+', log_file], capture_output=True, text=True, timeout=5)
            if result.stdout:
                traffic = int(result.stdout.strip().split('\n')[-1])
        except Exception:
            pass
    return jsonify({
        'success': True,
        'bandwidth_used': vm['bandwidth_used'],
        'bandwidth_limit': vm['bandwidth_limit'],
        'traffic_bytes': traffic,
        'usage_percent': min(int(vm['bandwidth_used'] / max(vm['bandwidth_limit'], 1) * 100), 100)
    })


# ========== API: INVOICES ==========

@app.route('/api/invoices/generate', methods=['POST'])
@login_required
@role_required('admin')
def api_generate_invoices():
    conn = get_db()
    vms = conn.execute("SELECT v.*,u.username FROM vms v JOIN users u ON v.user_id=u.id WHERE v.status='running'").fetchall()
    count = 0
    for vm in vms:
        vmd = dict(vm)
        amount = vmd['hourly_cost'] * 730
        inv_no = f"INV-{generate_uuid().upper()}"
        conn.execute("INSERT INTO invoices (invoice_no,user_id,amount,tax,total,type,description) VALUES (?,?,?,?,?,'monthly',?)",
                     (inv_no, vmd['user_id'], amount, amount * 0.18, amount * 1.18, f'VM {vmd["name"]} - Monthly'))
        count += 1
        user_row = conn.execute("SELECT email,username FROM users WHERE id=?", (vmd['user_id'],)).fetchone()
        if user_row and user_row['email']:
            send_email(user_row['email'], 'New Invoice',
                       f'Hi {user_row["username"]},\n\nInvoice {inv_no} for ${amount*1.18:.2f} has been generated.\n\nThanks,\nvpanel')
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': f'Generated {count} invoices'})


@app.route('/api/invoice/<inv_no>/pay', methods=['POST'])
@login_required
@role_required('admin')
def api_pay_invoice(inv_no):
    conn = get_db()
    conn.execute("UPDATE invoices SET status='paid',paid_at=CURRENT_TIMESTAMP WHERE invoice_no=?", (inv_no,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Marked as paid'})


@app.route('/api/invoice/create', methods=['POST'])
@login_required
@role_required('admin', 'reseller')
def api_create_invoice():
    user = get_current_user()
    uid = int(request.form.get('user_id', 0))
    amount = float(request.form.get('amount', 0))
    description = request.form.get('description', 'Invoice')
    promo_code = request.form.get('promo', '').strip()
    total = amount
    discount = 0
    if promo_code:
        conn = get_db()
        promo = conn.execute("SELECT * FROM promo_codes WHERE code=?", (promo_code.upper(),)).fetchone()
        if promo:
            p = dict(promo)
            if p['max_uses'] == 0 or p['used_count'] < p['max_uses']:
                if p['type'] == 'fixed':
                    discount = min(p['value'], total)
                else:
                    discount = total * p['value'] / 100
                total = round(total - discount, 2)
                conn.execute("UPDATE promo_codes SET used_count=used_count+1 WHERE id=?", (p['id'],))
                conn.commit()
        conn.close()
    inv_no = f"INV-{generate_uuid().upper()}"
    conn = get_db()
    conn.execute("INSERT INTO invoices (invoice_no,user_id,amount,tax,total,type,description) VALUES (?,?,?,?,?,'manual',?)",
                 (inv_no, uid, amount, 0, total, description))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'created invoice {inv_no} for user {uid}')
    return jsonify({'success': True, 'invoice_no': inv_no, 'amount': amount, 'discount': discount, 'total': total})


@app.route('/api/invoice/<inv_no>/checkout', methods=['POST'])
@login_required
def api_invoice_checkout(inv_no):
    user = get_current_user()
    settings = get_settings()
    stripe_key = settings.get('stripe_secret_key', '')
    if not stripe_key:
        return jsonify({'success': False, 'error': 'Stripe not configured'})
    conn = get_db()
    inv = conn.execute("SELECT * FROM invoices WHERE invoice_no=? AND user_id=?", (inv_no, user['id'])).fetchone()
    conn.close()
    if not inv:
        return jsonify({'success': False, 'error': 'Invoice not found'}), 404
    try:
        import stripe
        stripe.api_key = stripe_key
        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': f'Invoice {inv_no}: {inv["description"]}'},
                    'unit_amount': int(inv['total'] * 100),
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=request.host_url + 'invoices?success=1',
            cancel_url=request.host_url + 'invoices?canceled=1',
            metadata={'invoice_no': inv_no}
        )
        conn = get_db()
        conn.execute("UPDATE invoices SET stripe_session_id=? WHERE invoice_no=?", (session.id, inv_no))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'url': session.url})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/stripe-webhook', methods=['POST'])
def stripe_webhook():
    settings = get_settings()
    endpoint_secret = settings.get('stripe_webhook_secret', '')
    payload = request.get_data()
    sig_header = request.headers.get('Stripe-Signature', '')
    try:
        import stripe
        stripe.api_key = settings.get('stripe_secret_key', '')
        event = stripe.Webhook.construct_event(payload, sig_header, endpoint_secret)
    except Exception:
        return jsonify(success=False), 400
    if event['type'] == 'checkout.session.completed':
        session = event['data']['object']
        inv_no = session.get('metadata', {}).get('invoice_no')
        if inv_no:
            conn = get_db()
            conn.execute("UPDATE invoices SET status='paid',paid_at=CURRENT_TIMESTAMP WHERE invoice_no=?", (inv_no,))
            conn.commit()
            conn.close()
    return jsonify(success=True), 200


# ========== API: TICKETS ==========

@app.route('/api/ticket/<uuid>/reply', methods=['POST'])
@login_required
def api_ticket_reply(uuid):
    user = get_current_user()
    message = request.form.get('message', '')
    if not message:
        return jsonify({'success': False, 'error': 'Message required'})
    conn = get_db()
    ticket = conn.execute("SELECT * FROM tickets WHERE uuid=?", (uuid,)).fetchone()
    if not ticket:
        conn.close()
        return jsonify({'success': False, 'error': 'Ticket not found'})
    conn.execute("INSERT INTO ticket_replies (ticket_id,user_id,message) VALUES (?,?,?)", (ticket['id'], user['id'], message))
    conn.execute("UPDATE tickets SET status='in_progress' WHERE id=? AND status='open'", (ticket['id'],))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'replied to ticket {uuid}')
    return jsonify({'success': True, 'message': 'Reply added'})


@app.route('/api/ticket/<uuid>/close', methods=['POST'])
@login_required
def api_ticket_close(uuid):
    conn = get_db()
    conn.execute("UPDATE tickets SET status='closed' WHERE uuid=?", (uuid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Ticket closed'})


# ========== API: PLANS ==========

@app.route('/api/plan/create', methods=['POST'])
@login_required
@role_required('admin')
def api_create_plan():
    conn = get_db()
    conn.execute("INSERT INTO vm_plans (name,description,cpus,ram,disk,bandwidth,price_monthly,price_hourly) VALUES (?,?,?,?,?,?,?,?)",
                 (request.form['name'], request.form.get('description',''), int(request.form['cpus']),
                  int(request.form['ram']), int(request.form['disk']), int(request.form.get('bandwidth',1000)),
                  float(request.form['price_monthly']), float(request.form.get('price_hourly',0.01))))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Plan created'})


@app.route('/api/plan/<int:pid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def api_delete_plan(pid):
    conn = get_db()
    conn.execute("DELETE FROM vm_plans WHERE id=?", (pid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Plan deleted'})


# ========== API: USERS ==========

@app.route('/api/user/<int:uid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def api_delete_user(uid):
    conn = get_db()
    vms = conn.execute("SELECT uuid FROM vms WHERE user_id=?", (uid,)).fetchall()
    for v in vms:
        subprocess.run(['pkill', '-f', f'vpanel-{v["uuid"]}'], capture_output=True, timeout=10)
    conn.execute("DELETE FROM vms WHERE user_id=?", (uid,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'User deleted'})


# ========== API: NODES ==========

@app.route('/api/node/create', methods=['POST'])
@login_required
@role_required('admin')
def api_create_node():
    conn = get_db()
    try:
        conn.execute("INSERT INTO nodes (name,hostname,ip_address,total_cpus,total_ram,total_disk) VALUES (?,?,?,?,?,?)",
                     (request.form['name'], request.form['hostname'], request.form.get('ip_address',''),
                      int(request.form['total_cpus']), int(request.form['total_ram']), int(request.form['total_disk'])))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Node created'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/node/<int:nid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def api_delete_node(nid):
    conn = get_db()
    vms = conn.execute("SELECT COUNT(*) as c FROM vms WHERE node_id=?", (nid,)).fetchone()['c']
    if vms > 0:
        conn.close()
        return jsonify({'success': False, 'error': f'{vms} VMs still assigned to this node'})
    conn.execute("DELETE FROM nodes WHERE id=?", (nid,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': 'Node deleted'})


# ========== PROMO CODES ==========

@app.route('/api/promos')
@login_required
@role_required('admin')
def api_list_promos():
    conn = get_db()
    rows = conn.execute("SELECT * FROM promo_codes ORDER BY created_at DESC").fetchall()
    conn.close()
    return jsonify({'success': True, 'promos': [dict(r) for r in rows]})


@app.route('/api/promos/create', methods=['POST'])
@login_required
@role_required('admin')
def api_create_promo():
    code = request.form.get('code', '').strip().upper()
    ptype = request.form.get('type', 'percentage')
    value = float(request.form.get('value', 0))
    min_amount = float(request.form.get('min_amount', 0))
    max_uses = int(request.form.get('max_uses', 0))
    expires_at = request.form.get('expires_at', '')
    conn = get_db()
    try:
        conn.execute("INSERT INTO promo_codes (code,type,value,min_amount,max_uses,expires_at) VALUES (?,?,?,?,?,?)",
                     (code, ptype, value, min_amount, max_uses, expires_at or None))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Promo code created'})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/promo/validate', methods=['POST'])
@login_required
def api_validate_promo():
    code = request.form.get('code', '').strip().upper()
    amount = float(request.form.get('amount', 0))
    conn = get_db()
    promo = conn.execute("SELECT * FROM promo_codes WHERE code=?", (code,)).fetchone()
    conn.close()
    if not promo:
        return jsonify({'success': False, 'error': 'Invalid promo code'})
    p = dict(promo)
    if p['max_uses'] > 0 and p['used_count'] >= p['max_uses']:
        return jsonify({'success': False, 'error': 'Promo code has reached its usage limit'})
    if p['expires_at']:
        from datetime import datetime
        exp = datetime.strptime(p['expires_at'], '%Y-%m-%d %H:%M:%S') if ' ' in p['expires_at'] else datetime.strptime(p['expires_at'], '%Y-%m-%d')
        if exp < datetime.now():
            return jsonify({'success': False, 'error': 'Promo code has expired'})
    if amount < p['min_amount']:
        return jsonify({'success': False, 'error': f'Minimum amount of ${p["min_amount"]:.2f} required'})
    discount = p['value'] if p['type'] == 'fixed' else (amount * p['value'] / 100)
    discount = min(discount, amount)
    return jsonify({'success': True, 'promo': dict(p), 'discount': round(discount, 2), 'total': round(amount - discount, 2)})


@app.route('/promos')
@login_required
@role_required('admin')
def promos_page():
    user = get_current_user()
    template = open(Path(__file__).parent / 'templates' / 'promos.html').read()
    return render_template_string(template, title='Promo Codes', active_page='promos', current_user=user)


# ========== LOGIN HISTORY ==========

@app.route('/api/login-history')
@login_required
def api_login_history():
    user = get_current_user()
    conn = get_db()
    if user['role'] == 'admin':
        rows = conn.execute("SELECT * FROM login_history ORDER BY created_at DESC LIMIT 100").fetchall()
    else:
        rows = conn.execute("SELECT * FROM login_history WHERE user_id=? ORDER BY created_at DESC LIMIT 50", (user['id'],)).fetchall()
    conn.close()
    return jsonify({'success': True, 'history': [dict(r) for r in rows]})


@app.route('/api/login-history/all')
@login_required
@role_required('admin')
def api_login_history_all():
    conn = get_db()
    rows = conn.execute("SELECT * FROM login_history ORDER BY created_at DESC LIMIT 200").fetchall()
    conn.close()
    return jsonify({'success': True, 'history': [dict(r) for r in rows]})


# ========== API TOKENS ==========

@app.route('/api/tokens')
@login_required
def api_list_tokens():
    user = get_current_user()
    conn = get_db()
    rows = conn.execute("SELECT id, name, created_at, last_used_at FROM api_tokens WHERE user_id=? ORDER BY created_at DESC", (user['id'],)).fetchall()
    conn.close()
    return jsonify({'success': True, 'tokens': [dict(r) for r in rows]})


@app.route('/api/tokens/create', methods=['POST'])
@login_required
def api_create_token():
    user = get_current_user()
    name = request.form.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'error': 'Token name is required'})
    token = secrets.token_hex(32)
    conn = get_db()
    conn.execute("INSERT INTO api_tokens (user_id, name, token) VALUES (?,?,?)", (user['id'], name, token))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'created API token: {name}')
    return jsonify({'success': True, 'token': token, 'name': name})


@app.route('/api/tokens/<int:tok_id>/delete', methods=['POST'])
@login_required
def api_delete_token(tok_id):
    user = get_current_user()
    conn = get_db()
    row = conn.execute("SELECT * FROM api_tokens WHERE id=? AND user_id=?", (tok_id, user['id'])).fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'error': 'Token not found'}), 404
    conn.execute("DELETE FROM api_tokens WHERE id=?", (tok_id,))
    conn.commit()
    conn.close()
    log_activity(user['id'], f'deleted API token: {row["name"]}')
    return jsonify({'success': True, 'message': 'Token deleted'})


@app.route('/api/ping')
@api_token_required
def api_ping():
    return jsonify({'success': True, 'message': 'pong'})


# ========== MONITORING ==========

@app.route('/api/vm/<uuid>/monitoring')
@login_required
def api_vm_monitoring(uuid):
    conn = get_db()
    check = conn.execute("SELECT * FROM monitoring_checks WHERE vm_uuid=?", (uuid,)).fetchone()
    if not check:
        conn.close()
        return jsonify({'success': True, 'monitoring': None, 'history': []})
    logs = conn.execute("SELECT * FROM monitoring_logs WHERE check_id=? ORDER BY created_at DESC LIMIT 50",
                        (check['id'],)).fetchall()
    conn.close()
    return jsonify({'success': True, 'monitoring': dict(check), 'history': [dict(l) for l in logs]})


@app.route('/api/vm/<uuid>/monitoring/toggle', methods=['POST'])
@login_required
@role_required('admin', 'reseller')
def api_toggle_monitoring(uuid):
    conn = get_db()
    check = conn.execute("SELECT * FROM monitoring_checks WHERE vm_uuid=?", (uuid,)).fetchone()
    if check:
        new_val = 0 if check['enabled'] else 1
        conn.execute("UPDATE monitoring_checks SET enabled=? WHERE id=?", (new_val, check['id']))
    else:
        create_monitor_check(uuid, 'tcp', None, 300, 10)
        new_val = 1
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'enabled': bool(new_val)})


@app.route('/api/vm/<uuid>/auto-start', methods=['POST'])
@login_required
@role_required('admin', 'reseller')
def api_toggle_autostart(uuid):
    conn = get_db()
    vm = conn.execute("SELECT auto_start FROM vms WHERE uuid=?", (uuid,)).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    auto_start = request.form.get('auto_start', '').strip()
    if auto_start == '1' or auto_start == 'true':
        conn.execute("UPDATE vms SET auto_start=1 WHERE uuid=?", (uuid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'auto_start': True, 'message': 'Auto-start enabled'})
    elif auto_start == '0' or auto_start == 'false':
        conn.execute("UPDATE vms SET auto_start=0 WHERE uuid=?", (uuid,))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'auto_start': False, 'message': 'Auto-start disabled'})
    else:
        # Toggle
        new_val = 0 if vm['auto_start'] else 1
        conn.execute("UPDATE vms SET auto_start=? WHERE uuid=?", (new_val, uuid))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'auto_start': bool(new_val)})


@app.route('/api/vm/<uuid>/persist', methods=['POST'])
@login_required
@role_required('admin', 'reseller')
def api_toggle_persist(uuid):
    conn = get_db()
    vm = conn.execute("SELECT persist_running FROM vms WHERE uuid=?", (uuid,)).fetchone()
    if not vm:
        conn.close()
        return jsonify({'success': False, 'error': 'VM not found'}), 404
    new_val = 0 if vm['persist_running'] else 1
    conn.execute("UPDATE vms SET persist_running=? WHERE uuid=?", (new_val, uuid))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'persist_running': bool(new_val)})


# ========== STATUS PAGE ==========

@app.route('/status')
def status_page():
    conn = get_db()
    total_vms = conn.execute("SELECT COUNT(*) as c FROM vms").fetchone()['c']
    running_vms = conn.execute("SELECT COUNT(*) as c FROM vms WHERE status='running'").fetchone()['c']
    stopped_vms = conn.execute("SELECT COUNT(*) as c FROM vms WHERE status='stopped'").fetchone()['c']
    suspended_vms = conn.execute("SELECT COUNT(*) as c FROM vms WHERE suspended=1").fetchone()['c']
    nodes = conn.execute("SELECT * FROM nodes").fetchall()
    recent_incidents = conn.execute("SELECT a.*,u.username FROM activity_log a LEFT JOIN users u ON a.user_id=u.id ORDER BY a.created_at DESC LIMIT 10").fetchall()
    total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()['c']
    s = get_settings()
    conn.close()
    import subprocess
    uptime_sec = 0
    try:
        uptime_sec = int(subprocess.run(['cat', '/proc/uptime'], capture_output=True, text=True).stdout.split()[0].split('.')[0])
    except Exception:
        pass
    cpu_load = 0
    try:
        cpu_load = round(float(open('/proc/loadavg').read().split()[0]), 2)
    except Exception:
        pass
    mem_info = {'total': 0, 'available': 0}
    try:
        for line in open('/proc/meminfo'):
            if 'MemTotal' in line: mem_info['total'] = int(line.split()[1]) // 1024
            if 'MemAvailable' in line: mem_info['available'] = int(line.split()[1]) // 1024
    except Exception:
        pass
    disk_info = {'total': 0, 'used': 0}
    try:
        df = subprocess.run(['df', '-BG', '/'], capture_output=True, text=True).stdout.split('\n')[1].split()
        disk_info['total'] = int(df[1].replace('G', ''))
        disk_info['used'] = int(df[2].replace('G', ''))
    except Exception:
        pass
    template = open(Path(__file__).parent / 'templates' / 'status.html').read()
    return render_template_string(template, title='System Status',
        total_vms=total_vms, running_vms=running_vms, stopped_vms=stopped_vms,
        suspended_vms=suspended_vms, nodes=[dict(n) for n in nodes],
        recent_incidents=[dict(a) for a in recent_incidents],
        total_users=total_users, uptime_sec=uptime_sec, cpu_load=cpu_load,
        mem_info=mem_info, disk_info=disk_info, settings=s)


# ========== KNOWLEDGE BASE ==========

@app.route('/knowledge-base')
def kb_list():
    conn = get_db()
    articles = conn.execute("SELECT * FROM kb_articles WHERE published=1 ORDER BY category, created_at DESC").fetchall()
    categories = conn.execute("SELECT DISTINCT category FROM kb_articles WHERE published=1 ORDER BY category").fetchall()
    conn.close()
    cats = [r['category'] for r in categories]
    arts = [dict(a) for a in articles]
    user = get_current_user()
    template = open(Path(__file__).parent / 'templates' / 'kb_list.html').read()
    return render_template_string(template, title='Knowledge Base', active_page='kb', current_user=user, articles=arts, categories=cats)


@app.route('/knowledge-base/<slug>')
def kb_view(slug):
    conn = get_db()
    article = conn.execute("SELECT * FROM kb_articles WHERE slug=? AND published=1", (slug,)).fetchone()
    conn.close()
    if not article:
        return "Article not found", 404
    user = get_current_user()
    template = open(Path(__file__).parent / 'templates' / 'kb_view.html').read()
    return render_template_string(template, title=article['title'], active_page='kb', current_user=user, article=dict(article))


@app.route('/admin/kb')
@login_required
@role_required('admin')
def kb_admin():
    user = get_current_user()
    conn = get_db()
    articles = conn.execute("SELECT a.*,u.username as author_name FROM kb_articles a LEFT JOIN users u ON a.author_id=u.id ORDER BY a.created_at DESC").fetchall()
    conn.close()
    template = open(Path(__file__).parent / 'templates' / 'kb_admin.html').read()
    return render_template_string(template, title='Manage KB', active_page='kb_admin', current_user=user, articles=[dict(a) for a in articles])


@app.route('/admin/kb/create', methods=['POST'])
@login_required
@role_required('admin')
def kb_create():
    user = get_current_user()
    title = request.form.get('title', '').strip()
    category = request.form.get('category', 'general').strip()
    content = request.form.get('content', '')
    slug = slugify(title)
    conn = get_db()
    try:
        conn.execute("INSERT INTO kb_articles (title,slug,category,content,author_id) VALUES (?,?,?,?,?)",
                     (title, slug, category, content, user['id']))
        conn.commit()
        conn.close()
        flash('Article created', 'success')
    except Exception as e:
        conn.close()
        flash(str(e), 'error')
    return redirect('/admin/kb')


@app.route('/admin/kb/<int:aid>/edit', methods=['POST'])
@login_required
@role_required('admin')
def kb_edit(aid):
    title = request.form.get('title', '').strip()
    category = request.form.get('category', 'general').strip()
    content = request.form.get('content', '')
    published = int(request.form.get('published', 1))
    slug = slugify(title)
    conn = get_db()
    conn.execute("UPDATE kb_articles SET title=?,slug=?,category=?,content=?,published=?,updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (title, slug, category, content, published, aid))
    conn.commit()
    conn.close()
    flash('Article updated', 'success')
    return redirect('/admin/kb')


@app.route('/admin/kb/<int:aid>/delete', methods=['POST'])
@login_required
@role_required('admin')
def kb_delete(aid):
    conn = get_db()
    conn.execute("DELETE FROM kb_articles WHERE id=?", (aid,))
    conn.commit()
    conn.close()
    flash('Article deleted', 'success')
    return redirect('/admin/kb')


# ========== THEME SWITCHER ==========

@app.route('/api/theme', methods=['POST'])
@login_required
def api_set_theme():
    theme = request.form.get('theme', 'dark')
    if theme not in ('dark', 'light'):
        theme = 'dark'
    conn = get_db()
    conn.execute("UPDATE users SET theme=? WHERE id=?", (theme, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'theme': theme})


# ========== BRANDING SETTINGS ==========

@app.route('/api/settings/branding', methods=['POST'])
@login_required
@role_required('admin')
def api_save_branding():
    conn = get_db()
    keys = ['site_name', 'site_logo', 'footer_text', 'default_theme', 'custom_css', 'branding_show']
    for key in keys:
        conn.execute("INSERT OR REPLACE INTO settings (key,value) VALUES (?,?)", (key, request.form.get(key, '')))
    conn.commit()
    conn.close()
    log_activity(session['user_id'], 'updated branding settings')
    return jsonify({'success': True, 'message': 'Branding settings saved'})


# ========== SUSPENSION DAEMON ==========

def suspension_check():
    while True:
        try:
            conn = get_db()
            expired = conn.execute("""
                SELECT v.*, u.email, u.username FROM vms v
                JOIN users u ON v.user_id=u.id
                WHERE v.status='running' AND (u.status='suspended' OR u.balance < 0 OR v.expiry_date < datetime('now'))
            """).fetchall()
            for vm in expired:
                vmd = dict(vm)
                subprocess.run(['pkill', '-f', f'vpanel-{vmd["uuid"]}'], capture_output=True, timeout=10)
                conn.execute("UPDATE vms SET status='stopped',suspended=1 WHERE uuid=?", (vmd['uuid'],))
                log_activity(None, f'auto-suspended VM: {vmd["name"]} (user {vmd["username"]})')
                if vmd['email']:
                    send_email(vmd['email'], 'VM Suspended',
                               f'Hi {vmd["username"]},\n\nYour VM "{vmd["name"]}" has been suspended due to insufficient balance or expired term.\n\nPlease top up your account.\n\nThanks,\nvpanel')
            conn.commit()
            conn.close()
        except Exception:
            try: conn.close()
            except: pass
        time.sleep(60)


suspend_thread = threading.Thread(target=suspension_check, daemon=True)
suspend_thread.start()

backup_thread = threading.Thread(target=backup_scheduler, daemon=True)
backup_thread.start()

monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
monitor_thread.start()

def auto_detect_loop():
    """Sync database VM status with actual QEMU process state every 30s."""
    while True:
        try:
            conn = get_db()
            # Fix VMs marked running but no QEMU process
            stale = conn.execute("SELECT uuid, vnc_port, ws_port FROM vms WHERE status='running'").fetchall()
            for vm in stale:
                if not is_vm_running(vm['uuid']):
                    subprocess.run(['pkill', '-f', f'websockify.*{vm["ws_port"]}'], capture_output=True, timeout=10)
                    conn.execute("UPDATE vms SET status='stopped',vnc_port=NULL,ws_port=NULL WHERE uuid=?", (vm['uuid'],))
                    log_activity(None, f'auto-detected stopped VM: {vm["uuid"]}')
            # Also detect orphaned QEMU processes without running VMs
            running_uuids = {vm['uuid'] for vm in stale if is_vm_running(vm['uuid'])}
            pgrep = subprocess.run(['pgrep', '-af', 'vpanel-'], capture_output=True, text=True, timeout=5)
            for line in pgrep.stdout.split('\n'):
                if 'vpanel-' not in line: continue
                m = __import__('re').search(r'vpanel-([a-f0-9-]+)', line)
                if m and m.group(1) not in running_uuids:
                    subprocess.run(['pkill', '-f', f'websockify.*{m.group(1)}'], capture_output=True, timeout=10)
                    conn.execute("UPDATE vms SET status='stopped',vnc_port=NULL,ws_port=NULL WHERE uuid=?", (m.group(1),))
            conn.commit()
            conn.close()
        except Exception:
            try: conn.close()
            except: pass
        time.sleep(30)

auto_detect_thread = threading.Thread(target=auto_detect_loop, daemon=True)
auto_detect_thread.start()

# Restore persisted running VMs (vms with persist_running=1 that were running before restart)
def restore_persisted_vms():
    time.sleep(10)  # Wait for everything to initialize
    try:
        conn = get_db()
        vms = conn.execute("SELECT uuid, name FROM vms WHERE persist_running=1 AND auto_start=1 AND suspended=0").fetchall()
        for vm in vms:
            if not is_vm_running(vm['uuid']):
                try:
                    _start_vm_internal(vm['uuid'])
                    log_activity(0, f'auto-restored VM: {vm["name"]}')
                except Exception:
                    pass
        conn.close()
    except Exception:
        pass

restore_thread = threading.Thread(target=restore_persisted_vms, daemon=True)
restore_thread.start()


# ========== RUN ==========

def run(host='0.0.0.0', port=8080):
    print(f"\033[1;32m============================================\033[0m")
    print(f"\033[1;32m  vPanel Reseller System v2.0\033[0m")
    print(f"\033[1;32m  Running on http://{host}:{port}\033[0m")
    print(f"\033[1;32m============================================\033[0m")
    print(f"\033[1;34m[ℹ] Default login: admin / admin@123\033[0m")
    app.run(host=host, port=port, debug=False, threaded=True)
