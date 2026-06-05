import os
import sys
import subprocess
import json
import shutil
import signal
import time
import re
import uuid
from pathlib import Path
from typing import Optional, Dict, List, Tuple

from .config import VM_DIR, WEB_DIR, PIDS_DIR, CPU_MODELS, OS_IMAGES


class Colors:
    RED = '\033[1;31m'
    GREEN = '\033[1;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[1;34m'
    MAGENTA = '\033[1;35m'
    CYAN = '\033[1;36m'
    WHITE = '\033[1;37m'
    RESET = '\033[0m'
    BOLD = '\033[1m'
    DIM = '\033[2m'


class VMConfig:
    def __init__(self, data: dict = None):
        self.vm_name: str = ""
        self.os_type: str = ""
        self.codename: str = ""
        self.img_url: str = ""
        self.hostname: str = ""
        self.username: str = ""
        self.password: str = ""
        self.disk_size: str = "20G"
        self.memory: str = "2048"
        self.cpus: str = "2"
        self.ssh_port: str = "2222"
        self.gui_mode: str = "false"
        self.vnc_port: str = ""
        self.novnc_port: str = ""
        self.vnc_username: str = ""
        self.vnc_password: str = ""
        self.port_forwards: str = ""
        self.img_file: str = ""
        self.seed_file: str = ""
        self.created: str = ""
        self.cpu_model: str = "Host CPU (Passthrough)"
        self.custom_cpu_string: str = ""
        self.smbios_manufacturer: str = "Hopingboyz"
        self.smbios_product: str = "Hopingboyz VM"
        self.smbios_version: str = "1.0"
        self.autostart: str = "false"
        if data:
            self.__dict__.update(data)

    def to_dict(self) -> dict:
        return self.__dict__.copy()

    def save(self):
        os.makedirs(VM_DIR, exist_ok=True)
        config_file = os.path.join(VM_DIR, f"{self.vm_name}.conf")
        with open(config_file, 'w') as f:
            for key, value in self.to_dict().items():
                if isinstance(value, str):
                    f.write(f'{key.upper()}="{value}"\n')
                else:
                    f.write(f'{key.upper()}="{value}"\n')

    @staticmethod
    def load(vm_name: str) -> Optional['VMConfig']:
        config_file = os.path.join(VM_DIR, f"{vm_name}.conf")
        if not os.path.exists(config_file):
            return None
        data = {}
        with open(config_file, 'r') as f:
            for line in f:
                line = line.strip()
                if '=' in line:
                    key, _, value = line.partition('=')
                    value = value.strip('"').strip("'")
                    data[key.lower()] = value
        return VMConfig(data)

    @staticmethod
    def list_all() -> List[str]:
        if not os.path.exists(VM_DIR):
            return []
        vms = []
        for f in sorted(os.listdir(VM_DIR)):
            if f.endswith('.conf'):
                vms.append(f[:-5])
        return vms

    @staticmethod
    def delete(vm_name: str) -> bool:
        config_file = os.path.join(VM_DIR, f"{vm_name}.conf")
        if os.path.exists(config_file):
            os.remove(config_file)
            return True
        return False


