# vPanel

KVM-based VM reseller management panel built with Flask.

## Features

- VM lifecycle management (create, start, stop, restart, delete)
- Reseller/user management with role-based access
- Multiple OS templates (Ubuntu, Debian, CentOS, Fedora, Alpine, etc.)
- VNC & SSH web console via noVNC
- Backup & snapshot management
- Usage billing & invoicing
- Ticket support system
- Node management (multi-host)
- Firewall rules per VM
- Monitoring & alerts
- REST API

## Quick Install

```bash
sudo bash vpanel.sh
```

Select option **3** for full auto install + setup.

## Default Login

- URL: `http://<server-ip>`
- Username: `admin`
- Password: `admin@123`

## Requirements

- Ubuntu 22.04 / Debian 12
- KVM-capable CPU (hardware virtualization)
- Root access

## Project Structure

```
├── vpanel.sh                  # Management script
├── reseller_panel/            # Flask web app
│   ├── app.py                 # Main application
│   ├── database.py            # DB schema & init
│   ├── helpers.py             # Utility functions
│   ├── run.py                 # Entry point
│   └── templates/             # Jinja2 templates
├── vm_manager.py              # VM management CLI
└── vm_manager/                # VM management module
```

## Uninstall

```bash
sudo bash vpanel.sh
```

Select option **2** to uninstall.
