# vPanel

KVM-based VM reseller management panel built with Flask.  
Manage virtual machines, users, resellers, billing, and more from a web dashboard.

---

## Features

### VM Management
- Create, start, stop, restart, force-stop, delete VMs
- OS templates: Ubuntu (22.04, 24.04), Debian (10–13), CentOS Stream 9, Fedora (39, 40), AlmaLinux 9, Rocky Linux 9, Alpine 3.20, Windows 10 Lite
- Custom CPU models: Host Passthrough, QEMU64, AMD EPYC, AMD Ryzen, Intel Xeon, Intel Core
- Configurable vCPU, RAM, disk per VM
- Boot order, BIOS mode (BIOS/UEFI), rescue mode
- IPv4/IPv6, floating IP, reverse DNS
- Tags, favorites, protection, expiry protection
- Auto-start, persistent running
- Startup scripts & templates
- Additional storage volumes

### User & Reseller System
- Role-based access: admin, reseller, client
- Resellers can create and manage sub-users
- Configurable per-user limits: max VMs, CPU, RAM, disk, allowed OS
- Balance & credits system
- API key per user
- 2FA (OTP) support
- Theme customization (dark/light per user)

### Network & Security
- VNC console via noVNC with WebSocket proxy
- SSH web terminal
- Per-VM firewall rules (allow/deny by port, protocol, source IP)
- iptables integration with persistent rules

### Billing & Invoicing
- Hourly and monthly pricing per VM plan
- Automatic invoice generation
- Stripe payment integration
- Promo codes (percentage/fixed)
- Transaction history with balance tracking
- Tax support

### Backup & Snapshots
- Manual snapshots via QEMU
- Automated backup schedules (daily/weekly/monthly)
- Configurable retention policy
- Snapshot restore

### Monitoring
- TCP port monitoring per VM
- Configurable check interval & timeout
- Alert logging and activity feed
- Downtime detection

### Support System
- Ticket system with priorities (low/normal/high/critical)
- Department routing (support, billing, sales, technical)
- Ticket replies and status tracking

### Knowledge Base
- Article management with categories
- Published/unpublished control
- Slug-based URLs

### Node Management
- Multi-host (node) support for VM distribution
- Track per-node resources: CPU, RAM, disk
- Automatic node allocation

### API
- RESTful API with Bearer token authentication
- Manage VMs, users, and operations programmatically

---

## Quick Install

```bash
sudo bash vpanel.sh
```

Select option **3** for full auto install + setup.

### Manual Steps

```bash
# 1. Install dependencies
apt-get update && apt-get install -y python3 python3-pip nginx qemu-kvm novnc websockify

# 2. Install Python packages
pip3 install flask flask-sock pyotp qrcode[pil] werkzeug jinja2

# 3. Clone repo
git clone https://github.com/nobita329/vpanel.git /root/reseller_panel

# 4. Set up systemd service
cp systemd/vpanel.service /etc/systemd/system/
systemctl enable --now vpanel

# 5. Configure nginx
cp nginx/vpanel.conf /etc/nginx/sites-available/vpanel
ln -s /etc/nginx/sites-available/vpanel /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
systemctl reload nginx

# 6. Initialize database
python3 -c "from reseller_panel.database import init_db; init_db()"
```

---

## Default Login

| Field    | Value          |
|----------|----------------|
| URL      | `http://<ip>`  |
| Username | `admin`        |
| Password | `admin@123`    |

---

## Requirements

- **OS**: Ubuntu 22.04+ / Debian 12+
- **CPU**: x86_64 with KVM hardware virtualization
- **RAM**: Minimum 2GB (recommended 4GB+)
- **Disk**: 10GB+ free space
- **Root access**

---

## Project Structure

```
/root/
├── vpanel.sh                     # Management script (install/uninstall/setup)
├── reseller_panel/               # Flask web application
│   ├── app.py                    # Main Flask app (~160KB, all routes & logic)
│   ├── database.py               # SQLite schema, migrations, admin seeding
│   ├── helpers.py                # Utility functions (VM control, firewall, backup, monitor)
│   ├── run.py                    # Entry point (default port 8080)
│   ├── vms.sh                    # Shell helper for VM operations
│   ├── static/                   # CSS, JS, images
│   └── templates/                # Jinja2 HTML templates
│       ├── base.html             # Layout template
│       ├── login.html            # Login page
│       ├── dashboard.html        # Admin/reseller dashboard
│       ├── vms.html              # VM listing
│       ├── vm_detail.html        # Single VM detail & controls
│       ├── create_vm.html        # VM creation form
│       ├── users.html            # User management
│       ├── nodes.html            # Node management
│       ├── node_detail.html      # Node details
│       ├── plans.html            # VM plan management
│       ├── invoices.html         # Invoice listing
│       ├── tickets.html          # Support tickets
│       ├── ticket_view.html      # Ticket detail
│       ├── settings.html         # Panel settings
│       ├── profile.html          # User profile
│       ├── terminal.html         # SSH web terminal
│       ├── vnc.html              # noVNC console
│       └── ...                   # Additional pages
├── vm_manager.py                 # CLI tool for VM management
├── vm_manager/                   # VM management module
│   ├── __init__.py
│   ├── __main__.py
│   ├── config.py
│   ├── main.py
│   ├── vm_manager.py
│   └── web_panel.py
└── vms/                          # VM disk images & data (excluded from git)
    ├── pids/                     # PID files for running VMs
    └── snapshots/                # VM snapshots
```

---

## Services

| Service     | Port | Description                 |
|-------------|------|-----------------------------|
| Flask app   | 8080 | vPanel web application      |
| Nginx       | 80   | Reverse proxy to Flask      |
| noVNC       | 6080 | VNC web client              |

Systemd service: `vpanel.service`  
Nginx config: `/etc/nginx/sites-available/vpanel`

---

## API Endpoints

Authentication: `Authorization: Bearer <token>`

| Method | Endpoint                | Description            |
|--------|-------------------------|------------------------|
| GET    | `/api/vms`              | List user's VMs        |
| POST   | `/api/vms`              | Create a VM            |
| GET    | `/api/vms/{uuid}`       | VM details             |
| POST   | `/api/vms/{uuid}/start` | Start a VM             |
| POST   | `/api/vms/{uuid}/stop`  | Stop a VM              |
| POST   | `/api/vms/{uuid}/restart` | Restart a VM         |
| DELETE | `/api/vms/{uuid}`       | Delete a VM            |
| GET    | `/api/users`            | List users (admin)     |
| POST   | `/api/users`            | Create user (admin)    |
| GET    | `/api/nodes`            | List nodes             |
| GET    | `/api/plans`            | List VM plans          |

---

## Uninstall

```bash
sudo bash vpanel.sh
```

Select option **2**. Optionally remove app data and VM disk images.

### Manual Uninstall

```bash
systemctl stop vpanel && systemctl disable vpanel
rm -f /etc/systemd/system/vpanel.service
systemctl daemon-reload
rm -f /etc/nginx/sites-available/vpanel /etc/nginx/sites-enabled/vpanel
systemctl reload nginx
rm -rf /root/reseller_panel /root/vms
```

---

## Configuration

Settings are stored in the `settings` SQLite table and configurable from the web admin panel under **Settings**:

- Site name, logo, footer
- Currency, profit margin
- SMTP (email notifications)
- Stripe (payment processing)
- Default theme (dark/light)
- Custom CSS
- Branding toggle

Database: `/root/reseller_panel/reseller.db`

---

## License

MIT
