#!/usr/bin/env python3
"""
SCUM Server Manager - CLI Edition (V2: Lean)
=============================================
คำสั่งที่ใช้ได้:
  py runServerV2.py start     - เริ่ม server (foreground)
  py runServerV2.py stop      - หยุด server
  py runServerV2.py restart   - รีสตาร์ท server
"""

import subprocess
import time
import os
import sys
import json
import signal
import socket
import argparse
import configparser
import logging
import threading
import requests
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

# ============ Helpers ============

if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes
    CREATE_NO_WINDOW = 0x08000000

    def _startupinfo():
        """สร้าง STARTUPINFO สำหรับซ่อน console window ของ subprocess."""
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        return si
else:
    CREATE_NO_WINDOW = 0

    def _startupinfo():
        return None


# ============ Configuration ============

@dataclass
class ServerConfig:
    app_id: str = '3792580'
    install_path: str = "Server"
    game_port: str = '20020'
    restart_times: str = '06:00,18:00'
    args: str = '-log -fileopenlog -nobattleye'
    discord_webhook: str = ""
    restart_cooldown: int = 60
    log_retention_days: int = 7
    auto_update: bool = True
    backup_on_start: bool = False


# ============ Logging ============

class ColoredFormatter(logging.Formatter):
    """ANSI color formatter for terminal output."""
    COLORS = {
        'DEBUG': '\033[94m',
        'INFO': '\033[92m',
        'WARNING': '\033[93m',
        'ERROR': '\033[91m',
        'CRITICAL': '\033[91m\033[1m',
        'RESET': '\033[0m',
    }

    def format(self, record):
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        reset = self.COLORS['RESET']
        record.levelname = f"{color}{record.levelname}{reset}"
        return super().format(record)


def setup_logging(log_file: str = 'scum_server.log', level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger('SCUM')
    logger.setLevel(level)
    logger.handlers.clear()

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(level)
    console.setFormatter(ColoredFormatter('[%(asctime)s] %(levelname)s | %(message)s', datefmt='%H:%M:%S'))
    logger.addHandler(console)

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter('[%(asctime)s] [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'))
    logger.addHandler(file_handler)

    return logger


def get_scum_server_name(file_path):
    config = configparser.ConfigParser(interpolation=None)
    config.read(file_path, encoding='utf-8-sig')
    return config.get('General', 'scum.ServerName')


# ============ Banner ============

def print_banner():
    banner = """
    ╔═══════════════════════════════════════════════════════╗
    ║   ███████╗ ██████╗██╗   ██╗███╗   ███╗                ║
    ║   ██╔════╝██╔════╝██║   ██║████╗ ████║                ║
    ║   ███████╗██║     ██║   ██║██╔████╔██║                ║
    ║   ╚════██║██║     ██║   ██║██║╚██╔╝██║                ║
    ║   ███████║╚██████╗╚██████╔╝██║ ╚═╝ ██║                ║
    ║   ╚══════╝ ╚═════╝ ╚═════╝ ╚═╝     ╚═╝                ║
    ║                                                       ║
    ║        Server Manager - CLI Edition v3.0              ║
    ╚═══════════════════════════════════════════════════════╝
    """
    print(banner)


# ============ Admin Check ============

def is_admin() -> bool:
    if sys.platform == 'win32':
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            return False
    return os.geteuid() == 0


# ============ PID Manager (functions, not class) ============

def pid_write(pid: int, pid_file: str = 'scum_server.pid'):
    with open(pid_file, 'w') as f:
        f.write(str(pid))


def pid_read(pid_file: str = 'scum_server.pid') -> Optional[int]:
    try:
        with open(pid_file, 'r') as f:
            return int(f.read().strip())
    except Exception:
        return None


def pid_remove(pid_file: str = 'scum_server.pid'):
    try:
        os.remove(pid_file)
    except OSError:
        pass


def pid_is_running(pid_file: str = 'scum_server.pid') -> bool:
    pid = pid_read(pid_file)
    if not pid:
        return False
    if sys.platform == 'win32':
        si = _startupinfo()
        result = subprocess.run(
            f'tasklist /FI "PID eq {pid}" /NH',
            shell=True, capture_output=True, text=True,
            startupinfo=si, creationflags=CREATE_NO_WINDOW,
        )
        return 'SCUMServer.exe' in result.stdout or 'python' in result.stdout
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


# ============ Discord (module-level function) ============

def send_discord(webhook: str, message: str):
    if not webhook:
        return
    try:
        requests.post(webhook, json={"content": message}, timeout=5)
    except Exception:
        pass


# ============ Steam / Server ============

