#!/usr/bin/env python3
import os
import sys
import argparse
import threading

from .vm_manager import VMManager, VMConfig, Colors
from .config import VM_DIR, CPU_MODELS, OS_IMAGES


manager = VMManager()


def create_vm_interactive():
    print()
    print(f"{Colors.BOLD}{Colors.CYAN}╔═══════════════════════════════════════════════════════════╗{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}║              Create New Virtual Machine                   ║{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.CYAN}╚═══════════════════════════════════════════════════════════╝{Colors.RESET}")
    print()

    config = VMConfig()

    config.vm_name = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}VM Name: {Colors.RESET}").strip()
    while not config.vm_name or not manager.validate_input('name', config.vm_name):
        config.vm_name = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}VM Name (a-z, 0-9, -, _): {Colors.RESET}").strip()

    if VMConfig.load(config.vm_name):
        print(f"{Colors.RED}{Colors.BOLD}[✗]{Colors.RESET} {Colors.RED}VM '{config.vm_name}' already exists!{Colors.RESET}")
        return

    print(f"\n{Colors.BOLD}{Colors.YELLOW}Select OS Type:{Colors.RESET}")
    os_types = list(OS_IMAGES.keys())
    for i, otype in enumerate(os_types, 1):
        print(f"  {Colors.GREEN}{i}{Colors.RESET}. {otype.capitalize()}")

    os_choice = input(f"\n{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}OS [1]: {Colors.RESET}").strip() or "1"
    try:
        os_idx = int(os_choice) - 1
        os_type = os_types[os_idx] if 0 <= os_idx < len(os_types) else os_types[0]
    except ValueError:
        os_type = os_types[0]

    versions = list(OS_IMAGES[os_type].keys())
    if not versions:
        print(f"{Colors.RED}No versions available{Colors.RESET}")
        return

    print(f"\n{Colors.BOLD}{Colors.YELLOW}Select Version:{Colors.RESET}")
    for i, ver in enumerate(versions, 1):
        print(f"  {Colors.GREEN}{i}{Colors.RESET}. {OS_IMAGES[os_type][ver]['name']}")

    ver_choice = input(f"\n{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Version [1]: {Colors.RESET}").strip() or "1"
    try:
        ver_idx = int(ver_choice) - 1
        version = versions[ver_idx] if 0 <= ver_idx < len(versions) else versions[0]
    except ValueError:
        version = versions[0]

    config.os_type = os_type
    config.codename = version
    config.img_url = OS_IMAGES[os_type][version]['url']

    config.hostname = input(f"\n{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Hostname [{config.vm_name}]: {Colors.RESET}").strip() or config.vm_name
    config.username = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Username [hopingboyz]: {Colors.RESET}").strip() or "hopingboyz"
    config.password = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Password [hopingboyz]: {Colors.RESET}").strip() or "hopingboyz"

    config.cpus = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}CPU Cores [2]: {Colors.RESET}").strip() or "2"
    config.memory = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Memory (MB) [2048]: {Colors.RESET}").strip() or "2048"
    config.disk_size = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Disk Size (e.g., 20G, 50G) [20G]: {Colors.RESET}").strip() or "20G"
    config.ssh_port = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}SSH Host Port [2222]: {Colors.RESET}").strip() or "2222"

    print(f"\n{Colors.BOLD}{Colors.YELLOW}Select CPU Model:{Colors.RESET}")
    cpu_names = list(CPU_MODELS.keys())
    for i, name in enumerate(cpu_names, 1):
        marker = " (default)" if name == "Host CPU (Passthrough)" else ""
        print(f"  {Colors.GREEN}{i}{Colors.RESET}. {name}{Colors.DIM}{marker}{Colors.RESET}")

    cpu_choice = input(f"\n{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}CPU Model [1]: {Colors.RESET}").strip() or "1"
    try:
        cpu_idx = int(cpu_choice) - 1
        config.cpu_model = cpu_names[cpu_idx] if 0 <= cpu_idx < len(cpu_names) else cpu_names[0]
    except ValueError:
        config.cpu_model = cpu_names[0]

    if config.cpu_model == "Custom CPU Model":
        config.custom_cpu_string = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Custom CPU string: {Colors.RESET}").strip()

    gui = input(f"\n{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Enable GUI display? (y/N): {Colors.RESET}").strip().lower()
    config.gui_mode = "true" if gui == "y" else "false"

    print(f"\n{Colors.BOLD}{Colors.YELLOW}Summary:{Colors.RESET}")
    print(f"  {Colors.CYAN}Name:{Colors.RESET}     {config.vm_name}")
    print(f"  {Colors.CYAN}OS:{Colors.RESET}       {config.os_type} {config.codename}")
    print(f"  {Colors.CYAN}CPU:{Colors.RESET}       {config.cpus} cores ({config.cpu_model})")
    print(f"  {Colors.CYAN}RAM:{Colors.RESET}       {config.memory} MB")
    print(f"  {Colors.CYAN}Disk:{Colors.RESET}      {config.disk_size}")
    print(f"  {Colors.CYAN}SSH:{Colors.RESET}       localhost:{config.ssh_port}")
    print(f"  {Colors.CYAN}User:{Colors.RESET}      {config.username}:{config.password}")

    confirm = input(f"\n{Colors.YELLOW}{Colors.BOLD}[?]{Colors.RESET} {Colors.YELLOW}Create VM? (Y/n): {Colors.RESET}").strip().lower()
    if confirm == "n":
        print(f"{Colors.YELLOW}Cancelled.{Colors.RESET}")
        return

    def progress_callback(ctype, msg):
        if ctype == "download":
            print(f"{Colors.MAGENTA}{Colors.BOLD}[⟳]{Colors.RESET} {Colors.MAGENTA}{msg}{Colors.RESET}")
        elif ctype == "progress":
            print(f"{Colors.BLUE}{Colors.BOLD}[ℹ]{Colors.RESET} {Colors.BLUE}{msg}{Colors.RESET}")

    if manager.create_vm(config, progress_callback):
        print(f"\n{Colors.GREEN}{Colors.BOLD}[✓]{Colors.RESET} {Colors.GREEN}VM '{config.vm_name}' created!{Colors.RESET}")
        print(f"{Colors.BLUE}{Colors.BOLD}[ℹ]{Colors.RESET} {Colors.BLUE}Start it: python3 -m vm_manager start {config.vm_name}{Colors.RESET}")


