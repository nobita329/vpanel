import os
import sqlite3
from werkzeug.security import generate_password_hash

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'reseller.db')


def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        email TEXT,
        role TEXT NOT NULL DEFAULT 'client',
        parent_id INTEGER,
        balance REAL DEFAULT 0.00,
        credits INTEGER DEFAULT 0,
        max_vms INTEGER DEFAULT 5,
        max_cpus INTEGER DEFAULT 8,
        max_ram INTEGER DEFAULT 16384,
        max_disk INTEGER DEFAULT 200,
        allowed_os TEXT DEFAULT 'all',
        api_key TEXT,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (parent_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS vm_plans (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL, description TEXT, cpus INTEGER DEFAULT 1,
        ram INTEGER DEFAULT 1024, disk INTEGER DEFAULT 20, bandwidth INTEGER DEFAULT 1000,
        price_monthly REAL DEFAULT 9.99, price_hourly REAL DEFAULT 0.01,
        os_types TEXT DEFAULT 'ubuntu,debian,centos', status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS nodes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        hostname TEXT NOT NULL,
        ip_address TEXT,
        total_cpus INTEGER DEFAULT 0,
        total_ram INTEGER DEFAULT 0,
        total_disk INTEGER DEFAULT 0,
        used_cpus INTEGER DEFAULT 0,
        used_ram INTEGER DEFAULT 0,
        used_disk INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS vms (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL, user_id INTEGER NOT NULL, plan_id INTEGER,
        node_id INTEGER,
        os_type TEXT, os_version TEXT, hostname TEXT, username TEXT DEFAULT 'root',
        password TEXT, cpus INTEGER DEFAULT 1, ram INTEGER DEFAULT 1024,
        disk_size TEXT DEFAULT '20G', disk_gb INTEGER DEFAULT 20,
        ssh_port INTEGER, vnc_port INTEGER,
        status TEXT DEFAULT 'stopped', cpu_model TEXT DEFAULT 'Host CPU (Passthrough)',
        img_file TEXT, seed_file TEXT, bandwidth_used INTEGER DEFAULT 0,
        bandwidth_limit INTEGER DEFAULT 1000, hourly_cost REAL DEFAULT 0.00,
        total_cost REAL DEFAULT 0.00, notes TEXT,
        backup_enabled INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, started_at TIMESTAMP,
        last_billed TIMESTAMP, expiry_date TIMESTAMP, auto_renew INTEGER DEFAULT 1,
        suspended INTEGER DEFAULT 0,
        FOREIGN KEY (user_id) REFERENCES users(id),
        FOREIGN KEY (plan_id) REFERENCES vm_plans(id),
        FOREIGN KEY (node_id) REFERENCES nodes(id)
    );
    CREATE TABLE IF NOT EXISTS invoices (
        id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_no TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL, amount REAL NOT NULL, tax REAL DEFAULT 0.00,
        total REAL NOT NULL, type TEXT DEFAULT 'monthly', status TEXT DEFAULT 'pending',
        description TEXT, stripe_session_id TEXT, paid_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS transactions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
        type TEXT NOT NULL, amount REAL NOT NULL, balance_before REAL DEFAULT 0,
        balance_after REAL DEFAULT 0, description TEXT, reference TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT, uuid TEXT UNIQUE NOT NULL,
        user_id INTEGER NOT NULL, subject TEXT NOT NULL, message TEXT,
        priority TEXT DEFAULT 'normal', department TEXT DEFAULT 'support',
        status TEXT DEFAULT 'open', assigned_to INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS ticket_replies (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ticket_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL, message TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (ticket_id) REFERENCES tickets(id), FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vm_uuid TEXT NOT NULL,
        name TEXT NOT NULL,
        file_path TEXT,
        size INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (vm_uuid) REFERENCES vms(uuid)
    );
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS activity_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        action TEXT NOT NULL, details TEXT, ip_address TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY (user_id) REFERENCES users(id)
    );
    """)

    try:
        conn.execute("ALTER TABLE vms ADD COLUMN node_id INTEGER REFERENCES nodes(id)")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN disk_gb INTEGER DEFAULT 20")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN backup_enabled INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN suspended INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN ws_port INTEGER")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE invoices ADD COLUMN stripe_session_id TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN auto_start INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN persist_running INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN otp_secret TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN otp_enabled INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE users ADD COLUMN theme TEXT DEFAULT 'dark'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE snapshots ADD COLUMN file_path TEXT")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE custom_isos ADD COLUMN uploaded_by INTEGER REFERENCES users(id)")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE custom_isos ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN tags TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN favorite INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN protected INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN boot_order TEXT DEFAULT 'disk'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN bios_mode TEXT DEFAULT 'bios'")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN rescue_mode INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN dns_servers TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN floating_ip TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN ipv4_addresses TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN ipv6_addresses TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN reverse_dns TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN startup_script_id INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN template_id INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN auto_stop_time TEXT DEFAULT ''")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN expiry_protection INTEGER DEFAULT 1")
    except sqlite3.OperationalError:
        pass
    try:
        conn.execute("ALTER TABLE vms ADD COLUMN ws_port INTEGER")
    except sqlite3.OperationalError:
        pass

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS firewall_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vm_uuid TEXT NOT NULL,
        rule_type TEXT NOT NULL DEFAULT 'allow',
        protocol TEXT NOT NULL DEFAULT 'tcp',
        port_start INTEGER,
        port_end INTEGER,
        source_ip TEXT DEFAULT '0.0.0.0/0',
        description TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (vm_uuid) REFERENCES vms(uuid)
    );
    CREATE TABLE IF NOT EXISTS backup_schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vm_uuid TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        frequency TEXT NOT NULL DEFAULT 'daily',
        hour INTEGER DEFAULT 2,
        day_of_week INTEGER DEFAULT 0,
        retention_count INTEGER DEFAULT 7,
        next_run TIMESTAMP,
        last_run TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (vm_uuid) REFERENCES vms(uuid)
    );
    CREATE TABLE IF NOT EXISTS iso_mounts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vm_uuid TEXT NOT NULL,
        iso_path TEXT NOT NULL,
        mounted INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (vm_uuid) REFERENCES vms(uuid)
    );
    CREATE TABLE IF NOT EXISTS promo_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        type TEXT NOT NULL DEFAULT 'percentage',
        value REAL NOT NULL DEFAULT 0,
        min_amount REAL DEFAULT 0,
        max_uses INTEGER DEFAULT 0,
        used_count INTEGER DEFAULT 0,
        expires_at TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS api_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_used_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS login_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        username TEXT,
        ip_address TEXT,
        user_agent TEXT,
        status TEXT NOT NULL DEFAULT 'success',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS custom_isos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        size INTEGER DEFAULT 0,
        uploaded_by INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (uploaded_by) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS monitoring_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vm_uuid TEXT NOT NULL,
        check_type TEXT NOT NULL DEFAULT 'ping',
        port INTEGER,
        interval_sec INTEGER DEFAULT 300,
        timeout_sec INTEGER DEFAULT 10,
        enabled INTEGER DEFAULT 1,
        last_status TEXT,
        last_checked TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (vm_uuid) REFERENCES vms(uuid)
    );
    CREATE TABLE IF NOT EXISTS monitoring_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        check_id INTEGER NOT NULL,
        status TEXT NOT NULL,
        response_time_ms INTEGER,
        error TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (check_id) REFERENCES monitoring_checks(id)
    );
    CREATE TABLE IF NOT EXISTS kb_articles (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL,
        category TEXT DEFAULT 'general',
        content TEXT,
        published INTEGER DEFAULT 1,
        author_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (author_id) REFERENCES users(id)
    );
    CREATE TABLE IF NOT EXISTS ssh_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        public_key TEXT NOT NULL,
        fingerprint TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS vm_ssh_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vm_uuid TEXT NOT NULL REFERENCES vms(uuid),
        ssh_key_id INTEGER NOT NULL REFERENCES ssh_keys(id)
    );
    CREATE TABLE IF NOT EXISTS startup_scripts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        name TEXT NOT NULL,
        content TEXT NOT NULL,
        script_type TEXT DEFAULT 'shell',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS templates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        user_id INTEGER NOT NULL REFERENCES users(id),
        source_vm_uuid TEXT REFERENCES vms(uuid),
        os_type TEXT,
        os_version TEXT,
        cpus INTEGER DEFAULT 1,
        ram INTEGER DEFAULT 1024,
        disk_size TEXT DEFAULT '20G',
        disk_gb INTEGER DEFAULT 20,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS additional_storage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vm_uuid TEXT NOT NULL REFERENCES vms(uuid),
        file_path TEXT NOT NULL,
        size_gb INTEGER NOT NULL DEFAULT 10,
        mount_point TEXT DEFAULT '/mnt/storage',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        channel TEXT NOT NULL DEFAULT 'email',
        label TEXT,
        config TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS notification_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(id),
        event_type TEXT NOT NULL,
        notification_id INTEGER NOT NULL REFERENCES notifications(id),
        vm_uuid TEXT DEFAULT '',
        enabled INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS resource_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vm_uuid TEXT NOT NULL REFERENCES vms(uuid),
        metric TEXT NOT NULL,
        threshold REAL NOT NULL,
        enabled INTEGER DEFAULT 1,
        last_triggered TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS backup_files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        vm_uuid TEXT NOT NULL REFERENCES vms(uuid),
        name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        size INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)


    admin = conn.execute("SELECT id, password FROM users WHERE username='admin'").fetchone()
    if not admin:
        pw_hash = generate_password_hash('admin@123')
        conn.execute("INSERT INTO users (username,password,email,role,max_vms,max_cpus,max_ram,max_disk) VALUES (?,?,?,'admin',999,999,999999,999999)",
                     ('admin', pw_hash, 'admin@vpanel.com'))
    elif len(admin['password']) == 64:
        conn.execute("UPDATE users SET password=? WHERE id=?", (generate_password_hash('admin@123'), admin['id']))
        plans = [
            ('Starter VPS', '1 vCPU, 1GB RAM, 20GB SSD', 1, 1024, 20, 500, 4.99, 0.005),
            ('Basic VPS', '2 vCPU, 2GB RAM, 40GB SSD', 2, 2048, 40, 1000, 9.99, 0.01),
            ('Pro VPS', '4 vCPU, 4GB RAM, 80GB SSD', 4, 4096, 80, 2000, 19.99, 0.02),
            ('Business VPS', '6 vCPU, 8GB RAM, 160GB SSD', 6, 8192, 160, 5000, 39.99, 0.04),
            ('Enterprise VPS', '8 vCPU, 16GB RAM, 320GB SSD', 8, 16384, 320, 10000, 79.99, 0.08),
        ]
        for p in plans:
            conn.execute("INSERT INTO vm_plans (name,description,cpus,ram,disk,bandwidth,price_monthly,price_hourly) VALUES (?,?,?,?,?,?,?,?)", p)
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('currency','USD')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('site_name','vpanel VM Reseller')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('profit_margin','20')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('smtp_host','')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('smtp_port','587')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('smtp_user','')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('smtp_pass','')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('smtp_from','noreply@vpanel.com')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('stripe_secret_key','')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('stripe_publishable_key','')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('stripe_webhook_secret','')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('site_logo','')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('footer_text','Powered by vPanel Reseller Platform')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('default_theme','dark')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('custom_css','')")
        conn.execute("INSERT OR IGNORE INTO settings (key,value) VALUES ('branding_show','1')")

    conn.commit()
    conn.close()