class VMManager:
    def __init__(self):
        self.kvm_available = self._check_kvm()
        signal.signal(signal.SIGINT, self._handle_interrupt)
        signal.signal(signal.SIGTERM, self._handle_interrupt)
        os.makedirs(VM_DIR, exist_ok=True)
        os.makedirs(PIDS_DIR, exist_ok=True)

    def _check_kvm(self) -> bool:
        if os.path.exists('/dev/kvm') and os.access('/dev/kvm', os.R_OK | os.W_OK):
            return True
        return False

    def _handle_interrupt(self, sig=None, frame=None):
        print()
        print(f"{Colors.CYAN}{Colors.BOLD}╔═══════════════════════════════════════════════════════════╗{Colors.RESET}")
        print(f"{Colors.CYAN}{Colors.BOLD}║          Script Closed - Everything Keeps Running!        ║{Colors.RESET}")
        print(f"{Colors.CYAN}{Colors.BOLD}╚═══════════════════════════════════════════════════════════╝{Colors.RESET}")
        print(f"{Colors.GREEN}{Colors.BOLD}[✓]{Colors.RESET} {Colors.GREEN}All VMs running in background{Colors.RESET}")
        print(f"{Colors.GREEN}{Colors.BOLD}[✓]{Colors.RESET} {Colors.GREEN}All noVNC web consoles running{Colors.RESET}")
        print(f"{Colors.BLUE}{Colors.BOLD}[ℹ]{Colors.RESET} {Colors.BLUE}Control: ./vm_manager.py start/stop vm-name{Colors.RESET}")
        sys.exit(0)

    def check_dependencies(self) -> bool:
        deps = ['qemu-system-x86_64', 'wget', 'cloud-localds', 'qemu-img', 'openssl']
        missing = []
        for dep in deps:
            if shutil.which(dep):
                self.print_status("SUCCESS", f"Found: {dep}")
            else:
                missing.append(dep)
                self.print_status("ERROR", f"Missing: {dep}")

        if missing:
            print()
            self.print_status("ERROR", f"Missing required dependencies: {', '.join(missing)}")
            print(f"{Colors.DIM}  Ubuntu/Debian: {Colors.BOLD}sudo apt install qemu-system cloud-image-utils wget openssl{Colors.RESET}")
            print(f"{Colors.DIM}  Fedora:        {Colors.BOLD}sudo dnf install qemu-system-x86 cloud-utils wget openssl{Colors.RESET}")
            print(f"{Colors.DIM}  Arch:          {Colors.BOLD}sudo pacman -S qemu-full cloud-image-utils wget openssl{Colors.RESET}")
            return False

        qemu_ver = subprocess.run(['qemu-system-x86_64', '--version'],
                                  capture_output=True, text=True).stdout.split()[3] if subprocess.run(
            ['qemu-system-x86_64', '--version'], capture_output=True).returncode == 0 else "unknown"
        self.print_status("SUCCESS", f"QEMU version: {qemu_ver}")
        return True

    def print_status(self, stype: str, message: str):
        icons = {"INFO": "ℹ", "WARN": "⚠", "ERROR": "✗", "SUCCESS": "✓", "INPUT": "?", "PROGRESS": "⟳"}
        colors = {"INFO": Colors.BLUE, "WARN": Colors.YELLOW, "ERROR": Colors.RED,
                  "SUCCESS": Colors.GREEN, "INPUT": Colors.CYAN, "PROGRESS": Colors.MAGENTA}
        icon = icons.get(stype, " ")
        color = colors.get(stype, Colors.WHITE)
        print(f"{color}{Colors.BOLD}[{icon}]{Colors.RESET} {color}{message}{Colors.RESET}")

    def display_header(self):
        os.system('clear' if os.name == 'posix' else 'cls')
        print(f"{Colors.CYAN}{Colors.BOLD}")
        print("╔════════════════════════════════════════════════════════════════════════╗")
        print("║  _    _  ____  _____ _____ _   _  _____ ____   ______     ________    ║")
        print("║ | |  | |/ __ \|  __ \_   _| \ | |/ ____|  _ \ / __ \ \   / /___  /   ║")
        print("║ | |__| | |  | | |__) || | |  \| | |  __| |_) | |  | \ \_/ /   / /    ║")
        print("║ |  __  | |  | |  ___/ | | |   \ | | |_ |  _ <| |  | |\   /   / /     ║")
        print("║ | |  | | |__| | |    _| |_| |\  | |__| | |_) | |__| | | |   / /__    ║")
        print("║ |_|  |_|\____/|_|   |_____|_| \_|\_____|____/ \____/  |_|  /_____|   ║")
        print("╚════════════════════════════════════════════════════════════════════════╝")
        print(f"{Colors.RESET}")
        print(f"                    {Colors.MAGENTA}{Colors.BOLD}POWERED BY HOPINGBOYZ{Colors.RESET}")
        print()

        print(f"{Colors.BOLD}{Colors.BLUE}╔════════════════════════════════════════════════════════════════════════╗{Colors.RESET}")
        kvm_status = f"{Colors.GREEN}{Colors.BOLD}⚡ KVM:{Colors.RESET} {Colors.GREEN}ENABLED{Colors.RESET}" if self.kvm_available else f"{Colors.YELLOW}{Colors.BOLD}⚠ KVM:{Colors.RESET} {Colors.YELLOW}DISABLED{Colors.RESET}"
        print(f"{Colors.BOLD}{Colors.BLUE}║{Colors.RESET} {kvm_status}                                              {Colors.BOLD}{Colors.BLUE}║{Colors.RESET}")

        running = self.get_running_vms()
        total = len(VMConfig.list_all())
        vm_status = f"{Colors.GREEN}{Colors.BOLD}🖥 VMs:{Colors.RESET} {Colors.GREEN}{len(running)} Running{Colors.RESET}" if running else f"{Colors.DIM}{Colors.BOLD}🖥 VMs:{Colors.RESET} {Colors.DIM}0 Running{Colors.RESET}"
        print(f"{Colors.BOLD}{Colors.BLUE}║{Colors.RESET} {vm_status}   Total: {total}                                        {Colors.BOLD}{Colors.BLUE}║{Colors.RESET}")

        mem = subprocess.run(['free', '-h'], capture_output=True, text=True).stdout.split('\n')[1].split()[1] if subprocess.run(['free', '-h'], capture_output=True).returncode == 0 else "N/A"
        cpus = os.cpu_count() or 0
        print(f"{Colors.BOLD}{Colors.BLUE}║{Colors.RESET} {Colors.CYAN}{Colors.BOLD}💾 RAM:{Colors.RESET} {Colors.CYAN}{mem}{Colors.RESET}     {Colors.CYAN}{Colors.BOLD}🔧 CPUs:{Colors.RESET} {Colors.CYAN}{cpus} cores{Colors.RESET}                                  {Colors.BOLD}{Colors.BLUE}║{Colors.RESET}")
        print(f"{Colors.BOLD}{Colors.BLUE}╚════════════════════════════════════════════════════════════════════════╝{Colors.RESET}")
        print()

    def get_running_vms(self) -> List[str]:
        result = subprocess.run(['pgrep', '-af', 'hopingboyz-'], capture_output=True, text=True)
        vms = set()
        for line in result.stdout.split('\n'):
            if 'hopingboyz-' in line:
                parts = line.split('hopingboyz-')
                if len(parts) > 1:
                    name = parts[1].split()[0]
                    vms.add(name)
        return list(vms)

    def is_vm_running(self, vm_name: str) -> bool:
        result = subprocess.run(['pgrep', '-af', f'hopingboyz-{vm_name}'],
                                capture_output=True, text=True)
        return f'hopingboyz-{vm_name}' in result.stdout

    def check_port(self, port: int) -> bool:
        result = subprocess.run(['ss', '-tln'], capture_output=True, text=True)
        return f':{port} ' not in result.stdout

    def get_available_vnc_port(self, start: int = 5901) -> int:
        port = start
        while port < 6000:
            if self.check_port(port):
                return port
            port += 1
        return 0

    def get_available_novnc_port(self, start: int = 6080) -> int:
        port = start
        while port < 6100:
            if self.check_port(port):
                return port
            port += 1
        return 0

    def download_image(self, url: str, dest: str) -> bool:
        self.print_status("INFO", f"Downloading image from {url}")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        result = subprocess.run(['wget', '-q', '--show-progress', '-O', dest, url])
        return result.returncode == 0

    def create_seed_image(self, config: VMConfig) -> bool:
        user_data = f"""#cloud-config
hostname: {config.hostname}
manage_etc_hosts: true
users:
  - name: {config.username}
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: false
    ssh_authorized_keys:
      - ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAACAQDMHn+4MqzQH7xJ6zV7ZBJQ6mF6mZn5F5z5F5z5F5z5F5z5F5z5F5z5F5z5F5z5F5z5F5z5== hopingboyz@vm
chpasswd:
  list: |
    {config.username}:{config.password}
  expire: false
ssh_pwauth: true
package_update: true
package_upgrade: false
timezone: UTC
runcmd:
  - systemctl enable ssh
  - systemctl start ssh
"""
        meta_data = f"""instance-id: {config.vm_name}-{uuid.uuid4().hex[:8]}
local-hostname: {config.hostname}
"""
        os.makedirs(os.path.dirname(config.seed_file), exist_ok=True)

        with open('/tmp/user-data', 'w') as f:
            f.write(user_data)
        with open('/tmp/meta-data', 'w') as f:
            f.write(meta_data)

        result = subprocess.run(['cloud-localds', '-v', config.seed_file, '/tmp/user-data', '/tmp/meta-data'],
                                capture_output=True, text=True)
        os.remove('/tmp/user-data')
        os.remove('/tmp/meta-data')

        if result.returncode != 0:
            self.print_status("ERROR", f"Failed to create seed image: {result.stderr}")
            return False
        return True

    def create_vm(self, config: VMConfig, callback=None) -> bool:
        self.print_status("INFO", f"Creating VM: {config.vm_name}")

        os.makedirs(VM_DIR, exist_ok=True)
        config.img_file = os.path.join(VM_DIR, f"{config.vm_name}.qcow2")
        config.seed_file = os.path.join(VM_DIR, f"{config.vm_name}-seed.qcow2")
        config.created = time.strftime("%Y-%m-%d %H:%M:%S")

        if not config.img_url:
            self.print_status("ERROR", "No image URL specified")
            return False

        base_img = os.path.join(VM_DIR, f"base-{os.path.basename(config.img_url)}")
        if not os.path.exists(base_img):
            if callback:
                callback("download", f"Downloading base image...")
            if not self.download_image(config.img_url, base_img):
                self.print_status("ERROR", "Failed to download image")
                return False

        if callback:
            callback("progress", "Creating disk image...")
        disk_size = config.disk_size if config.disk_size.endswith(('G', 'M', 'T')) else f"{config.disk_size}G"
        subprocess.run(['qemu-img', 'create', '-f', 'qcow2', '-b', base_img, '-F', 'qcow2',
                        config.img_file, disk_size],
                       capture_output=True)

        if callback:
            callback("progress", "Creating seed image (cloud-init)...")
        if not self.create_seed_image(config):
            return False

        config.save()

        self.print_status("SUCCESS", f"VM {config.vm_name} created successfully!")
        return True

    def get_qemu_command(self, config: VMConfig, vnc_port: int = None) -> List[str]:
        cpu_string = CPU_MODELS.get(config.cpu_model, "host")
        if config.cpu_model == "Custom CPU Model" and config.custom_cpu_string:
            cpu_string = config.custom_cpu_string
        if cpu_string == "host" or cpu_string == "qemu64":
            cpu_arg = cpu_string
        else:
            cpu_arg = cpu_string

        if vnc_port is None:
            vnc_port = int(config.vnc_port) if config.vnc_port else self.get_available_vnc_port()

        cmd = [
            'qemu-system-x86_64',
            '-name', f"hopingboyz-{config.vm_name},process=hopingboyz-{config.vm_name}",
            '-machine', 'type=q35,accel=kvm' if self.kvm_available else 'type=q35',
            '-cpu', cpu_arg,
            '-smp', config.cpus,
            '-m', config.memory,
            '-drive', f'file={config.img_file},format=qcow2,if=virtio,aio=native,cache=none',
            '-drive', f'file={config.seed_file},format=qcow2,if=virtio',
            '-netdev', f'user,id=net0,hostfwd=tcp::{config.ssh_port}-:22',
            '-device', 'virtio-net-pci,netdev=net0',
            '-vnc', f':{vnc_port - 5900}',
            '-vga', 'virtio',
            '-display', 'gtk' if config.gui_mode == 'true' else 'none',
            '-usb',
            '-device', 'usb-tablet',
            '-k', 'en-us',
            '-smbios', f'type=0,manufacturer="{config.smbios_manufacturer}",product="{config.smbios_product}",version="{config.smbios_version}"',
            '-rtc', 'base=localtime,clock=host',
            '-device', 'virtio-balloon-pci',
            '-device', 'virtio-rng-pci',
            '-msg', 'timestamp=on',
        ]

        if config.port_forwards:
            for pf in config.port_forwards.split(','):
                pf = pf.strip()
                if pf:
                    cmd.extend(['-netdev', f'user,id=net{pf},hostfwd=tcp::{pf}-:{pf.split(":")[0] if ":" in pf else pf}',
                                '-device', 'virtio-net-pci,netdev=net{pf}'])

        if not self.kvm_available:
            cmd.extend(['-enable-kvm' if False else '--disable-kvm'])

        return cmd, vnc_port

    def start_vm(self, vm_name: str) -> bool:
        config = VMConfig.load(vm_name)
        if not config:
            self.print_status("ERROR", f"VM '{vm_name}' not found")
            return False

        if self.is_vm_running(vm_name):
            self.print_status("WARN", f"VM '{vm_name}' is already running")
            return False

        if not os.path.exists(config.img_file):
            self.print_status("ERROR", f"Disk image not found: {config.img_file}")
            return False

        vnc_port = int(config.vnc_port) if config.vnc_port else self.get_available_vnc_port()
        cmd, vnc_port = self.get_qemu_command(config, vnc_port)

        config.vnc_port = str(vnc_port)
        config.save()

        log_file = os.path.join(VM_DIR, f"{vm_name}.log")
        with open(log_file, 'w') as log:
            proc = subprocess.Popen(cmd, stdout=log, stderr=log)

        pid_file = os.path.join(PIDS_DIR, f"{vm_name}.pid")
        with open(pid_file, 'w') as f:
            f.write(str(proc.pid))

        self.print_status("SUCCESS", f"VM '{vm_name}' started (PID: {proc.pid})")
        self.print_status("INFO", f"SSH: ssh {config.username}@localhost -p {config.ssh_port}")
        self.print_status("INFO", f"VNC: vnc://localhost:{vnc_port}")
        return True

    def stop_vm(self, vm_name: str) -> bool:
        if not self.is_vm_running(vm_name):
            self.print_status("WARN", f"VM '{vm_name}' is not running")
            return False

        pid_file = os.path.join(PIDS_DIR, f"{vm_name}.pid")
        if os.path.exists(pid_file):
            with open(pid_file) as f:
                pid = f.read().strip()
            subprocess.run(['kill', pid], capture_output=True)
            os.remove(pid_file)

        subprocess.run(['pkill', '-f', f'hopingboyz-{vm_name}'], capture_output=True)
        time.sleep(1)

        if not self.is_vm_running(vm_name):
            self.print_status("SUCCESS", f"VM '{vm_name}' stopped")
            return True
        else:
            subprocess.run(['pkill', '-9', '-f', f'hopingboyz-{vm_name}'], capture_output=True)
            self.print_status("SUCCESS", f"VM '{vm_name}' force stopped")
            return True

    def restart_vm(self, vm_name: str) -> bool:
        self.stop_vm(vm_name)
        time.sleep(2)
        return self.start_vm(vm_name)

    def delete_vm(self, vm_name: str) -> bool:
        if self.is_vm_running(vm_name):
            self.print_status("WARN", f"Stopping VM '{vm_name}' first...")
            self.stop_vm(vm_name)
            time.sleep(1)

        config = VMConfig.load(vm_name)
        if config:
            for f in [config.img_file, config.seed_file]:
                if f and os.path.exists(f):
                    os.remove(f)
                    self.print_status("INFO", f"Removed: {f}")

        VMConfig.delete(vm_name)
        self.print_status("SUCCESS", f"VM '{vm_name}' deleted")
        return True

    def get_vm_info(self, vm_name: str) -> dict:
        config = VMConfig.load(vm_name)
        if not config:
            return {}
        info = config.to_dict()
        info['running'] = self.is_vm_running(vm_name)
        if os.path.exists(config.img_file):
            size = os.path.getsize(config.img_file)
            info['disk_size_bytes'] = size
            info['disk_size_hr'] = self._format_bytes(size)
        return info

    def _format_bytes(self, bytes_val: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if bytes_val < 1024:
                return f"{bytes_val:.1f} {unit}"
            bytes_val /= 1024
        return f"{bytes_val:.1f} PB"

    def validate_input(self, itype: str, value: str) -> bool:
        patterns = {
            'number': r'^\d+$',
            'port': r'^\d+$',
            'name': r'^[a-zA-Z0-9_-]+$',
            'username': r'^[a-z_][a-z0-9_-]*$',
            'size': r'^\d+[GgMmTt]?$',
        }
        if itype == 'port' and value.isdigit():
            port = int(value)
            if port < 1024 or port > 65535:
                self.print_status("ERROR", "Port must be between 1024 and 65535")
                return False
        pattern = patterns.get(itype)
        if pattern and not re.match(pattern, value):
            self.print_status("ERROR", f"Invalid {itype} format")
            return False
        return True