def list_vms():
    vms = VMConfig.list_all()
    if not vms:
        print(f"{Colors.YELLOW}No VMs found.{Colors.RESET}")
        return
    print(f"\n{Colors.BOLD}{Colors.CYAN}Virtual Machines:{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 70}{Colors.RESET}")
    print(f"{Colors.BOLD}{'NAME':<20} {'STATUS':<10} {'CPU':<8} {'RAM':<8} {'SSH':<8} {'VNC':<8}{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 70}{Colors.RESET}")
    for name in vms:
        config = VMConfig.load(name)
        running = manager.is_vm_running(name)
        status = f"{Colors.GREEN}Running{Colors.RESET}" if running else f"{Colors.RED}Stopped{Colors.RESET}"
        cpus = config.cpus if config else "?"
        mem = config.memory if config else "?"
        ssh = config.ssh_port if config else "?"
        vnc = config.vnc_port if config and config.vnc_port else "-"
        print(f"{Colors.WHITE}{name:<20}{Colors.RESET} {status:<22} {cpus:<8} {mem:<8} {ssh:<8} {vnc:<8}")
    print(f"{Colors.DIM}{'─' * 70}{Colors.RESET}")


def show_vm_info(name):
    config = VMConfig.load(name)
    if not config:
        print(f"{Colors.RED}VM '{name}' not found{Colors.RESET}")
        return
    running = manager.is_vm_running(name)
    info = manager.get_vm_info(name)

    print(f"\n{Colors.BOLD}{Colors.CYAN}VM Information: {name}{Colors.RESET}")
    print(f"{Colors.DIM}{'─' * 50}{Colors.RESET}")
    print(f"{Colors.WHITE}Name:{Colors.RESET}          {name}")
    print(f"{Colors.WHITE}Status:{Colors.RESET}        {'🟢 Running' if running else '🔴 Stopped'}")
    print(f"{Colors.WHITE}OS:{Colors.RESET}            {config.os_type}/{config.codename}")
    print(f"{Colors.WHITE}CPU:{Colors.RESET}           {config.cpus} cores ({config.cpu_model})")
    print(f"{Colors.WHITE}Memory:{Colors.RESET}        {config.memory} MB")
    print(f"{Colors.WHITE}Disk Size:{Colors.RESET}     {config.disk_size}")
    if 'disk_size_hr' in info:
        print(f"{Colors.WHITE}Disk Used:{Colors.RESET}     {info['disk_size_hr']}")
    print(f"{Colors.WHITE}SSH Port:{Colors.RESET}      localhost:{config.ssh_port}")
    print(f"{Colors.WHITE}VNC Port:{Colors.RESET}      {config.vnc_port or 'Not started'}")
    print(f"{Colors.WHITE}Username:{Colors.RESET}      {config.username}")
    print(f"{Colors.WHITE}Hostname:{Colors.RESET}      {config.hostname}")
    print(f"{Colors.WHITE}GUI Mode:{Colors.RESET}      {config.gui_mode}")
    print(f"{Colors.WHITE}Created:{Colors.RESET}       {config.created}")
    print(f"{Colors.WHITE}Image:{Colors.RESET}         {config.img_file}")
    print(f"{Colors.DIM}{'─' * 50}{Colors.RESET}")


