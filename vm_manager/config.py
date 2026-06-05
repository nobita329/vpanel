import os
import json
from pathlib import Path

VM_DIR = os.environ.get("VM_DIR", os.path.expanduser("~/vms"))
WEB_DIR = os.path.join(VM_DIR, "web")
PIDS_DIR = os.path.join(VM_DIR, "pids")
CONFIG_DIR = Path(os.path.dirname(os.path.abspath(__file__)))

CPU_MODELS = {
    "AMD Ryzen 9 7950X3D": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 9 7950X3D 16-Core Processor",
    "AMD Ryzen 9 7950X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 9 7950X 16-Core Processor",
    "AMD Ryzen 9 7900X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 9 7900X 12-Core Processor",
    "AMD Ryzen 9 5950X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 9 5950X 16-Core Processor",
    "AMD Ryzen 9 5900X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 9 5900X 12-Core Processor",
    "AMD Ryzen 7 7800X3D": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 7 7800X3D 8-Core Processor",
    "AMD Ryzen 7 7700X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 7 7700X 8-Core Processor",
    "AMD Ryzen 7 5800X3D": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 7 5800X3D 8-Core Processor",
    "AMD Ryzen 7 5800X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 7 5800X 8-Core Processor",
    "AMD Ryzen 5 7600X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 5 7600X 6-Core Processor",
    "AMD Ryzen 5 5600X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen 5 5600X 6-Core Processor",
    "AMD Ryzen Threadripper PRO 5995WX": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen Threadripper PRO 5995WX 64-Cores",
    "AMD Ryzen Threadripper PRO 5975WX": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen Threadripper PRO 5975WX 32-Cores",
    "AMD Ryzen Threadripper 3990X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen Threadripper 3990X 64-Core Processor",
    "AMD Ryzen Threadripper 3970X": "EPYC,vendor=AuthenticAMD,model-id=AMD Ryzen Threadripper 3970X 32-Core Processor",
    "AMD EPYC 9654": "EPYC,vendor=AuthenticAMD,model-id=AMD EPYC 9654 96-Core Processor",
    "AMD EPYC 9554": "EPYC,vendor=AuthenticAMD,model-id=AMD EPYC 9554 64-Core Processor",
    "AMD EPYC 7763": "EPYC,vendor=AuthenticAMD,model-id=AMD EPYC 7763 64-Core Processor",
    "AMD EPYC 7713": "EPYC,vendor=AuthenticAMD,model-id=AMD EPYC 7713 64-Core Processor",
    "AMD EPYC 7543": "EPYC,vendor=AuthenticAMD,model-id=AMD EPYC 7543 32-Core Processor",
    "AMD EPYC 7443": "EPYC,vendor=AuthenticAMD,model-id=AMD EPYC 7443 24-Core Processor",
    "Intel Core i9-14900K": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i9-14900K",
    "Intel Core i9-14900KS": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i9-14900KS",
    "Intel Core i9-13900K": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i9-13900K",
    "Intel Core i9-13900KS": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i9-13900KS",
    "Intel Core i9-12900K": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i9-12900K",
    "Intel Core i7-14700K": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i7-14700K",
    "Intel Core i7-13700K": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i7-13700K",
    "Intel Core i7-12700K": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i7-12700K",
    "Intel Core i5-14600K": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i5-14600K",
    "Intel Core i5-13600K": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Core i5-13600K",
    "Intel Xeon Platinum 8480+": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon Platinum 8480+",
    "Intel Xeon Platinum 8380": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon Platinum 8380",
    "Intel Xeon Gold 6348": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon Gold 6348",
    "Intel Xeon Gold 6338": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon Gold 6338",
    "Intel Xeon Silver 4314": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon Silver 4314",
    "Intel Xeon E5-2690 v4": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon E5-2690 v4",
    "Intel Xeon E5-2680 v4": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon E5-2680 v4",
    "Intel Xeon E-2388G": "Skylake-Server,vendor=GenuineIntel,model-id=Intel Xeon E-2388G",
    "Host CPU (Passthrough)": "host",
    "QEMU Default (qemu64)": "qemu64",
    "Custom CPU Model": "custom",
}

OS_IMAGES = {
    "ubuntu": {
        "24.04": {
            "name": "Ubuntu 24.04 LTS (Noble Numbat)",
            "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        },
        "22.04": {
            "name": "Ubuntu 22.04 LTS (Jammy Jellyfish)",
            "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
        },
        "20.04": {
            "name": "Ubuntu 20.04 LTS (Focal Fossa)",
            "url": "https://cloud-images.ubuntu.com/focal/current/focal-server-cloudimg-amd64.img",
        },
    },
    "debian": {
        "12": {
            "name": "Debian 12 (Bookworm)",
            "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-generic-amd64.qcow2",
        },
        "11": {
            "name": "Debian 11 (Bullseye)",
            "url": "https://cloud.debian.org/images/cloud/bullseye/latest/debian-11-generic-amd64.qcow2",
        },
    },
    "centos": {
        "9": {
            "name": "CentOS Stream 9",
            "url": "https://cloud.centos.org/centos/9-stream/x86_64/images/CentOS-Stream-GenericCloud-9-latest.x86_64.qcow2",
        },
    },
    "fedora": {
        "40": {
            "name": "Fedora 40",
            "url": "https://download.fedoraproject.org/pub/fedora/linux/releases/40/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-40-1.14.x86_64.qcow2",
        },
        "39": {
            "name": "Fedora 39",
            "url": "https://download.fedoraproject.org/pub/fedora/linux/releases/39/Cloud/x86_64/images/Fedora-Cloud-Base-Generic-39-1.5.x86_64.qcow2",
        },
    },
    "arch": {
        "latest": {
            "name": "Arch Linux",
            "url": "https://geo.mirror.pkgbuild.com/images/latest/Arch-Linux-x86_64-cloudimg.qcow2",
        },
    },
    "alpine": {
        "3.20": {
            "name": "Alpine Linux 3.20",
            "url": "https://dl-cdn.alpinelinux.org/alpine/v3.20/releases/cloud/nocloud_alpine-3.20.0-x86_64-cloudimg.qcow2",
        },
    },
}
