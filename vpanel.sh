#!/usr/bin/env bash
set -euo pipefail

VERSION="1.0.0"
APP_DIR="/root/reseller_panel"
REPO_URL="https://github.com/Nadims29/vpanel.git"
SERVICE_FILE="/etc/systemd/system/vpanel.service"
NGINX_CONF="/etc/nginx/sites-available/vpanel"
NGINX_ENABLED="/etc/nginx/sites-enabled/vpanel"

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

logo() {
    echo -e "${GREEN}"
    echo "╔══════════════════════════════════╗"
    echo "║          vPanel Manager          ║"
    echo "║          Version $VERSION          ║"
    echo "╚══════════════════════════════════╝"
    echo -e "${NC}"
}

menu() {
    clear
    logo
    echo "1) Install"
    echo "2) Uninstall"
    echo "3) All Auto (Install + Setup)"
    echo "4) Exit"
    echo ""
    read -rp "Select option [1-4]: " choice
    case "$choice" in
        1) install ;;
        2) uninstall ;;
        3) all_auto ;;
        4) exit 0 ;;
        *) echo -e "${RED}Invalid option${NC}"; sleep 1; menu ;;
    esac
}

install_deps() {
    echo -e "${YELLOW}[*] Installing system dependencies...${NC}"
    apt-get update -y
    apt install -y qemu-system cloud-image-utils wget lsof 
    apt-get install -y python3 python3-pip python3-venv nginx qemu-kvm libvirt-daemon-system \
                       virt-manager novnc websockify iptables wget curl sqlite3
}

install_python_deps() {
    echo -e "${YELLOW}[*] Installing Python packages...${NC}"
    pip3 install flask flask-sock pyotp qrcode[pil] werkzeug jinja2
}

git clone https://github.com/nobita329/vpanel.git
shopt -s dotglob && mv /root/vpanel/* /root/ && rm -rf /root/vpanel
clone_repo() {
    if [[ -d "$APP_DIR/.git" ]]; then
        echo -e "${YELLOW}[*] Updating vPanel repository...${NC}"
        cd "$APP_DIR" && git pull
    elif [[ -d "$APP_DIR" ]]; then
        echo -e "${YELLOW}[*] App directory exists (not a git repo), skipping clone${NC}"
    else
        echo -e "${YELLOW}[*] Cloning vPanel repository...${NC}"
        git clone "$REPO_URL" "$APP_DIR"
    fi
}

setup_service() {
    echo -e "${YELLOW}[*] Creating systemd service...${NC}"
    cat > "$SERVICE_FILE" << 'EOF'
[Unit]
Description=vPanel Reseller System
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/root
ExecStart=/usr/bin/python3 /root/reseller_panel/run.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable vpanel
    systemctl start vpanel
    echo -e "${GREEN}[+] vPanel service started${NC}"
}

setup_nginx() {
    echo -e "${YELLOW}[*] Configuring Nginx reverse proxy...${NC}"

    rm -f /etc/nginx/sites-enabled/default

    cat > "$NGINX_CONF" << 'EOF'
server {
    listen 80;
    server_name _;

    client_max_body_size 10G;

    location /static/ {
        alias /root/reseller_panel/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    location /novnc/ {
        alias /usr/share/novnc/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }

    location /ssh/ {
        proxy_pass http://127.0.0.1:8080/ssh/;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400s;
        proxy_send_timeout 86400s;
    }
}
EOF
    ln -sf "$NGINX_CONF" "$NGINX_ENABLED"
    nginx -t 2>/dev/null && (systemctl restart nginx 2>/dev/null || nginx -s reload 2>/dev/null) || true
    echo -e "${GREEN}[+] Nginx configured${NC}"
}

setup_db() {
    echo -e "${YELLOW}[*] Initializing database...${NC}"
    python3 -c "from reseller_panel.database import init_db; init_db()"
    echo -e "${GREEN}[+] Database initialized (admin / admin@123)${NC}"
}

setup_vm_dir() {
    mkdir -p ~/vms/{pids,snapshots}
}

install() {
    install_deps
    install_python_deps
    clone_repo
    setup_service
    setup_nginx
    setup_vm_dir
    echo -e "${GREEN}[+] Install complete${NC}"
    read -rp "Press Enter to continue..."
    menu
}

uninstall() {
    echo -e "${YELLOW}[*] Stopping vPanel service...${NC}"
    systemctl stop vpanel 2>/dev/null || true
    systemctl disable vpanel 2>/dev/null || true

    echo -e "${YELLOW}[*] Removing systemd service...${NC}"
    rm -f "$SERVICE_FILE"
    systemctl daemon-reload

    echo -e "${YELLOW}[*] Removing Nginx config...${NC}"
    rm -f "$NGINX_CONF" "$NGINX_ENABLED"
    systemctl reload nginx 2>/dev/null || true

    read -rp "Remove app directory $APP_DIR? (y/N): " ans
    if [[ "$ans" =~ ^[yY] ]]; then
        rm -rf "$APP_DIR"
        echo -e "${YELLOW}[*] App directory removed${NC}"
    fi

    read -rp "Remove VM data ~/vms? (y/N): " ans
    if [[ "$ans" =~ ^[yY] ]]; then
        rm -rf ~/vms
        echo -e "${YELLOW}[*] VM data removed${NC}"
    fi

    echo -e "${GREEN}[+] Uninstall complete${NC}"
    read -rp "Press Enter to continue..."
    menu
}

setup() {
    setup_db
    setup_vm_dir
    echo -e "${GREEN}[+] Setup complete${NC}"
}

all_auto() {
    echo -e "${YELLOW}[*] Full auto: Install + Setup${NC}"
    install
    setup
    echo -e "${GREEN}[+] All done — vPanel is ready at http://$(curl -4s ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')${NC}"
    echo -e "${GREEN}[+] Login: admin / admin@123${NC}"
    read -rp "Press Enter to continue..."
    menu
}

if [[ $EUID -ne 0 ]]; then
    echo -e "${RED}This script must be run as root${NC}"
    exit 1
fi

menu