def get_latest_build(app_id: str) -> str:
    try:
        url = f"https://api.steamcmd.net/v1/info/{app_id}"
        data = requests.get(url, timeout=10).json()
        return str(data['data'][app_id]['depots']['branches']['public']['buildid'])
    except Exception:
        return "unknown"


def run_steamcmd(steamcmd_exe: str, install_path: str, app_id: str, validate: bool = False, logger: logging.Logger = None) -> bool:
    if not os.path.exists(steamcmd_exe):
        if logger:
            logger.error("❌ ไม่พบ steamcmd.exe!")
        return False

    action = "validate" if validate else "update"
    if logger:
        logger.info(f"🔄 กำลัง{action}เกมผ่าน SteamCMD...")

    cmd = [
        steamcmd_exe,
        "+force_install_dir", install_path,
        "+login", "anonymous",
        "+app_update", app_id,
    ]
    if validate:
        cmd.append("validate")
    cmd.append("+quit")

    try:
        si = _startupinfo()
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='ignore',
            startupinfo=si, creationflags=CREATE_NO_WINDOW,
        )

        for line in iter(process.stdout.readline, ''):
            line = line.strip()
            if not line:
                continue
            if "Success" in line or "Installed" in line or "up to date" in line:
                if logger:
                    logger.info(f"  ✓ {line}")
            elif "error" in line.lower():
                if logger:
                    logger.error(f"  ✗ {line}")
            elif "downloading" in line.lower() or "progress" in line.lower():
                print(f"\r  📥 {line}", end='', flush=True)

        process.wait()
        print()
        if logger:
            logger.info("✓ SteamCMD เสร็จสิ้น")
        return True

    except Exception as e:
        if logger:
            logger.error(f"❌ SteamCMD error: {e}")
        return False


def check_server_ready(game_port: str) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(2.0)
        sock.sendto(
            b'\xFF\xFF\xFF\xFF\x54Source Engine Query\x00',
            ("127.0.0.1", int(game_port) + 2),
        )
        data, _ = sock.recvfrom(1024)
        sock.close()
        return bool(data)
    except Exception:
        return False


def launch_server(server_exe: str, game_port: str, args: str, install_path: str, discord_webhook: str, logger: logging.Logger = None) -> bool:
    if not os.path.exists(server_exe):
        if logger:
            logger.error(f"❌ ไม่พบ {server_exe}")
        return False

    cmd = [server_exe, f"-port={game_port}"] + args.split()

    if logger:
        logger.info(f"🚀 กำลังเริ่ม SCUM Server...")
        logger.info(f"   Port: {game_port}")

    try:
        si = _startupinfo()
        process = subprocess.Popen(
            cmd, cwd=os.path.dirname(server_exe),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding='utf-8', errors='ignore', bufsize=1,
            startupinfo=si, creationflags=CREATE_NO_WINDOW,
        )
        pid_write(process.pid)

        try:
            ip = requests.get('https://api.ipify.org', timeout=5).text
        except Exception:
            ip = "Unknown"

        name_server = get_scum_server_name(os.path.join(
            install_path, "SCUM", "Saved", "Config", "WindowsServer", "ServerSettings.ini"
        ))
        send_discord(discord_webhook, f"🖥️ {name_server}\n🟢 ONLINE `{ip}:{int(game_port)+2}`")

        # Read server output in a background thread, log only important lines
        def read_output():
            important = ['error', 'warning', 'joined', 'disconnected', 'ready', 'started']
            for line in iter(process.stdout.readline, ''):
                line = line.strip()
                if not line:
                    continue
                if any(k in line.lower() for k in important):
                    if logger:
                        logger.info(f"[GAME] {line}")

        threading.Thread(target=read_output, daemon=True).start()
        return True

    except Exception as e:
        if logger:
            logger.error(f"❌ ไม่สามารถเริ่ม server: {e}")
        return False


def stop_server(logger: logging.Logger = None) -> bool:
    # --- 1. RCON shutdown ---
    try:
        rcon_path = r".\rcon.exe"
        si = _startupinfo()
        subprocess.run(
            [rcon_path, "-commands", "ShutdownServer Pretty please"],
            capture_output=True, text=True,
            startupinfo=si, creationflags=CREATE_NO_WINDOW,
        )
        time.sleep(5)
    except Exception as e:
        if logger:
            logger.warning(f"RCON shutdown failed or rcon.exe not found: {e}")

    # --- 2. Force kill via PID ---
    pid = pid_read()
    if not pid:
        return True

    try:
        si = _startupinfo()
        subprocess.run(
            f"taskkill /F /T /PID {pid}",
            shell=True, capture_output=True,
            startupinfo=si, creationflags=CREATE_NO_WINDOW,
        )
        pid_remove()
        return True
    except Exception:
        return False


# ============ Config Loading ============

