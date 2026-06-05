import os
import json
import threading
import time
import base64
import functools

from flask import Flask, render_template_string, request, jsonify, redirect, session, url_for, send_from_directory

from .config import VM_DIR, WEB_DIR, PIDS_DIR
from .vm_manager import VMManager, VMConfig

app = Flask(__name__)
app.secret_key = os.urandom(32).hex()

vm_manager = VMManager()

LOGIN_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
    <title>VM Manager Login</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
            padding: 20px;
        }
        .login-box {
            background: white;
            border-radius: 20px;
            box-shadow: 0 20px 60px rgba(0,0,0,0.3);
            padding: 40px;
            max-width: 400px;
            width: 100%;
        }
        h1 { color: #333; text-align: center; margin-bottom: 10px; }
        .subtitle { color: #667eea; text-align: center; margin-bottom: 30px; font-weight: 600; }
        .lock-icon { text-align: center; font-size: 48px; margin-bottom: 20px; }
        .form-group { margin-bottom: 20px; }
        label { display: block; color: #555; font-weight: 600; margin-bottom: 8px; }
        input {
            width: 100%; padding: 12px 15px;
            border: 2px solid #e0e0e0; border-radius: 8px;
            font-size: 16px; transition: border-color 0.3s;
        }
        input:focus { outline: none; border-color: #667eea; }
        button {
            width: 100%; padding: 15px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            color: white; border: none; border-radius: 8px;
            font-size: 16px; font-weight: 600; cursor: pointer;
            transition: transform 0.2s;
        }
        button:hover { transform: translateY(-2px); }
        .error { color: #dc2626; text-align: center; margin-bottom: 15px; display: none; }
        .footer { text-align: center; margin-top: 20px; color: #666; font-size: 14px; }
    </style>
</head>
<body>
    <div class="login-box">
        <div class="lock-icon">🔒</div>
        <h1>VM Manager</h1>
        <div class="subtitle">Authentication Required</div>
        <div class="error" id="error">{{ error }}</div>
        <form method="POST">
            <div class="form-group">
                <label>Username</label>
                <input type="text" name="username" required autofocus>
            </div>
            <div class="form-group">
                <label>Password</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit">Access Dashboard</button>
        </form>
        <div class="footer">Hopingboyz VM Manager</div>
    </div>
</body>
</html>
"""

DASHBOARD_TEMPLATE = """\
<!DOCTYPE html>
<html>
<head>
    <title>VM Manager Dashboard</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }
        .header {
            background: white;
            border-radius: 20px;
            padding: 25px 30px;
            margin-bottom: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .header h1 { color: #333; font-size: 24px; }
        .header .sub { color: #667eea; font-size: 14px; }
        .header-actions { display: flex; gap: 10px; align-items: center; }
        .btn {
            padding: 10px 20px; border: none; border-radius: 8px;
            font-size: 14px; font-weight: 600; cursor: pointer;
            transition: all 0.3s; text-decoration: none; display: inline-block;
        }
        .btn:hover { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(0,0,0,0.2); }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); color: white; }
        .btn-danger { background: linear-gradient(135deg, #ef4444, #dc2626); color: white; }
        .btn-success { background: linear-gradient(135deg, #10b981, #059669); color: white; }
        .btn-warning { background: linear-gradient(135deg, #f59e0b, #d97706); color: white; }
        .btn-sm { padding: 6px 12px; font-size: 12px; }
        .btn-logout { background: #ef4444; color: white; }
        .stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }
        .stat-card {
            background: white;
            border-radius: 15px;
            padding: 20px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            text-align: center;
        }
        .stat-card .number { font-size: 32px; font-weight: 700; color: #667eea; }
        .stat-card .label { color: #666; font-size: 14px; margin-top: 5px; }
        .vms-grid {
            display: grid;
            grid-template-columns: repeat(auto-fill, minmax(350px, 1fr));
            gap: 20px;
        }
        .vm-card {
            background: white;
            border-radius: 15px;
            padding: 25px;
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
            transition: all 0.3s;
        }
        .vm-card:hover { transform: translateY(-3px); box-shadow: 0 8px 25px rgba(0,0,0,0.15); }
        .vm-card .header-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 15px;
        }
        .vm-card .vm-name { font-size: 18px; font-weight: 700; color: #333; }
        .vm-card .status-badge {
            padding: 4px 12px; border-radius: 20px;
            font-size: 12px; font-weight: 600;
        }
        .status-running { background: #d1fae5; color: #065f46; }
        .status-stopped { background: #fee2e2; color: #991b1b; }
        .vm-card .detail-row {
            display: flex; justify-content: space-between;
            padding: 8px 0; border-bottom: 1px solid #f0f0f0;
            font-size: 14px;
        }
        .vm-card .detail-row:last-child { border-bottom: none; }
        .vm-card .detail-label { color: #666; }
        .vm-card .detail-value { color: #333; font-weight: 500; }
        .vm-card .actions {
            display: flex; gap: 8px; margin-top: 15px;
            flex-wrap: wrap;
        }
        .empty-state {
            background: white;
            border-radius: 20px;
            padding: 60px;
            text-align: center;
            grid-column: 1 / -1;
        }
        .empty-state .icon { font-size: 64px; margin-bottom: 20px; }
        .empty-state h2 { color: #333; margin-bottom: 10px; }
        .empty-state p { color: #666; margin-bottom: 20px; }
        .modal-overlay {
            display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0;
            background: rgba(0,0,0,0.5); z-index: 1000;
            justify-content: center; align-items: center;
        }
        .modal-overlay.active { display: flex; }
        .modal {
            background: white; border-radius: 20px; padding: 30px;
            max-width: 600px; width: 100%; max-height: 80vh; overflow-y: auto;
        }
        .modal h2 { margin-bottom: 20px; color: #333; }
        .modal .form-group { margin-bottom: 15px; }
        .modal label { display: block; color: #555; font-weight: 600; margin-bottom: 5px; font-size: 14px; }
        .modal input, .modal select {
            width: 100%; padding: 10px 12px;
            border: 2px solid #e0e0e0; border-radius: 8px; font-size: 14px;
        }
        .modal input:focus, .modal select:focus { outline: none; border-color: #667eea; }
        .modal .btn-row { display: flex; gap: 10px; margin-top: 20px; }
        .modal .btn-row .btn { flex: 1; text-align: center; }
        .toast {
            position: fixed; bottom: 20px; right: 20px;
            padding: 15px 25px; border-radius: 10px;
            color: white; font-weight: 600;
            z-index: 2000; display: none;
            animation: slideIn 0.3s ease;
        }
        .toast.success { background: #10b981; }
        .toast.error { background: #ef4444; }
        .toast.info { background: #3b82f6; }
        @keyframes slideIn {
            from { transform: translateX(100%); opacity: 0; }
            to { transform: translateX(0); opacity: 1; }
        }
        .inline-flex { display: inline-flex; align-items: center; gap: 5px; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <h1>🖥️ VM Manager</h1>
            <div class="sub">POWERED BY HOPINGBOYZ</div>
        </div>
        <div class="header-actions">
            <button class="btn btn-primary" onclick="openCreateModal()">+ New VM</button>
            <button class="btn btn-logout" onclick="logout()">Logout</button>
        </div>
    </div>

    <div class="stats">
        <div class="stat-card">
            <div class="number">{{ stats.total }}</div>
            <div class="label">Total VMs</div>
        </div>
        <div class="stat-card">
            <div class="number" style="color: #10b981;">{{ stats.running }}</div>
            <div class="label">Running</div>
        </div>
        <div class="stat-card">
            <div class="number" style="color: #ef4444;">{{ stats.stopped }}</div>
            <div class="label">Stopped</div>
        </div>
        <div class="stat-card">
            <div class="number">{{ stats.kvm }}</div>
            <div class="label">KVM Status</div>
        </div>
    </div>

    <div class="vms-grid">
        {% if vms %}
        {% for vm in vms %}
        <div class="vm-card" id="vm-{{ vm.name }}">
            <div class="header-row">
                <span class="vm-name">{{ vm.name }}</span>
                <span class="status-badge status-{{ 'running' if vm.running else 'stopped' }}">
                    {{ '🟢 Running' if vm.running else '🔴 Stopped' }}
                </span>
            </div>
            <div class="detail-row">
                <span class="detail-label">OS</span>
                <span class="detail-value">{{ vm.os_type }}/{{ vm.codename }}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">CPU</span>
                <span class="detail-value">{{ vm.cpus }} cores ({{ vm.cpu_model[:25] }})</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">Memory</span>
                <span class="detail-value">{{ vm.memory }} MB</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">SSH Port</span>
                <span class="detail-value">{{ vm.ssh_port }}</span>
            </div>
            <div class="detail-row">
                <span class="detail-label">VNC Port</span>
                <span class="detail-value">{{ vm.vnc_port or 'Auto' }}</span>
            </div>
            {% if vm.running %}
            <div class="actions">
                <button class="btn btn-sm btn-success" onclick="controlVM('{{ vm.name }}', 'stop')">⏹ Stop</button>
                <button class="btn btn-sm btn-warning" onclick="controlVM('{{ vm.name }}', 'restart')">🔄 Restart</button>
                <button class="btn btn-sm btn-primary" onclick="window.open('/vnc/{{ vm.name }}', '_blank')">🖱 VNC</button>
            </div>
            {% else %}
            <div class="actions">
                <button class="btn btn-sm btn-success" onclick="controlVM('{{ vm.name }}', 'start')">▶ Start</button>
                <button class="btn btn-sm btn-danger" onclick="deleteVM('{{ vm.name }}')">🗑 Delete</button>
            </div>
            {% endif %}
        </div>
        {% endfor %}
        {% else %}
        <div class="empty-state">
            <div class="icon">📦</div>
            <h2>No Virtual Machines</h2>
            <p>Create your first VM to get started</p>
            <button class="btn btn-primary" onclick="openCreateModal()">+ Create VM</button>
        </div>
        {% endif %}
    </div>

    <div class="modal-overlay" id="createModal">
        <div class="modal">
            <h2>🚀 Create New VM</h2>
            <form id="createForm" onsubmit="return createVM(event)">
                <div class="form-group">
                    <label>VM Name *</label>
                    <input type="text" name="vm_name" required pattern="[a-zA-Z0-9_-]+" placeholder="my-vm">
                </div>
                <div class="form-group">
                    <label>OS Type *</label>
                    <select name="os_type" id="osType" onchange="updateVersions()">
                        <option value="ubuntu">Ubuntu</option>
                        <option value="debian">Debian</option>
                        <option value="centos">CentOS</option>
                        <option value="fedora">Fedora</option>
                        <option value="arch">Arch Linux</option>
                        <option value="alpine">Alpine Linux</option>
                        <option value="custom">Custom (URL)</option>
                    </select>
                </div>
                <div class="form-group" id="versionGroup">
                    <label>Version *</label>
                    <select name="version" id="osVersion"></select>
                </div>
                <div class="form-group" id="customUrlGroup" style="display:none;">
                    <label>Custom Image URL</label>
                    <input type="text" name="custom_url" placeholder="https://example.com/image.qcow2">
                </div>
                <div class="form-group">
                    <label>Hostname</label>
                    <input type="text" name="hostname" placeholder="my-vm">
                </div>
                <div class="form-group">
                    <label>Username *</label>
                    <input type="text" name="username" required value="hopingboyz">
                </div>
                <div class="form-group">
                    <label>Password *</label>
                    <input type="text" name="password" required value="hopingboyz">
                </div>
                <div class="form-group">
                    <label>CPU Cores</label>
                    <input type="number" name="cpus" value="2" min="1" max="64">
                </div>
                <div class="form-group">
                    <label>Memory (MB)</label>
                    <input type="number" name="memory" value="2048" min="512" max="65536">
                </div>
                <div class="form-group">
                    <label>Disk Size</label>
                    <input type="text" name="disk_size" value="20G" placeholder="20G">
                </div>
                <div class="form-group">
                    <label>SSH Port</label>
                    <input type="number" name="ssh_port" value="2222" min="1024" max="65535">
                </div>
                <div class="form-group">
                    <label>CPU Model</label>
                    <select name="cpu_model">
                        {% for model in cpu_models %}
                        <option value="{{ model }}" {% if model == 'Host CPU (Passthrough)' %}selected{% endif %}>{{ model }}</option>
                        {% endfor %}
                    </select>
                </div>
                <div class="btn-row">
                    <button type="button" class="btn btn-danger" onclick="closeCreateModal()">Cancel</button>
                    <button type="submit" class="btn btn-success">Create VM</button>
                </div>
            </form>
        </div>
    </div>

    <div class="toast" id="toast"></div>

    <script>
        const OS_IMAGES = {{ os_images|safe }};

        function updateVersions() {
            const type = document.getElementById('osType').value;
            const versionGroup = document.getElementById('versionGroup');
            const customUrlGroup = document.getElementById('customUrlGroup');
            const versionSelect = document.getElementById('osVersion');

            if (type === 'custom') {
                versionGroup.style.display = 'none';
                customUrlGroup.style.display = 'block';
                return;
            }
            versionGroup.style.display = 'block';
            customUrlGroup.style.display = 'none';

            const versions = OS_IMAGES[type] || {};
            versionSelect.innerHTML = '';
            for (const [ver, info] of Object.entries(versions)) {
                const opt = document.createElement('option');
                opt.value = ver;
                opt.textContent = `${info.name}`;
                versionSelect.appendChild(opt);
            }
        }

        function openCreateModal() {
            document.getElementById('createModal').classList.add('active');
            updateVersions();
        }

        function closeCreateModal() {
            document.getElementById('createModal').classList.remove('active');
        }

        async function createVM(event) {
            event.preventDefault();
            const form = document.getElementById('createForm');
            const data = new FormData(form);

            try {
                const resp = await fetch('/api/vm/create', { method: 'POST', body: data });
                const result = await resp.json();
                if (result.success) {
                    showToast('VM created successfully!', 'success');
                    closeCreateModal();
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast('Error: ' + (result.error || 'Unknown'), 'error');
                }
            } catch (e) {
                showToast('Error creating VM', 'error');
            }
            return false;
        }

        async function controlVM(name, action) {
            try {
                const resp = await fetch(`/api/vm/${name}/${action}`, { method: 'POST' });
                const result = await resp.json();
                if (result.success) {
                    showToast(`${action} command sent to ${name}`, 'success');
                    setTimeout(() => location.reload(), 2000);
                } else {
                    showToast(`Error: ${result.error}`, 'error');
                }
            } catch (e) {
                showToast(`Error sending ${action} command`, 'error');
            }
        }

        async function deleteVM(name) {
            if (!confirm(`Delete VM "${name}"? This will remove the disk image.`)) return;
            try {
                const resp = await fetch(`/api/vm/${name}/delete`, { method: 'POST' });
                const result = await resp.json();
                if (result.success) {
                    showToast('VM deleted', 'success');
                    setTimeout(() => location.reload(), 1500);
                } else {
                    showToast(`Error: ${result.error}`, 'error');
                }
            } catch (e) {
                showToast('Error deleting VM', 'error');
            }
        }

        function showToast(msg, type) {
            const t = document.getElementById('toast');
            t.textContent = msg;
            t.className = 'toast ' + type;
            t.style.display = 'block';
            setTimeout(() => t.style.display = 'none', 3000);
        }

        function logout() {
            window.location.href = '/logout';
        }

        updateVersions();
    </script>
</body>
</html>
"""


def require_auth(f):
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        auth_enabled = app.config.get('VNC_USERNAME') and app.config.get('VNC_PASSWORD')
        if auth_enabled and 'authenticated' not in session:
            return redirect('/login')
        return f(*args, **kwargs)
    return decorated


@app.route('/login', methods=['GET', 'POST'])
def login_page():
    auth_enabled = app.config.get('VNC_USERNAME') and app.config.get('VNC_PASSWORD')
    if not auth_enabled:
        session['authenticated'] = True
        return redirect('/')

    error = ''
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        if username == app.config['VNC_USERNAME'] and password == app.config['VNC_PASSWORD']:
            session['authenticated'] = True
            return redirect('/')
        else:
            error = 'Invalid username or password'
    return render_template_string(LOGIN_TEMPLATE, error=error)


@app.route('/logout')
def logout():
    session.pop('authenticated', None)
    return redirect('/login')


@app.route('/')
@require_auth
def dashboard():
    vm_list = VMConfig.list_all()
    vms_data = []
    running_count = 0
    for name in vm_list:
        info = vm_manager.get_vm_info(name)
        if info.get('running'):
            running_count += 1
        vms_data.append({
            'name': name,
            'running': info.get('running', False),
            'os_type': info.get('os_type', 'N/A'),
            'codename': info.get('codename', 'N/A'),
            'cpu_model': info.get('cpu_model', 'N/A'),
            'cpus': info.get('cpus', 'N/A'),
            'memory': info.get('memory', 'N/A'),
            'ssh_port': info.get('ssh_port', 'N/A'),
            'vnc_port': info.get('vnc_port', 'N/A'),
        })

    import json as json_mod
    return render_template_string(
        DASHBOARD_TEMPLATE,
        vms=vms_data,
        stats={
            'total': len(vm_list),
            'running': running_count,
            'stopped': len(vm_list) - running_count,
            'kvm': '✅ Enabled' if vm_manager.kvm_available else '⚠ Disabled',
        },
        cpu_models=list(CPU_MODELS.keys()) if 'CPU_MODELS' in dir() else [],
        os_images=json_mod.dumps(OS_IMAGES) if 'OS_IMAGES' in dir() else '{}',
    )


import json as _json_mod


@app.route('/api/vm/create', methods=['POST'])
@require_auth
def api_create_vm():
    try:
        config = VMConfig()
        config.vm_name = request.form.get('vm_name', '').strip()
        if not config.vm_name:
            return jsonify({'success': False, 'error': 'VM name required'})

        if not vm_manager.validate_input('name', config.vm_name):
            return jsonify({'success': False, 'error': 'Invalid VM name'})

        if VMConfig.load(config.vm_name):
            return jsonify({'success': False, 'error': 'VM already exists'})

        os_type = request.form.get('os_type', 'ubuntu')
        version = request.form.get('version', '')

        if os_type == 'custom':
            config.os_type = 'custom'
            config.codename = 'custom'
            config.img_url = request.form.get('custom_url', '')
            if not config.img_url:
                return jsonify({'success': False, 'error': 'Custom image URL required'})
        elif os_type in OS_IMAGES and version in OS_IMAGES[os_type]:
            config.os_type = os_type
            config.codename = version
            config.img_url = OS_IMAGES[os_type][version]['url']
        else:
            return jsonify({'success': False, 'error': 'Invalid OS/version'})

        config.hostname = request.form.get('hostname', config.vm_name)
        config.username = request.form.get('username', 'hopingboyz')
        config.password = request.form.get('password', 'hopingboyz')
        config.cpus = request.form.get('cpus', '2')
        config.memory = request.form.get('memory', '2048')
        config.disk_size = request.form.get('disk_size', '20G')
        config.ssh_port = request.form.get('ssh_port', '2222')
        config.cpu_model = request.form.get('cpu_model', 'Host CPU (Passthrough)')

        def callback(ctype, msg):
            pass

        if not vm_manager.create_vm(config, callback):
            return jsonify({'success': False, 'error': 'Failed to create VM'})

        return jsonify({'success': True, 'vm_name': config.vm_name})

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/vm/<name>/<action>', methods=['POST'])
@require_auth
def api_control_vm(name, action):
    if action == 'start':
        success = vm_manager.start_vm(name)
    elif action == 'stop':
        success = vm_manager.stop_vm(name)
    elif action == 'restart':
        success = vm_manager.restart_vm(name)
    elif action == 'delete':
        success = vm_manager.delete_vm(name)
    else:
        return jsonify({'success': False, 'error': 'Unknown action'})

    return jsonify({'success': success, 'error': '' if success else 'Operation failed'})


@app.route('/api/status/<name>')
@require_auth
def api_status(name):
    running = vm_manager.is_vm_running(name)
    return jsonify({'running': running})


@app.route('/api/vms')
@require_auth
def api_vms():
    vms = []
    for name in VMConfig.list_all():
        info = vm_manager.get_vm_info(name)
        vms.append({
            'name': name,
            'running': info.get('running', False),
            'os_type': info.get('os_type', ''),
            'memory': info.get('memory', ''),
            'cpus': info.get('cpus', ''),
            'ssh_port': info.get('ssh_port', ''),
        })
    return jsonify(vms)


@app.route('/vnc/<name>')
@require_auth
def vnc_redirect(name):
    config = VMConfig.load(name)
    if not config:
        return "VM not found", 404
    vnc_port = config.vnc_port or 'auto'
    return render_template_string(f"""\
<!DOCTYPE html>
<html>
<head><title>VNC - {name}</title>
<meta charset="utf-8">
<style>
  * {{ margin:0; padding:0; box-sizing:border-box; }}
  body {{ background: #1a1a2e; color: white; font-family: sans-serif; }}
  .container {{ max-width: 1200px; margin: 0 auto; padding: 20px; }}
  h1 {{ margin-bottom: 20px; }}
  .info {{ background: #16213e; padding: 20px; border-radius: 10px; margin-bottom: 20px; }}
  .info p {{ margin: 5px 0; color: #a0a0a0; }}
  .vnc-link {{ display: inline-block; padding: 15px 30px; background: linear-gradient(135deg, #667eea, #764ba2); color: white; text-decoration: none; border-radius: 10px; font-weight: 600; }}
  .vnc-link:hover {{ transform: translateY(-2px); }}
</style>
</head>
<body>
<div class="container">
  <h1>🖥️ VNC Console - {name}</h1>
  <div class="info">
    <p>VM: {name}</p>
    <p>VNC Port: {vnc_port}</p>
    <p>SSH: ssh {config.username}@localhost -p {config.ssh_port}</p>
  </div>
  {{"<a class='vnc-link' href='#' onclick='alert(\"Connect using any VNC client to localhost:" + str(vnc_port) + "\")'>🔗 Connect via VNC Client</a>" if not vm_manager.is_vm_running(name) else ""}}
  <p style="margin-top:20px; color:#666;">Use a VNC client (TigerVNC, RealVNC) to connect to <b>localhost:{vnc_port}</b></p>
</div>
</body>
</html>
""", name=name, vnc_port=vnc_port)


def run_web_panel(host='0.0.0.0', port=8080, username=None, password=None):
    if username and password:
        app.config['VNC_USERNAME'] = username
        app.config['VNC_PASSWORD'] = password
        print(f"{Colors.BOLD}{Colors.GREEN}[✓]{Colors.RESET} {Colors.GREEN}Authentication enabled{Colors.RESET}")

    print(f"{Colors.BOLD}{Colors.GREEN}[✓]{Colors.RESET} {Colors.GREEN}Web Panel starting on http://{host}:{port}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}[ℹ]{Colors.RESET} {Colors.BLUE}Press Ctrl+C to stop the web panel (VMs keep running){Colors.RESET}")

    from .vm_manager import Colors

    app.run(host=host, port=port, debug=False, threaded=True)