def show_menu():
    manager.display_header()
    print(f"{Colors.BOLD}{Colors.YELLOW}Main Menu:{Colors.RESET}")
    print(f"  {Colors.GREEN}1{Colors.RESET}. Create VM")
    print(f"  {Colors.GREEN}2{Colors.RESET}. List VMs")
    print(f"  {Colors.GREEN}3{Colors.RESET}. Start VM")
    print(f"  {Colors.GREEN}4{Colors.RESET}. Stop VM")
    print(f"  {Colors.GREEN}5{Colors.RESET}. Restart VM")
    print(f"  {Colors.GREEN}6{Colors.RESET}. VM Info")
    print(f"  {Colors.GREEN}7{Colors.RESET}. Delete VM")
    print(f"  {Colors.GREEN}8{Colors.RESET}. Start Web Panel")
    print(f"  {Colors.GREEN}9{Colors.RESET}. Check Dependencies")
    print(f"  {Colors.GREEN}0{Colors.RESET}. Exit")
    print()


def start_web_panel():
    from .web_panel import run_web_panel

    username = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Web Username (leave empty for no auth): {Colors.RESET}").strip() or None
    password = None
    if username:
        password = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Web Password: {Colors.RESET}").strip() or None

    port = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Web Panel Port [8080]: {Colors.RESET}").strip() or "8080"

    print(f"\n{Colors.GREEN}Starting web panel...{Colors.RESET}")
    run_web_panel(host='0.0.0.0', port=int(port), username=username, password=password)


def main():
    parser = argparse.ArgumentParser(
        description="Hopingboyz VM Manager - Python Edition",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Examples:
  python3 -m vm_manager create              # Interactive VM creation
  python3 -m vm_manager list                # List all VMs
  python3 -m vm_manager start my-vm         # Start a VM
  python3 -m vm_manager stop my-vm          # Stop a VM
  python3 -m vm_manager restart my-vm       # Restart a VM
  python3 -m vm_manager info my-vm          # Show VM info
  python3 -m vm_manager delete my-vm        # Delete a VM
  python3 -m vm_manager web                 # Start web control panel
        """
    )

    parser.add_argument('command', nargs='?', default='menu',
                        choices=['menu', 'create', 'list', 'start', 'stop', 'restart', 'info', 'delete', 'web', 'check'],
                        help='Command to execute')
    parser.add_argument('name', nargs='?', help='VM name')

    args = parser.parse_args()

    if args.command == 'menu':
        while True:
            show_menu()
            choice = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}Select option: {Colors.RESET}").strip()

            if choice == '1':
                create_vm_interactive()
            elif choice == '2':
                list_vms()
            elif choice == '3':
                name = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}VM name: {Colors.RESET}").strip()
                if name:
                    manager.start_vm(name)
            elif choice == '4':
                name = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}VM name: {Colors.RESET}").strip()
                if name:
                    manager.stop_vm(name)
            elif choice == '5':
                name = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}VM name: {Colors.RESET}").strip()
                if name:
                    manager.restart_vm(name)
            elif choice == '6':
                name = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}VM name: {Colors.RESET}").strip()
                if name:
                    show_vm_info(name)
            elif choice == '7':
                name = input(f"{Colors.CYAN}{Colors.BOLD}[?]{Colors.RESET} {Colors.CYAN}VM name: {Colors.RESET}").strip()
                if name:
                    if input(f"{Colors.RED}Delete '{name}'? (y/N): {Colors.RESET}").strip().lower() == 'y':
                        manager.delete_vm(name)
            elif choice == '8':
                start_web_panel()
            elif choice == '9':
                manager.check_dependencies()
            elif choice == '0':
                print(f"\n{Colors.GREEN}Goodbye!{Colors.RESET}")
                break
            else:
                print(f"{Colors.RED}Invalid option{Colors.RESET}")

            if choice != '0':
                input(f"\n{Colors.DIM}Press Enter to continue...{Colors.RESET}")

    elif args.command == 'create':
        create_vm_interactive()
    elif args.command == 'list':
        list_vms()
    elif args.command == 'start':
        if args.name:
            manager.start_vm(args.name)
        else:
            print(f"{Colors.RED}Usage: python3 -m vm_manager start <vm-name>{Colors.RESET}")
    elif args.command == 'stop':
        if args.name:
            manager.stop_vm(args.name)
        else:
            print(f"{Colors.RED}Usage: python3 -m vm_manager stop <vm-name>{Colors.RESET}")
    elif args.command == 'restart':
        if args.name:
            manager.restart_vm(args.name)
        else:
            print(f"{Colors.RED}Usage: python3 -m vm_manager restart <vm-name>{Colors.RESET}")
    elif args.command == 'info':
        if args.name:
            show_vm_info(args.name)
        else:
            print(f"{Colors.RED}Usage: python3 -m vm_manager info <vm-name>{Colors.RESET}")
    elif args.command == 'delete':
        if args.name:
            manager.delete_vm(args.name)
        else:
            print(f"{Colors.RED}Usage: python3 -m vm_manager delete <vm-name>{Colors.RESET}")
    elif args.command == 'web':
        start_web_panel()
    elif args.command == 'check':
        manager.check_dependencies()


if __name__ == '__main__':
    main()