def load_config() -> ServerConfig:
    config = configparser.ConfigParser()
    config.read('config_cli.ini', encoding='utf-8')

    s = dict(config['SETTINGS']) if 'SETTINGS' in config else {}

    defaults = ServerConfig()
    raw_path = s.get('InstallPath', defaults.install_path) or "Server"
    path_server = os.path.abspath(raw_path)

    if not os.path.exists(path_server):
        print(f"📌 ไม่พบโฟลเดอร์: {path_server} กำลังสร้างใหม่...")
        os.makedirs(path_server, exist_ok=True)
    else:
        print(f"✅ พบโฟลเดอร์ Server เดิมที่: {path_server}")

    return ServerConfig(
        install_path=path_server,
        game_port=s.get('GamePort', defaults.game_port),
        restart_times=s.get('RestartTimes', defaults.restart_times),
        args=s.get('Args', defaults.args),
        discord_webhook=s.get('DiscordWebhook', defaults.discord_webhook),
    )


# ============ Main Manager ============

class ScumServerManager:
    def __init__(self):
        self.cfg = load_config()
        self.logger = setup_logging()
        self.running = False
        self.last_restart = 0.0
        self.last_build_id = None
        self.stop_event = threading.Event()

        self.base_dir = os.getcwd()
        self.steamcmd_exe = os.path.join(self.base_dir, "steamcmd", "steamcmd.exe")
        self.server_exe = os.path.join(
            self.cfg.install_path, "SCUM", "Binaries", "Win64", "SCUMServer.exe"
        )

    def _check_build_and_update(self):
        if not self.last_build_id:
            self.last_build_id = get_latest_build(self.cfg.app_id)
            self.logger.info(f"📋 Current build: {self.last_build_id}")
            return

        latest = get_latest_build(self.cfg.app_id)
        if latest != "unknown" and latest != self.last_build_id:
            self.logger.warning(f"🆕 New build detected! {self.last_build_id} → {latest}")
            send_discord(self.cfg.discord_webhook,
                         f"🆕 **Build Update**\nFrom Build: `{self.last_build_id}` ➔ To Build: `{latest}`\n🔄 กำลังรีสตาร์ทเพื่ออัพเดต...")
            self.last_build_id = latest
            self._restart("Auto-update: New build available")

    def _restart(self, reason: str) -> bool:
        now = time.time()
        if now - self.last_restart < self.cfg.restart_cooldown:
            self.logger.warning(f"⏳ รอ cooldown ({self.cfg.restart_cooldown}s)...")
            return False

        self.last_restart = now
        self.logger.warning(f"🔄 Restarting: {reason}")
        send_discord(self.cfg.discord_webhook, f"🔄 เซิร์ฟเวอร์กำลังรีสตาร์ท: {reason}")

        stop_server(self.logger)
        time.sleep(2)

        if self.cfg.auto_update:
            run_steamcmd(self.steamcmd_exe, self.cfg.install_path, self.cfg.app_id, logger=self.logger)
            time.sleep(1)

        return launch_server(self.server_exe, self.cfg.game_port, self.cfg.args,
                             self.cfg.install_path, self.cfg.discord_webhook, self.logger)

    def _main_loop(self):
        """Simple while loop — no schedule library needed."""
        def parse_time(t):
            h, m = t.split(':')
            return int(h) * 60 + int(m)

        scheduled_minutes = sorted(parse_time(t.strip()) for t in self.cfg.restart_times.split(',') if t.strip())
        last_scheduled = None  # ป้องกัน restart ซ้ำในนาทีเดียวกัน

        build_counter = 0

        while pid_is_running():
            # Check scheduled restarts
            current_min = parse_time(datetime.now().strftime('%H:%M'))
            if current_min in scheduled_minutes and current_min != last_scheduled:
                self._restart(f"Scheduled ({current_min // 60:02d}:{current_min % 60:02d})")
                last_scheduled = current_min

            # Build check every ~60s (12 * 5s)
            build_counter += 1
            if build_counter >= 12:
                build_counter = 0
                self._check_build_and_update()

            time.sleep(5)

    # ============ CLI Commands ============

    def cmd_start(self, daemon: bool = False):
        if pid_is_running():
            self.logger.error("⚠️ Server กำลังทำงานอยู่แล้ว!")
            return

        if not daemon:
            print_banner()

        if sys.platform == 'win32' and not is_admin():
            self.logger.warning("⚠️ แนะนำให้รันด้วยสิทธิ์ Administrator")

        self.running = True

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        if self.cfg.auto_update:
            run_steamcmd(self.steamcmd_exe, self.cfg.install_path, self.cfg.app_id, logger=self.logger)

        if not launch_server(self.server_exe, self.cfg.game_port, self.cfg.args,
                             self.cfg.install_path, self.cfg.discord_webhook, self.logger):
            self.logger.error("❌ ไม่สามารถเริ่ม server ได้")
            return

        # Status monitor — inline polling, no extra thread
        def status_loop():
            last_status = None
            while pid_is_running():
                is_ready = check_server_ready(self.cfg.game_port)
                status = "online" if is_ready else "starting"
                if status != last_status:
                    if is_ready:
                        self.logger.info("🟢 ONLINE")
                    else:
                        self.logger.warning("🟡 STARTING / LOADING...")
                    last_status = status
                time.sleep(10)

        threading.Thread(target=status_loop, daemon=True).start()

        self._main_loop()

    def _signal_handler(self, signum, frame):
        self.logger.info("\n🛑 ได้รับสัญญาณหยุด...")
        # Kill server immediately and exit — same logic as cmd_stop
        stop_server(self.logger)
        pid_remove()
        os._exit(0)

    def cmd_stop(self):
        self.logger.info("🛑 กำลังหยุด server...")
        stop_server(self.logger)
        pid_remove()
        self.logger.info("✓ Server หยุดทำงานแล้ว")
        send_discord(self.cfg.discord_webhook, "🛑 เซิร์ฟเวอร์ปิดการใช้งานแล้ว (OFFLINE)")

    def cmd_restart(self):
        if not pid_is_running():
            self.logger.warning("⚠️ Server ไม่ได้ทำงานอยู่ กำลังเริ่มใหม่...")
            self.cmd_start()
            return
        self._restart("Manual Restart")

    def cmd_update(self, validate: bool = False):
        print_banner()
        send_discord(self.cfg.discord_webhook, "🛠️ เริ่มทำการอัพเดตเซิร์ฟเวอร์แบบกำหนดเอง (Manual Update)...")
        run_steamcmd(self.steamcmd_exe, self.cfg.install_path, self.cfg.app_id, validate=validate, logger=self.logger)

    def cmd_status(self):
        print_banner()
        print("\n" + "=" * 60)
        print("📊 SERVER STATUS")
        print("=" * 60)
        print(f"\n⚙️  Configuration:")
        print(f"   Install Path: {self.cfg.install_path}")
        print(f"   Game Port: {self.cfg.game_port}")
        print(f"   Restart Times: {self.cfg.restart_times}")
        print(f"\n🖥️  Process:")
        if pid_is_running():
            pid = pid_read()
            print(f"   Status: 🟢 RUNNING (PID: {pid})")
        else:
            print(f"   Status: 🔴 STOPPED")
        print(f"\n🌐 Server Query:")
        if check_server_ready(self.cfg.game_port):
            print("   Status: ✅ Ready to accept connections")
        else:
            print("   Status: ❌ Not responding")
        print(f"\n🎮 Steam:")
        print(f"   Latest Build: {get_latest_build(self.cfg.app_id)}")

    def cmd_monitor(self):
        print_banner()
        self.logger.info("🔍 Monitor mode - กด Ctrl+C เพื่อออก")
        try:
            while True:
                os.system('cls' if sys.platform == 'win32' else 'clear')
                print_banner()
                print(f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print("-" * 40)
                print(f"PID: {pid_read() or 'None'}")
                ready = check_server_ready(self.cfg.game_port)
                print(f"Status: {'🟢 ONLINE' if ready else '🔴 OFFLINE'}")
                print("-" * 40)
                print("\nกด Ctrl+C เพื่อออก...")
                time.sleep(5)
        except KeyboardInterrupt:
            print("\nออกจาก monitor mode")

    def cmd_backup(self):
        import shutil
        from datetime import datetime

        backup_dir = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        os.makedirs(backup_dir, exist_ok=True)

        for f in ['config_cli.ini', 'scum_server.log']:
            if os.path.exists(f):
                shutil.copy2(f, backup_dir)

        server_config_path = os.path.join(self.cfg.install_path, "SCUM", "Saved", "Config")
        if os.path.exists(server_config_path):
            shutil.copytree(server_config_path, os.path.join(backup_dir, "Config"), dirs_exist_ok=True)

        self.logger.info(f"✓ Backup สร้างที่: {backup_dir}")


# ============ Main ============

def main():
    parser = argparse.ArgumentParser(
        description="SCUM Server Manager - CLI Edition v3.0 (Lean)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="ตัวอย่าง:\n  %(prog)s start\n",
    )
    parser.add_argument('command', choices=['start', 'stop', 'restart'], help='Command')
    args = parser.parse_args()

    manager = ScumServerManager()
    getattr(manager, f'cmd_{args.command}')()


if __name__ == "__main__":
    main()
