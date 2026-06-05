#!/bin/bash

# Add user local bin to PATH for websockify
export PATH="$HOME/.local/bin:$PATH"

# Disable exit on error for better control
set -uo pipefail

# Handle interrupt signals (Ctrl+C) - VMs keep running
handle_interrupt() {
    echo
    echo
    echo -e "${CYAN}${BOLD}╔═══════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}${BOLD}║          Script Closed - Everything Keeps Running!        ║${RESET}"
    echo -e "${CYAN}${BOLD}╚═══════════════════════════════════════════════════════════╝${RESET}"
    echo
    echo -e "${GREEN}${BOLD}[✓]${RESET} ${GREEN}All VMs running in background${RESET}"
    echo -e "${GREEN}${BOLD}[✓]${RESET} ${GREEN}All noVNC web consoles running${RESET}"
    echo -e "${BLUE}${BOLD}[ℹ]${RESET} ${BLUE}Control: sudo systemctl start/stop vm-NAME${RESET}"
    echo
    
    exit 0
}

# Function to stop all manually started noVNC processes
stop_all_manual_novnc() {
    local stopped=0
    
    # Find all noVNC PID files
    if [ -d "$VM_DIR/pids" ]; then
        for pid_file in "$VM_DIR/pids"/*-novnc.pid; do
            [ -f "$pid_file" ] || continue
            
            local vm_name=$(basename "$pid_file" | sed 's/-novnc\.pid$//')
            local pid=$(cat "$pid_file" 2>/dev/null)
            
            # Check if this noVNC is managed by systemd
            if sudo systemctl is-active "vm-${vm_name}-novnc.service" &>/dev/null 2>&1; then
                continue
            fi
            
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null
                rm -f "$pid_file"
                ((stopped++))
            else
                rm -f "$pid_file"
            fi
        done
    fi
    
    if [ $stopped -gt 0 ]; then
        echo -e "${YELLOW}${BOLD}[!]${RESET} ${YELLOW}Stopped $stopped manual noVNC process(es)${RESET}"
        echo -e "${BLUE}${BOLD}[ℹ]${RESET} ${BLUE}Systemd-managed noVNC services continue running${RESET}"
    fi
}

# Function to create VNC control panel
create_vnc_control_panel() {
    local vm_name=$1
    local novnc_port=$2
    
    # Create web directory for this VM
    local web_dir="$VM_DIR/web/$vm_name"
    mkdir -p "$web_dir"
    
    # Copy noVNC files if they exist
    local novnc_source=""
    for dir in "$VM_DIR/novnc" "$HOME/.novnc" "/usr/share/novnc"; do
        if [ -d "$dir" ] && [ -f "$dir/vnc.html" ]; then
            novnc_source="$dir"
            break
        fi
    done
    
    if [ -n "$novnc_source" ]; then
        for item in "$novnc_source"/*; do
            local basename=$(basename "$item")
            if [ "$basename" = "vnc.html" ]; then
                cp "$item" "$web_dir/vnc_real.html" 2>/dev/null
            elif [ ! -e "$web_dir/$basename" ]; then
                ln -sf "$item" "$web_dir/$basename" 2>/dev/null || cp -r "$item" "$web_dir/$basename" 2>/dev/null
            fi
        done
    fi
    
    # Create index.html
    cat > "$web_dir/index.html" <<'EOFINDEX'
<!DOCTYPE html>
<html>
<head>
    <title>VM_NAME_PLACEHOLDER - Console</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.tailwindcss.com"></script>
    <style>
        body { background: #0f0f1a; font-family: 'Inter', sans-serif; }
        .card { background: #1a1a2e; border: 1px solid #2a2a4a; }
        .btn-primary { background: linear-gradient(135deg, #667eea, #764ba2); }
        .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 15px rgba(102,126,234,0.4); }
        .btn-success { background: linear-gradient(135deg, #10b981, #059669); }
        .btn-danger { background: linear-gradient(135deg, #ef4444, #dc2626); }
        .btn-warning { background: linear-gradient(135deg, #f59e0b, #d97706); }
    </style>
</head>
<body class="text-gray-100 min-h-screen flex items-center justify-center p-4">
    <div class="card rounded-2xl p-8 max-w-lg w-full">
        <h1 class="text-2xl font-bold text-center mb-2">VM_NAME_PLACEHOLDER</h1>
        <p class="text-gray-400 text-center mb-6">Web Console</p>
        <div class="flex flex-col space-y-3">
            <a href="/vnc.html?autoconnect=true&resize=scale" class="btn-primary text-center py-3 rounded-lg font-semibold">Open VNC Console</a>
        </div>
    </div>
</body>
</html>
EOFINDEX
    
    sed "s/VM_NAME_PLACEHOLDER/$vm_name/g" "$web_dir/index.html" > "$web_dir/index.html.tmp" && mv "$web_dir/index.html.tmp" "$web_dir/index.html"
}

# Trap Ctrl+C for graceful message
trap 'handle_interrupt' INT TERM

# Color codes
readonly RED='\033[1;31m'
readonly GREEN='\033[1;32m'
readonly YELLOW='\033[1;33m'
readonly BLUE='\033[1;34m'
readonly MAGENTA='\033[1;35m'
readonly CYAN='\033[1;36m'
readonly WHITE='\033[1;37m'
readonly RESET='\033[0m'
readonly BOLD='\033[1m'
readonly DIM='\033[2m'

# Global variables
KVM_AVAILABLE=false
VM_DIR="${VM_DIR:-$HOME/vms}"

# CPU Models available
declare -A CPU_MODELS=(
    ["AMD Ryzen 9 7950X3D"]="EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 9 7950X3D 16-Core Processor"
    ["AMD Ryzen 9 7950X"]="EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 9 7950X 16-Core Processor"
    ["AMD Ryzen 9 5950X"]="EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 9 5950X 16-Core Processor"
    ["AMD Ryzen 7 7800X3D"]="EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 7 7800X3D 8-Core Processor"
    ["AMD Ryzen 7 5800X"]="EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 7 5800X 8-Core Processor"
    ["AMD Ryzen 5 7600X"]="EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 5 7600X 6-Core Processor"
    ["AMD EPYC 9654"]="EPYC,vendor=AuthenticAMD,model-id=AMD EPYC 9654 96-Core Processor"
    ["AMD EPYC 7763"]="EPYC,vendor=AuthenticAMD,model-id=AMD EPYC 7763 64-Core Processor"
    ["Intel Core i9-14900K"]="Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i9-14900K"
    ["Intel Core i9-13900K"]="Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i9-13900K"
    ["Intel Core i7-14700K"]="Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i7-14700K"
    ["Intel Core i7-13700K"]="Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i7-13700K"
    ["Intel Xeon Platinum 8380"]="Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon Platinum 8380"
    ["Intel Xeon Gold 6348"]="Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon Gold 6348"
    ["Host CPU (Passthrough)"]="host"
    ["QEMU Default (qemu64)"]="qemu64"
    ["Custom CPU Model"]="custom"
)

# OS Options
declare -A OS_OPTIONS=(
    ["Ubuntu 24.04 LTS"]="ubuntu|noble|https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img|ubuntu24|ubuntu|ubuntu"
    ["Ubuntu 22.04 LTS"]="ubuntu|jammy|https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img|ubuntu22|ubuntu|ubuntu"
    ["Ubuntu 20.04 LTS"]="ubuntu|focal|https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img|ubuntu20|ubuntu|ubuntu"
    ["Debian 13 (Trixie)"]="debian|trixie|https://cloud.debian.org/images/cloud/trixie/latest/debian-13-generic-amd64.qcow2|debian13|debian|debian"
    ["Debian 12 (Bookworm)"]="debian|bookworm|https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2|debian12|debian|debian"
    ["Debian 11 (Bullseye)"]="debian|bullseye|https://cloud.debian.org/images/cloud/bullseye/latest/debian-11-generic-amd64.qcow2|debian11|debian|debian"
    ["Debian 10 (Buster)"]="debian|buster|https://cloud.debian.org/images/cloud/buster/latest/debian-10-generic-amd64.qcow2|debian10|debian|debian"
    ["Fedora 40"]="fedora|40|https://download.fedoraproject.org/pub/fedora/linux/releases/40/Cloud/x86_64/images/Fedora-Cloud-Base-40-1.14.x86_64.qcow2|fedora40|fedora|fedora"
    ["Fedora 39"]="fedora|39|https://download.fedoraproject.org/pub/fedora/linux/releases/39/Cloud/x86_64/images/Fedora-Cloud-Base-39-1.5.x86_64.qcow2|fedora39|fedora|fedora"
    ["CentOS Stream 9"]="centos|stream9|https://cloud.centos.org/centos/9-stream/x86_64/images/CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2|centos9|centos|centos"
    ["AlmaLinux 9"]="almalinux|9|https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/AlmaLinux-9-GenericCloud-latest.x86_64.qcow2|almalinux9|alma|alma"
    ["Rocky Linux 9"]="rockylinux|9|https://download.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud.latest.x86_64.qcow2|rocky9|rocky|rocky"
    ["Proxmox VE 8"]="proxmox|8|https://enterprise.proxmox.com/iso/proxmox-ve_8.4-1.iso|proxmox8|proxmox|proxmox"
    ["Windows 10 Lite"]="windows|10-lite|https://archive.org/download/windows-10-lite-edition-19h2-x64/Windows%2010%20Lite%20Edition%2019H2%20x64.iso|win10|admin|admin"
)

# Function to check KVM availability
check_kvm_support() {
    if [ -e /dev/kvm ] && [ -r /dev/kvm ] && [ -w /dev/kvm ]; then
        KVM_AVAILABLE=true
        return 0
    fi
    KVM_AVAILABLE=false
    return 1
}

# Function to check dependencies
check_dependencies() {
    local deps=("qemu-system-x86_64" "wget" "cloud-localds" "qemu-img" "openssl")
    local missing_deps=()
    
    for dep in "${deps[@]}"; do
        if command -v "$dep" &> /dev/null; then
            echo -e "${GREEN}${BOLD}[✓]${RESET} ${GREEN}Found: $dep${RESET}"
        else
            missing_deps+=("$dep")
            echo -e "${RED}${BOLD}[✗]${RESET} ${RED}Missing: $dep${RESET}"
        fi
    done
    
    if [ ${#missing_deps[@]} -ne 0 ]; then
        echo
        echo -e "${RED}${BOLD}[✗]${RESET} ${RED}Missing required dependencies: ${missing_deps[*]}${RESET}"
        echo
        echo -e "${BLUE}Install: sudo apt install qemu-system cloud-image-utils wget openssl${RESET}"
        echo
        exit 1
    fi
    
    echo -e "${GREEN}${BOLD}[✓]${RESET} ${GREEN}All required dependencies are installed${RESET}"
}

# Function to validate input
validate_input() {
    local type=$1
    local value=$2
    
    case $type in
        "number")
            if ! [[ "$value" =~ ^[0-9]+$ ]]; then
                return 1
            fi
            ;;
        "port")
            if ! [[ "$value" =~ ^[0-9]+$ ]] || [ "$value" -lt 1024 ] || [ "$value" -gt 65535 ]; then
                return 1
            fi
            ;;
        "name")
            if ! [[ "$value" =~ ^[a-zA-Z0-9_-]+$ ]]; then
                return 1
            fi
            ;;
    esac
    return 0
}

# Function to check if port is available
check_port_available() {
    local port=$1
    if command -v ss &> /dev/null; then
        if ss -tln 2>/dev/null | grep -q ":$port "; then
            return 1
        fi
    elif command -v netstat &> /dev/null; then
        if netstat -tln 2>/dev/null | grep -q ":$port "; then
            return 1
        fi
    fi
    return 0
}

# Function to normalize disk size
normalize_disk_size() {
    local size=$1
    if [[ "$size" =~ ^[0-9]+$ ]]; then
        echo "${size}G"
    else
        echo "$size"
    fi
}

# Function to find next available VNC port
find_available_vnc_port() {
    local start_port=${1:-5900}
    local port=$start_port
    if [ "$port" -lt 5900 ]; then port=5900; fi
    while [ $port -lt 6000 ]; do
        if check_port_available "$port"; then
            echo "$port"
            return 0
        fi
        ((port++))
    done
    echo "5900"
    return 1
}

# Function to find next available noVNC port
find_available_novnc_port() {
    local start_port=${1:-6080}
    local port=$start_port
    while [ $port -lt 6200 ]; do
        if check_port_available "$port"; then
            echo "$port"
            return 0
        fi
        ((port++))
    done
    echo "6080"
    return 1
}

# Function to get all VM configurations
get_vm_list() {
    if [ ! -d "$VM_DIR" ]; then
        return
    fi
    find "$VM_DIR" -name "*.conf" -type f -exec basename {} .conf \; 2>/dev/null | sort
}

# Function to check if VM is running
is_vm_running() {
    local vm_name=$1
    local config_file="$VM_DIR/$vm_name.conf"
    if [[ ! -f "$config_file" ]]; then return 1; fi
    local IMG_FILE=""
    source "$config_file" 2>/dev/null || return 1
    if [ -z "$IMG_FILE" ]; then return 1; fi
    if pgrep -f "vm-$vm_name" >/dev/null 2>&1; then return 0; fi
    if pgrep -f "$IMG_FILE" >/dev/null 2>&1; then return 0; fi
    return 1
}

# Function to load VM configuration
load_vm_config() {
    local vm_name=$1
    local config_file="$VM_DIR/$vm_name.conf"
    
    if [[ ! -f "$config_file" ]]; then
        echo -e "${RED}${BOLD}[✗]${RESET} ${RED}Configuration not found: $config_file${RESET}"
        return 1
    fi
    
    unset VM_NAME OS_TYPE CODENAME IMG_URL HOSTNAME USERNAME PASSWORD
    unset DISK_SIZE MEMORY CPUS SSH_PORT GUI_MODE PORT_FORWARDS IMG_FILE SEED_FILE CREATED CPU_MODEL CUSTOM_CPU_STRING
    unset VNC_PORT NOVNC_PORT SMBIOS_MANUFACTURER SMBIOS_PRODUCT SMBIOS_VERSION
    unset VNC_USERNAME VNC_PASSWORD
    
    if ! source "$config_file" 2>/dev/null; then
        echo -e "${RED}${BOLD}[✗]${RESET} ${RED}Failed to load configuration${RESET}"
        return 1
    fi
    
    CPU_MODEL="${CPU_MODEL:-Host CPU (Passthrough)}"
    
    if [[ -z "$VM_NAME" || -z "$IMG_FILE" ]]; then
        echo -e "${RED}${BOLD}[✗]${RESET} ${RED}Invalid configuration${RESET}"
        return 1
    fi
    
    return 0
}

# Function to save VM configuration
save_vm_config() {
    local config_file="$VM_DIR/$VM_NAME.conf"
    local cpu_string="${CPU_MODELS[$CPU_MODEL]}"
    if [ -z "$cpu_string" ] && [ -n "$CUSTOM_CPU_STRING" ]; then
        cpu_string="$CUSTOM_CPU_STRING"
    fi
    
    cat > "$config_file" <<EOF
VM_NAME="$VM_NAME"
OS_TYPE="$OS_TYPE"
CODENAME="$CODENAME"
IMG_URL="$IMG_URL"
HOSTNAME="$HOSTNAME"
USERNAME="$USERNAME"
PASSWORD="$PASSWORD"
DISK_SIZE="$DISK_SIZE"
MEMORY="$MEMORY"
CPUS="$CPUS"
SSH_PORT="$SSH_PORT"
GUI_MODE="$GUI_MODE"
VNC_PORT="${VNC_PORT:-}"
NOVNC_PORT="${NOVNC_PORT:-}"
VNC_USERNAME="${VNC_USERNAME:-}"
VNC_PASSWORD="${VNC_PASSWORD:-}"
PORT_FORWARDS="$PORT_FORWARDS"
IMG_FILE="$IMG_FILE"
SEED_FILE="$SEED_FILE"
CREATED="$CREATED"
CPU_MODEL="$CPU_MODEL"
CUSTOM_CPU_STRING="$cpu_string"
SMBIOS_MANUFACTURER="${SMBIOS_MANUFACTURER:-VM}"
SMBIOS_PRODUCT="${SMBIOS_PRODUCT:-VM}"
SMBIOS_VERSION="${SMBIOS_VERSION:-1.0}"
AUTOSTART="false"
EOF
    
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}${BOLD}[✓]${RESET} ${GREEN}Configuration saved${RESET}"
    else
        echo -e "${RED}${BOLD}[✗]${RESET} ${RED}Failed to save configuration${RESET}"
        return 1
    fi
}

# Function to print status
print_status() {
    local type=$1
    local message=$2
    case $type in
        "INFO") echo -e "${BLUE}${BOLD}[ℹ]${RESET} ${BLUE}$message${RESET}" ;;
        "WARN") echo -e "${YELLOW}${BOLD}[⚠]${RESET} ${YELLOW}$message${RESET}" ;;
        "ERROR") echo -e "${RED}${BOLD}[✗]${RESET} ${RED}$message${RESET}" ;;
        "SUCCESS") echo -e "${GREEN}${BOLD}[✓]${RESET} ${GREEN}$message${RESET}" ;;
        "INPUT") echo -ne "${CYAN}${BOLD}[?]${RESET} ${CYAN}$message${RESET}" ;;
    esac
}

# Function to install noVNC and websockify
install_novnc() {
    echo
    echo -e "${CYAN}${BOLD}╔═══════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}${BOLD}║  Installing noVNC & websockify                            ║${RESET}"
    echo -e "${CYAN}${BOLD}╚═══════════════════════════════════════════════════════════╝${RESET}"
    echo
    
    local novnc_dir="$VM_DIR/novnc"
    
    if [ -d "$novnc_dir" ] && [ -f "$novnc_dir/vnc.html" ]; then
        print_status "SUCCESS" "noVNC already installed at $novnc_dir"
    else
        print_status "INFO" "Downloading noVNC..."
        mkdir -p "$novnc_dir"
        if command -v git &> /dev/null; then
            git clone --depth 1 https://github.com/novnc/noVNC.git "$novnc_dir" 2>/dev/null
        fi
        if [ -f "$novnc_dir/vnc.html" ]; then
            print_status "SUCCESS" "noVNC installed"
        else
            print_status "ERROR" "noVNC installation failed"
            return 1
        fi
    fi
    
    if command -v websockify &> /dev/null; then
        print_status "SUCCESS" "websockify is installed"
    else
        print_status "INFO" "Installing websockify..."
        pip3 install websockify --user 2>/dev/null && export PATH="$HOME/.local/bin:$PATH"
        if command -v websockify &> /dev/null; then
            print_status "SUCCESS" "websockify installed"
        else
            print_status "WARN" "websockify not found, try: pip3 install websockify --user"
        fi
    fi
    
    echo
    print_status "SUCCESS" "Installation complete!"
    read -p "$(print_status "INPUT" "Press Enter to continue...")"
}

# Function to setup VM image
setup_vm_image() {
    mkdir -p "$VM_DIR"
    mkdir -p "$VM_DIR/.iso_cache"
    
    local cache_file="$VM_DIR/.iso_cache/${OS_TYPE}.img"
    
    if [[ -f "$cache_file" ]]; then
        print_status "SUCCESS" "Using cached image for $OS_TYPE"
        if [[ ! -f "$IMG_FILE" ]]; then
            cp "$cache_file" "$IMG_FILE"
            print_status "SUCCESS" "Image copied from cache"
        fi
    else
        if [[ -f "$IMG_FILE" ]]; then
            print_status "INFO" "Image file already exists"
        else
            print_status "INFO" "Downloading cloud image for $OS_TYPE..."
            if wget --progress=bar:force:noscroll "$IMG_URL" -O "$IMG_FILE.tmp" 2>&1; then
                mv "$IMG_FILE.tmp" "$IMG_FILE"
                print_status "SUCCESS" "Download completed"
                cp "$IMG_FILE" "$cache_file"
                print_status "SUCCESS" "Image cached"
            else
                print_status "ERROR" "Download failed"
                rm -f "$IMG_FILE.tmp"
                return 1
            fi
        fi
    fi
    
    print_status "INFO" "Resizing disk to $DISK_SIZE..."
    qemu-img resize "$IMG_FILE" "$DISK_SIZE" &>/dev/null || qemu-img create -f qcow2 "$IMG_FILE" "$DISK_SIZE" &>/dev/null
    
    print_status "INFO" "Generating cloud-init..."
    local hashed_password=$(openssl passwd -6 "$PASSWORD" 2>/dev/null)
    
    cat > /tmp/user-data <<EOF
#cloud-config
hostname: $HOSTNAME
ssh_pwauth: true
disable_root: false
chpasswd:
  expire: false
  list: |
    root:$PASSWORD
    $USERNAME:$PASSWORD
users:
  - name: root
    lock_passwd: false
  - name: $USERNAME
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
package_update: false
runcmd:
  - sed -i 's/^#*PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
  - sed -i 's/^#*PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
  - systemctl restart sshd || systemctl restart ssh
EOF
    
    cat > /tmp/meta-data <<EOF
instance-id: iid-$VM_NAME
local-hostname: $HOSTNAME
EOF
    
    cloud-localds "$SEED_FILE" /tmp/user-data /tmp/meta-data &>/dev/null
    rm -f /tmp/user-data /tmp/meta-data
    print_status "SUCCESS" "Cloud-init seed created"
}

# Function to setup Proxmox VM
setup_proxmox_vm() {
    mkdir -p "$VM_DIR"
    mkdir -p "$VM_DIR/.iso_cache"
    local cache_file="$VM_DIR/.iso_cache/proxmox-ve-8.iso"
    
    if [[ -f "$cache_file" ]]; then
        print_status "SUCCESS" "Using cached Proxmox ISO"
        if [[ ! -f "$IMG_FILE" ]]; then
            cp "$cache_file" "$IMG_FILE"
        fi
    else
        if [[ -f "$IMG_FILE" ]]; then
            print_status "INFO" "Proxmox ISO already exists"
        else
            print_status "INFO" "Downloading Proxmox VE ISO (first time)..."
            if wget --progress=bar:force:noscroll "$IMG_URL" -O "$IMG_FILE.tmp" 2>&1; then
                mv "$IMG_FILE.tmp" "$IMG_FILE"
                cp "$IMG_FILE" "$cache_file"
                print_status "SUCCESS" "Downloaded and cached"
            else
                print_status "ERROR" "Download failed"
                rm -f "$IMG_FILE.tmp"
                return 1
            fi
        fi
    fi
    
    local proxmox_disk="$VM_DIR/$VM_NAME-disk.qcow2"
    qemu-img create -f qcow2 "$proxmox_disk" "$DISK_SIZE" &>/dev/null
    SEED_FILE=""
    print_status "SUCCESS" "Proxmox VM setup complete"
}

# Function to start noVNC proxy
start_novnc_proxy() {
    local vm_name=$1
    local vnc_port=$2
    local novnc_port=$3
    
    export PATH="$HOME/.local/bin:$PATH"
    
    local websockify_cmd=""
    command -v websockify &> /dev/null && websockify_cmd="websockify" || [ -f "$HOME/.local/bin/websockify" ] && websockify_cmd="$HOME/.local/bin/websockify"
    
    if [ -z "$websockify_cmd" ]; then
        print_status "WARN" "websockify not found, VNC available on port $vnc_port"
        return 1
    fi
    
    local novnc_dir=""
    for dir in "$VM_DIR/novnc" "$HOME/.novnc" "/usr/share/novnc"; do
        if [ -d "$dir" ] && [ -f "$dir/vnc.html" ]; then
            novnc_dir="$dir"
            break
        fi
    done
    
    if [ -z "$novnc_dir" ]; then
        print_status "WARN" "noVNC not found, installing..."
        install_novnc_no_interactive
        novnc_dir="$VM_DIR/novnc"
    fi
    
    mkdir -p "$VM_DIR/web/$vm_name"
    create_vnc_control_panel "$vm_name" "$novnc_port"
    
    nohup "$websockify_cmd" \
        --web="$VM_DIR/web/$vm_name" \
        --log-file="$VM_DIR/logs/$vm_name-novnc.log" \
        "0.0.0.0:$novnc_port" \
        "localhost:$vnc_port" \
        >> "$VM_DIR/logs/$vm_name-novnc.log" 2>&1 &
    
    local novnc_pid=$!
    echo "$novnc_pid" > "$VM_DIR/pids/$vm_name-novnc.pid"
    sleep 2
    
    if kill -0 $novnc_pid 2>/dev/null; then
        echo
        echo -e "${GREEN}${BOLD}╔═══════════════════════════════════════════════════════════╗${RESET}"
        echo -e "${GREEN}${BOLD}║  Web Console: http://localhost:$novnc_port/vnc.html              ║${RESET}"
        echo -e "${GREEN}${BOLD}╚═══════════════════════════════════════════════════════════╝${RESET}"
        echo
        print_status "SUCCESS" "noVNC started (PID: $novnc_pid)"
    fi
}

# Function to stop noVNC proxy
stop_novnc_proxy() {
    local vm_name=$1
    local pid_file="$VM_DIR/pids/$vm_name-novnc.pid"
    if [ -f "$pid_file" ]; then
        local pid=$(cat "$pid_file")
        kill $pid 2>/dev/null
        rm -f "$pid_file"
    fi
}

# Function to create new VM
create_new_vm() {
    echo
    echo -e "${CYAN}${BOLD}╔═══════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}${BOLD}║  Create New VM                                            ║${RESET}"
    echo -e "${CYAN}${BOLD}╚═══════════════════════════════════════════════════════════╝${RESET}"
    echo
    
    # OS Selection
    echo -e "Select Operating System:"
    echo
    local os_names=()
    local i=1
    for os in "${!OS_OPTIONS[@]}"; do
        echo -e "  ${BOLD}$i)${RESET} $os"
        os_names[$i]="$os"
        ((i++))
    done
    echo
    
    read -p "$(print_status "INPUT" "Choice (1-${#OS_OPTIONS[@]}): ")" choice
    local os="${os_names[$choice]}"
    [[ -z "$os" ]] && { print_status "ERROR" "Invalid choice"; sleep 2; return 1; }
    
    IFS='|' read -r OS_TYPE CODENAME IMG_URL DEFAULT_HOSTNAME DEFAULT_USERNAME DEFAULT_PASSWORD <<< "${OS_OPTIONS[$os]}"
    print_status "SUCCESS" "Selected: $os"
    
    local IS_PROXMOX=false
    [[ "$OS_TYPE" == "proxmox" ]] && IS_PROXMOX=true
    
    # VM Name
    while true; do
        read -p "$(print_status "INPUT" "VM name [${DIM}$DEFAULT_HOSTNAME${RESET}]: ")" VM_NAME
        VM_NAME="${VM_NAME:-$DEFAULT_HOSTNAME}"
        validate_input "name" "$VM_NAME" || { print_status "ERROR" "Invalid name"; continue; }
        [[ -f "$VM_DIR/$VM_NAME.conf" ]] && { print_status "ERROR" "VM already exists"; continue; }
        break
    done
    
    if [ "$IS_PROXMOX" = true ]; then
        HOSTNAME="$VM_NAME"
        USERNAME="root"
        PASSWORD="proxmox"
    else
        read -p "$(print_status "INPUT" "Hostname [${DIM}$VM_NAME${RESET}]: ")" HOSTNAME
        HOSTNAME="${HOSTNAME:-$VM_NAME}"
        read -p "$(print_status "INPUT" "Username [${DIM}$DEFAULT_USERNAME${RESET}]: ")" USERNAME
        USERNAME="${USERNAME:-$DEFAULT_USERNAME}"
        read -s -p "$(print_status "INPUT" "Password [${DIM}hidden${RESET}]: ")" PASSWORD
        echo
        PASSWORD="${PASSWORD:-$DEFAULT_PASSWORD}"
    fi
    
    read -p "$(print_status "INPUT" "Disk size [${DIM}20G${RESET}]: ")" DISK_SIZE
    DISK_SIZE=$(normalize_disk_size "${DISK_SIZE:-20G}")
    
    read -p "$(print_status "INPUT" "Memory in MB [${DIM}2048${RESET}]: ")" MEMORY
    MEMORY="${MEMORY:-2048}"
    
    read -p "$(print_status "INPUT" "CPUs [${DIM}2${RESET}]: ")" CPUS
    CPUS="${CPUS:-2}"
    
    # SSH Port
    while true; do
        read -p "$(print_status "INPUT" "SSH Port [${DIM}2222${RESET}]: ")" SSH_PORT
        SSH_PORT="${SSH_PORT:-2222}"
        validate_input "port" "$SSH_PORT" || continue
        check_port_available "$SSH_PORT" || { print_status "ERROR" "Port in use"; continue; }
        break
    done
    
    # VNC Configuration
    VNC_PORT=""
    NOVNC_PORT=""
    read -p "$(print_status "INPUT" "Enable VNC web console? (y/n) [${DIM}n${RESET}]: ")" gui_input
    if [[ "${gui_input:-n}" =~ ^[Yy]$ ]]; then
        GUI_MODE=true
        local suggested_vnc=$(find_available_vnc_port 5900)
        while true; do
            read -p "$(print_status "INPUT" "VNC Port [${DIM}$suggested_vnc${RESET}]: ")" VNC_PORT
            VNC_PORT="${VNC_PORT:-$suggested_vnc}"
            [ "$VNC_PORT" -lt 5900 ] && { print_status "ERROR" "VNC port must be 5900+"; continue; }
            check_port_available "$VNC_PORT" || { print_status "ERROR" "Port in use"; continue; }
            break
        done
        local suggested_novnc=$(find_available_novnc_port 6080)
        while true; do
            read -p "$(print_status "INPUT" "noVNC Web Port [${DIM}$suggested_novnc${RESET}]: ")" NOVNC_PORT
            NOVNC_PORT="${NOVNC_PORT:-$suggested_novnc}"
            check_port_available "$NOVNC_PORT" || { print_status "ERROR" "Port in use"; continue; }
            break
        done
    else
        GUI_MODE=false
    fi
    
    # Port forwards
    read -p "$(print_status "INPUT" "Additional port forwards (e.g. 8080:80) [${DIM}none${RESET}]: ")" PORT_FORWARDS
    
    IMG_FILE="$VM_DIR/$VM_NAME.img"
    SEED_FILE="$VM_DIR/$VM_NAME-seed.iso"
    CREATED="$(date '+%Y-%m-%d %H:%M:%S')"
    
    echo
    if [ "$IS_PROXMOX" = true ]; then
        setup_proxmox_vm
    else
        setup_vm_image
    fi
    
    save_vm_config
    echo
    print_status "SUCCESS" "VM '$VM_NAME' created!"
    sleep 2
}

# Function to start a VM
start_vm() {
    local vm_name=$1
    load_vm_config "$vm_name" || return 1
    
    echo
    print_status "INFO" "Starting VM: $vm_name"
    echo
    
    [[ ! -f "$IMG_FILE" ]] && { print_status "ERROR" "Image not found"; return 1; }
    
    local IS_PROXMOX=false
    [[ "$OS_TYPE" == "proxmox" ]] && IS_PROXMOX=true
    
    if [ "$IS_PROXMOX" = false ]; then
        [[ ! -f "$SEED_FILE" ]] && { print_status "WARN" "Seed missing, recreating..."; setup_vm_image; }
    fi
    
    check_port_available "$SSH_PORT" || { print_status "ERROR" "SSH port $SSH_PORT in use"; return 1; }
    
    # Build QEMU command
    local qemu_cmd=(qemu-system-x86_64)
    local machine_type=$(qemu-system-x86_64 -machine help 2>/dev/null | grep -E "^pc-i440fx" | head -1 | awk '{print $1}')
    machine_type="${machine_type:-pc}"
    
    qemu_cmd+=(-machine "$machine_type")
    qemu_cmd+=(-name "VM-$VM_NAME,process=vm-$VM_NAME")
    
    if [ "$KVM_AVAILABLE" = true ]; then
        qemu_cmd+=(-enable-kvm)
        local cpu_string="${CPU_MODELS[$CPU_MODEL]}"
        [ -z "$cpu_string" ] && [ -n "$CUSTOM_CPU_STRING" ] && cpu_string="$CUSTOM_CPU_STRING"
        [ -z "$cpu_string" ] && cpu_string="host"
        qemu_cmd+=(-cpu "$cpu_string")
    else
        qemu_cmd+=(-cpu qemu64)
    fi
    
    qemu_cmd+=(-m "$MEMORY" -smp "$CPUS")
    
    # Boot logic
    local boot_marker="$VM_DIR/.$VM_NAME.installed"
    local reboot_marker="$VM_DIR/.$VM_NAME.rebooted"
    
    if [ "$IS_PROXMOX" = true ]; then
        local proxmox_disk="$VM_DIR/$VM_NAME-disk.qcow2"
        [ ! -f "$proxmox_disk" ] && qemu-img create -f qcow2 "$proxmox_disk" "$DISK_SIZE" >/dev/null 2>&1
        
        if [ ! -f "$boot_marker" ]; then
            qemu_cmd+=(-drive "file=$IMG_FILE,media=cdrom,readonly=on" -drive "file=$proxmox_disk,format=qcow2,if=virtio,cache=writeback" -boot order=d)
            print_status "INFO" "Booting from ISO installer"
        elif [ ! -f "$reboot_marker" ]; then
            qemu_cmd+=(-drive "file=$IMG_FILE,media=cdrom,readonly=on" -drive "file=$proxmox_disk,format=qcow2,if=virtio,cache=writeback" -boot order=c)
            print_status "INFO" "Installation phase"
        else
            qemu_cmd+=(-drive "file=$proxmox_disk,format=qcow2,if=virtio,cache=writeback" -boot order=c)
            print_status "SUCCESS" "Booting from disk"
        fi
    else
        if [[ "$IMG_URL" == *".iso"* ]]; then
            local install_disk="$VM_DIR/$VM_NAME-disk.qcow2"
            [ ! -f "$install_disk" ] && qemu-img create -f qcow2 "$install_disk" "$DISK_SIZE" >/dev/null 2>&1
            if [ ! -f "$boot_marker" ]; then
                qemu_cmd+=(-drive "file=$IMG_FILE,media=cdrom,readonly=on" -drive "file=$install_disk,format=qcow2,if=virtio,cache=writeback" -boot order=d)
            else
                qemu_cmd+=(-drive "file=$install_disk,format=qcow2,if=virtio,cache=writeback" -boot order=c)
            fi
        else
            qemu_cmd+=(-drive "file=$IMG_FILE,format=qcow2,if=virtio,cache=writeback" -drive "file=$SEED_FILE,format=raw,if=virtio" -boot order=c)
            touch "$boot_marker"
        fi
    fi
    
    # Network
    local network_config="user,id=n0,hostfwd=tcp::$SSH_PORT-:22"
    if [[ -n "$PORT_FORWARDS" ]]; then
        IFS=',' read -ra forwards <<< "$PORT_FORWARDS"
        for forward in "${forwards[@]}"; do
            forward=$(echo "$forward" | xargs)
            [[ "$forward" =~ ^([0-9]+):([0-9]+)$ ]] && network_config="$network_config,hostfwd=tcp::${BASH_REMATCH[1]}-:${BASH_REMATCH[2]}"
        done
    fi
    qemu_cmd+=(-device virtio-net-pci,netdev=n0 -netdev "$network_config")
    
    # SMBIOS
    qemu_cmd+=(-smbios "type=0,vendor=${SMBIOS_MANUFACTURER:-VM},version=${SMBIOS_VERSION:-1.0}" \
                -smbios "type=1,manufacturer=${SMBIOS_MANUFACTURER:-VM},product=${SMBIOS_PRODUCT:-VM},version=${SMBIOS_VERSION:-1.0}" \
                -smbios "type=2,manufacturer=${SMBIOS_MANUFACTURER:-VM},product=${SMBIOS_PRODUCT:-VM} Motherboard,version=${SMBIOS_VERSION:-1.0}")
    
    # Display
    if [ -n "$VNC_PORT" ]; then
        qemu_cmd+=(-vnc "0.0.0.0:$((VNC_PORT - 5900))")
    else
        qemu_cmd+=(-display none)
    fi
    
    qemu_cmd+=(-daemonize -pidfile "$VM_DIR/pids/$VM_NAME.pid" -serial "file:$VM_DIR/logs/$VM_NAME.log")
    qemu_cmd+=(-device virtio-balloon-pci -object rng-random,filename=/dev/urandom,id=rng0 -device virtio-rng-pci,rng=rng0)
    qemu_cmd+=(-device qemu-xhci,id=xhci -device usb-tablet)
    
    mkdir -p "$VM_DIR/pids" "$VM_DIR/logs"
    
    "${qemu_cmd[@]}" 2>>"$VM_DIR/logs/$VM_NAME.log"
    
    if [ $? -eq 0 ]; then
        sleep 2
        print_status "SUCCESS" "VM '$vm_name' started!"
        echo
        echo -e "  SSH: ${BOLD}ssh $USERNAME@localhost -p $SSH_PORT${RESET}"
        echo -e "  Pass: ${BOLD}$PASSWORD${RESET}"
        
        if [ -n "$VNC_PORT" ] && [ -n "$NOVNC_PORT" ]; then
            start_novnc_proxy "$vm_name" "$VNC_PORT" "$NOVNC_PORT"
        fi
    else
        print_status "ERROR" "Failed to start VM"
        tail -5 "$VM_DIR/logs/$VM_NAME.log"
    fi
    sleep 2
}

# Function to stop a VM
stop_vm() {
    local vm_name=$1
    load_vm_config "$vm_name" || return 1
    
    stop_novnc_proxy "$vm_name"
    
    if ! is_vm_running "$vm_name"; then
        print_status "INFO" "VM not running"
        sleep 1
        return 0
    fi
    
    print_status "INFO" "Stopping VM: $vm_name"
    local pids=$(pgrep -f "vm-$vm_name" 2>/dev/null)
    
    for pid in $pids; do
        kill -TERM "$pid" 2>/dev/null
        sleep 0.5
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null
    done
    
    rm -f "$VM_DIR/pids/$vm_name.pid"
    print_status "SUCCESS" "VM stopped"
    sleep 1
}

# Function to delete a VM
delete_vm() {
    local vm_name=$1
    load_vm_config "$vm_name" || return 1
    
    echo
    print_status "WARN" "This will delete VM '$vm_name' and all data!"
    read -p "$(print_status "INPUT" "Type 'yes' to confirm: ")" confirm
    
    if [[ "$confirm" == "yes" ]]; then
        is_vm_running "$vm_name" && stop_vm "$vm_name"
        stop_novnc_proxy "$vm_name"
        rm -f "$IMG_FILE" "$SEED_FILE" "$VM_DIR/$vm_name.conf" "$VM_DIR/pids/$vm_name.pid" "$VM_DIR/logs/$vm_name.log"
        rm -f "$VM_DIR/.$vm_name.installed" "$VM_DIR/.$vm_name.rebooted"
        print_status "SUCCESS" "VM deleted"
    else
        print_status "INFO" "Cancelled"
    fi
    sleep 1
}

# Function to show VM info
show_vm_info() {
    local vm_name=$1
    load_vm_config "$vm_name" || return 1
    
    local status="${RED}Stopped${RESET}"
    is_vm_running "$vm_name" && status="${GREEN}Running${RESET}"
    
    echo
    echo -e "${CYAN}${BOLD}╔═══════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}${BOLD}║  VM: $vm_name$(printf '%*s' $((43 - ${#vm_name})) '')║${RESET}"
    echo -e "${CYAN}${BOLD}╚═══════════════════════════════════════════════════════════╝${RESET}"
    echo
    echo -e "  Status:     $status"
    echo -e "  OS:         $OS_TYPE"
    echo -e "  Hostname:   $HOSTNAME"
    echo -e "  Username:   $USERNAME"
    echo -e "  Password:   $PASSWORD"
    echo -e "  SSH Port:   $SSH_PORT"
    echo -e "  Memory:     $MEMORY MB"
    echo -e "  CPUs:       $CPUS"
    echo -e "  Disk:       $DISK_SIZE"
    echo -e "  Created:    $CREATED"
    [ -n "$VNC_PORT" ] && echo -e "  VNC Port:   $VNC_PORT"
    [ -n "$NOVNC_PORT" ] && echo -e "  Web Port:   $NOVNC_PORT"
    echo
    echo -e "  ${BOLD}SSH: ssh $USERNAME@localhost -p $SSH_PORT${RESET}"
    echo
    read -p "$(print_status "INPUT" "Press Enter...")"
}

# Function to manage autostart
manage_autostart() {
    local vms=($(get_vm_list))
    [ ${#vms[@]} -eq 0 ] && { print_status "ERROR" "No VMs"; sleep 1; return 1; }
    
    echo
    echo -e "${CYAN}${BOLD}╔═══════════════════════════════════════════════════════════╗${RESET}"
    echo -e "${CYAN}${BOLD}║  Autostart Management                                      ║${RESET}"
    echo -e "${CYAN}${BOLD}╚═══════════════════════════════════════════════════════════╝${RESET}"
    echo
    
    for i in "${!vms[@]}"; do
        local svc="vm-${vms[$i]}.service"
        local status="${RED}Disabled${RESET}"
        sudo test -f "/etc/systemd/system/$svc" 2>/dev/null && status="${GREEN}Enabled${RESET}"
        printf "  %2d) %-20s %b\n" $((i+1)) "${vms[$i]}" "$status"
    done
    
    echo
    read -p "$(print_status "INPUT" "VM number to toggle (0=back): ")" num
    [[ "$num" == "0" ]] && return 0
    local vm_name="${vms[$((num-1))]}"
    [ -z "$vm_name" ] && return 0
    
    local svc="vm-${vm_name}.service"
    if sudo test -f "/etc/systemd/system/$svc" 2>/dev/null; then
        sudo systemctl stop "$svc" 2>/dev/null
        sudo systemctl disable "$svc" 2>/dev/null
        sudo rm -f "/etc/systemd/system/$svc"
        sudo systemctl daemon-reload
        print_status "SUCCESS" "Autostart disabled"
    else
        load_vm_config "$vm_name"
        local service_file="/etc/systemd/system/$svc"
        sudo tee "$service_file" > /dev/null <<EOF
[Unit]
Description=VM - $vm_name
After=network.target

[Service]
Type=forking
ExecStart=$VM_DIR/start-${vm_name}.sh
ExecStop=$VM_DIR/stop-${vm_name}.sh
PIDFile=$VM_DIR/pids/${vm_name}.pid
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
        
        cat > "$VM_DIR/start-${vm_name}.sh" <<STARTEOF
#!/bin/bash
VM_DIR="$VM_DIR"
VM_NAME="$vm_name"
IMG_FILE="$IMG_FILE"
SEED_FILE="$SEED_FILE"
MEMORY="${MEMORY:-2048}"
CPUS="${CPUS:-2}"
SSH_PORT="${SSH_PORT:-2222}"
VNC_PORT="${VNC_PORT:-}"
OS_TYPE="${OS_TYPE:-}"
DISK_SIZE="${DISK_SIZE:-20G}"
PORT_FORWARDS="${PORT_FORWARDS:-}"
SMBIOS_MANUFACTURER="${SMBIOS_MANUFACTURER:-VM}"
SMBIOS_PRODUCT="${SMBIOS_PRODUCT:-VM}"
SMBIOS_VERSION="${SMBIOS_VERSION:-1.0}"

cd "\$VM_DIR"
exec "\$(dirname "\$0")/vms.sh" --start "\$VM_NAME"
STARTEOF
        
        cat > "$VM_DIR/stop-${vm_name}.sh" <<STOPEOF
#!/bin/bash
VM_DIR="$VM_DIR"
VM_NAME="$vm_name"
cd "\$VM_DIR"
exec "\$(dirname "\$0")/vms.sh" --stop "\$VM_NAME"
STOPEOF
        
        chmod +x "$VM_DIR/start-${vm_name}.sh" "$VM_DIR/stop-${vm_name}.sh"
        sudo systemctl enable "$svc" 2>/dev/null
        sudo systemctl daemon-reload
        print_status "SUCCESS" "Autostart enabled"
    fi
    sleep 2
}

# =============================
# Main Menu
# =============================

main_menu() {
    while true; do
        clear
        echo -e "${CYAN}${BOLD}"
        cat << "EOF"
╔═══════════════════════════════════════════════════════════╗
║              VM Manager - vPanel CLI                      ║
╚═══════════════════════════════════════════════════════════╝
EOF
        echo -e "${RESET}"
        
        local vms=($(get_vm_list))
        local vm_count=${#vms[@]}
        
        if [ $vm_count -gt 0 ]; then
            echo -e "${BOLD}VMs:${RESET}"
            for i in "${!vms[@]}"; do
                local vm_name="${vms[$i]}"
                local status="${RED}● Stopped${RESET}"
                is_vm_running "$vm_name" && status="${GREEN}● Running${RESET}"
                printf "  %2d) %-25s %b\n" $((i+1)) "$vm_name" "$status"
            done
            echo
        fi
        
        echo -e "${BOLD}Options:${RESET}"
        echo "  1) Create VM"
        [ $vm_count -gt 0 ] && echo "  2) Start VM     3) Stop VM     4) VM Info"
        [ $vm_count -gt 0 ] && echo "  5) Delete VM    6) Autostart   7) Mark Installed"
        echo "  i) Install noVNC  0) Exit"
        echo
        read -p "$(print_status "INPUT" "Choice: ")" choice
        
        case $choice in
            1) create_new_vm ;;
            2|3|4|5|7)
                [ $vm_count -eq 0 ] && { print_status "ERROR" "No VMs"; sleep 1; continue; }
                read -p "$(print_status "INPUT" "VM number: ")" num
                local vm_name="${vms[$((num-1))]}"
                [ -z "$vm_name" ] && continue
                case $choice in
                    2) start_vm "$vm_name" ;;
                    3) stop_vm "$vm_name" ;;
                    4) show_vm_info "$vm_name" ;;
                    5) delete_vm "$vm_name" ;;
                    7)
                        touch "$VM_DIR/.$vm_name.installed" "$VM_DIR/.$vm_name.rebooted"
                        print_status "SUCCESS" "VM marked as installed"
                        sleep 1
                        ;;
                esac
                ;;
            6) manage_autostart ;;
            i|I) install_novnc ;;
            0) exit 0 ;;
        esac
    done
}

# =============================
# CLI Argument Handling
# =============================

case "${1:-}" in
    --start)
        [ -z "$2" ] && { echo "Usage: $0 --start <vm_name>"; exit 1; }
        start_vm "$2"
        exit $?
        ;;
    --stop)
        [ -z "$2" ] && { echo "Usage: $0 --stop <vm_name>"; exit 1; }
        stop_vm "$2"
        exit $?
        ;;
    --status)
        [ -z "$2" ] && { echo "Usage: $0 --status <vm_name>"; exit 1; }
        is_vm_running "$2" && echo "running" || echo "stopped"
        exit 0
        ;;
    --list)
        get_vm_list
        exit 0
        ;;
    --install-novnc)
        install_novnc
        exit $?
        ;;
esac

# Initialize
clear
check_kvm_support
check_dependencies
mkdir -p "$VM_DIR"

# Trap Ctrl+C
trap 'handle_interrupt' INT TERM

# Start interactive menu
main_menu
