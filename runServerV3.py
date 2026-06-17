#!/usr/bin/env python3
"""
SCUM Server Manager - GUI Edition (Integrated V4.1 - Auto Config)
=================================================
เพิ่มระบบตรวจสอบและสร้างไฟล์ config_cli.ini อัตโนมัติหากไม่มีไฟล์หรือมีค่าสูญหาย
"""

import subprocess
import time
import os
import sys
import configparser
import logging
import threading
import socket
import struct
from datetime import datetime
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox

try:
    import requests
except ImportError:
    requests = None

# ============ Helpers & Platform Setup ============

if sys.platform == 'win32':
    import ctypes
    CREATE_NO_WINDOW = 0x08000000
    def _startupinfo():
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0
        return si
else:
    CREATE_NO_WINDOW = 0
    def _startupinfo():
        return None

def is_admin() -> bool:
    if sys.platform == 'win32':
        try:
            return ctypes.windll.shell32.IsUserAnAdmin()
        except Exception:
            return False
    return os.geteuid() == 0

# ============ Configuration Class ============

class ServerConfig:
    def __init__(self):
        self.app_id = '3792580'
        self.install_path = "Server"
        self.game_port = '20020'
        self.restart_times = '06:00,18:00'
        self.args = '-log -fileopenlog -nobattleye'
        self.discord_webhook = ""
        self.restart_cooldown = 60
        self.auto_update = True
        self.server_name = "SCUM SERVER (LOADING...)"
        
        self.rcon_host = '127.0.0.1'
        self.rcon_port = 9010
        self.rcon_password = ''

# ============ Custom GUI Logging Handler ============

class TextWindowLogHandler(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget

    def emit(self, record):
        msg = self.format(record)
        def append():
            if self.text_widget.winfo_exists():
                self.text_widget.configure(state='normal')
                self.text_widget.insert(tk.END, msg + '\n')
                self.text_widget.see(tk.END)
                self.text_widget.configure(state='disabled')
        if self.text_widget.winfo_exists():
            self.text_widget.after(0, append)

# ============ Main GUI & Core Logic App ============

class ScumServerGUIApp:
    def __init__(self, root):
        self.root = root
        self.root.title("SCUM Server Runner Pro v1.4")
        self.root.geometry("1150x760" if sys.platform == 'win32' else "1150x780")
        
        self.bg_main = "#1E1E2E"
        self.bg_panel = "#282A36"
        self.bg_box = "#343746"
        self.fg_text = "#F8F8F2"
        self.fg_dim = "#A5B0CB"
        self.color_accent = "#50FA7B"
        
        self.root.configure(bg=self.bg_main)

        self.cfg = self.load_config()
        self.server_process = None
        self.monitor_thread_active = False
        self.was_server_online = False 
        
        self.base_dir = os.getcwd()
        self.steamcmd_exe = os.path.join(self.base_dir, "steamcmd", "steamcmd.exe")
        self.server_exe = os.path.join(self.cfg.install_path, "SCUM", "Binaries", "Win64", "SCUMServer.exe")
        self.cfg.server_name = self.get_scum_server_name()

        self.logger = logging.getLogger('SCUM_GUI')
        self.logger.setLevel(logging.INFO)

        self.setup_styles()
        self.create_widgets()
        
        ui_handler = TextWindowLogHandler(self.log_area)
        ui_handler.setFormatter(logging.Formatter('[%(asctime)s] %(levelname)s | %(message)s', datefmt='%H:%M:%S'))
        self.logger.addHandler(ui_handler)

        self.running = True
        self.start_background_monitoring()

        self.logger.info("🤖 ยินดีต้อนรับสู่ระบบจัดการ SCUM Server (GUI Mode)")
        self.logger.info(f"📋 ดึงชื่อเซิร์ฟเวอร์เกม: {self.cfg.server_name}")
        if sys.platform == 'win32' and not is_admin():
            self.logger.warning("⚠️ คำเตือน: โปรแกรมไม่ได้เปิดด้วยสิทธิ์ Administrator")

    def load_config(self) -> ServerConfig:
        config_file = 'config_cli.ini'
        config = configparser.ConfigParser()
        
        # อ่านไฟล์ถ้ามีอยู่ (ถ้าไม่มีไฟล์ มันก็จะเป็น Config เปล่าๆ)
        config.read(config_file, encoding='utf-8')
        needs_save = False

        # ตรวจสอบและสร้างหัวข้อ (Sections) หากไม่มี
        if not config.has_section('SETTINGS'):
            config.add_section('SETTINGS')
            needs_save = True
            
        if not config.has_section('rcon'):
            # เผื่อมีคีย์ RCON (ตัวใหญ่) ก็ถือว่ามี
            if config.has_section('RCON'):
                pass
            else:
                config.add_section('rcon')
                needs_save = True

        # ตั้งค่า Defaults ตามที่คุณระบุ
        default_install_path = os.path.join(os.getcwd(), "Server")
        
        defaults_settings = {
            'installpath': default_install_path,
            'gameport': '20020',
            'restarttimes': '06:00,18:00',
            'args': '-log -fileopenlog -nobattleye',
            'discordwebhook': ''
        }
        
        defaults_rcon = {
            'host': '127.0.0.1',
            'port': '9010',
            'password': ''
        }

        # ไล่เช็กและเติมค่าในหมวด SETTINGS
        for key, default_val in defaults_settings.items():
            if not config.has_option('SETTINGS', key):
                config.set('SETTINGS', key, str(default_val))
                needs_save = True

        # ไล่เช็กและเติมค่าในหมวด rcon
        target_rcon_section = 'RCON' if config.has_section('RCON') else 'rcon'
        for key, default_val in defaults_rcon.items():
            if not config.has_option(target_rcon_section, key):
                config.set(target_rcon_section, key, str(default_val))
                needs_save = True

        # ถ้ามีการแก้ไขหรือเติมค่าใหม่ ให้เขียนทับไฟล์
        if needs_save:
            with open(config_file, 'w', encoding='utf-8') as f:
                config.write(f)

        # นำค่าไปใส่ใน ServerConfig Object เพื่อใช้ในโปรแกรม
        cfg = ServerConfig()
        cfg.install_path = os.path.abspath(config.get('SETTINGS', 'installpath'))
        cfg.game_port = config.get('SETTINGS', 'gameport')
        cfg.restart_times = config.get('SETTINGS', 'restarttimes')
        cfg.args = config.get('SETTINGS', 'args')
        cfg.discord_webhook = config.get('SETTINGS', 'discordwebhook')
        
        cfg.rcon_host = config.get(target_rcon_section, 'host')
        cfg.rcon_port = config.getint(target_rcon_section, 'port')
        cfg.rcon_password = config.get(target_rcon_section, 'password')

        # สร้างโฟลเดอร์ Server เตรียมรอไว้เลย ถ้ายังไม่มี
        if not os.path.exists(cfg.install_path):
            os.makedirs(cfg.install_path, exist_ok=True)
            
        return cfg

    def get_scum_server_name(self) -> str:
        ini_path = os.path.join(self.cfg.install_path, "SCUM", "Saved", "Config", "WindowsServer", "ServerSettings.ini")
        if os.path.exists(ini_path):
            try:
                config = configparser.ConfigParser(interpolation=None)
                config.read(ini_path, encoding='utf-8-sig')
                if config.has_option('General', 'scum.ServerName'):
                    return config.get('General', 'scum.ServerName')
            except Exception:
                return "SCUM SERVER (Read Error)"
        return "SCUM SERVER (Ini Not Found)"

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', background=self.bg_main, foreground=self.fg_text)
        style.configure('TLabelframe', background=self.bg_panel, bordercolor=self.bg_box, borderwidth=1)
        style.configure('TLabelframe.Label', background=self.bg_panel, foreground=self.fg_dim, font=('Helvetica', 10, 'bold'))
        style.configure('TLabel', background=self.bg_panel, foreground=self.fg_text, font=('Helvetica', 10))

    def create_widgets(self):
        top_container = tk.Frame(self.root, bg=self.bg_main)
        top_container.pack(fill=tk.X, padx=20, pady=(20, 10))

        left_panel = tk.Frame(top_container, bg=self.bg_main, width=250)
        left_panel.pack(side=tk.LEFT, fill=tk.BOTH, padx=(0, 15))

        services_frame = ttk.LabelFrame(left_panel, text=" SYSTEM SERVICES ")
        services_frame.pack(fill=tk.BOTH, expand=True)

        self.indicator_runner = self.create_indicator_row(services_frame, "Core Manager")
        self.indicator_steam = self.create_indicator_row(services_frame, "SteamCMD.exe")
        self.indicator_rcon = self.create_indicator_row(services_frame, "RCON Status")

        center_panel = tk.Frame(top_container, bg=self.bg_main)
        center_panel.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        dash_frame = ttk.LabelFrame(center_panel, text=" SERVER DASHBOARD ")
        dash_frame.pack(fill=tk.BOTH, expand=True)

        status_box = tk.Frame(dash_frame, bg=self.bg_box, bd=0)
        status_box.pack(fill=tk.X, padx=20, pady=(20, 15))

        self.lbl_server_title = tk.Label(status_box, text=self.cfg.server_name, bg=self.bg_box, fg=self.fg_text, font=('Helvetica', 13, 'bold'), anchor="w")
        self.lbl_server_title.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=15, pady=12)

        self.lbl_status_badge = tk.Label(status_box, text="OFFLINE", bg="#FF5555", fg="#FFFFFF", font=('Helvetica', 9, 'bold'), padx=15, pady=6)
        self.lbl_status_badge.pack(side=tk.RIGHT, padx=15, pady=12)

        info_grid = tk.Frame(dash_frame, bg=self.bg_panel)
        info_grid.pack(fill=tk.X, padx=25, pady=10)

        info_labels = [
            ("Game Port:", self.cfg.game_port),
            ("RCON Port:", str(self.cfg.rcon_port)),
            ("Install Dir:", self.cfg.install_path),
            ("Auto-Restart:", self.cfg.restart_times)
        ]

        for i, (label_text, val_text) in enumerate(info_labels):
            ttk.Label(info_grid, text=label_text, font=('Helvetica', 10, 'bold'), foreground=self.fg_dim).grid(row=i, column=0, sticky=tk.W, pady=6)
            lbl_val = ttk.Label(info_grid, text=val_text, font=('Helvetica', 10))
            lbl_val.grid(row=i, column=1, sticky=tk.W, padx=20, pady=6)

        self.btn_master = tk.Button(dash_frame, text="START SERVER", bg=self.color_accent, fg="#282A36", 
                                    activebackground="#42d468", font=('Helvetica', 12, 'bold'),
                                    command=self.toggle_server_action, bd=0, cursor="hand2", pady=12)
        self.btn_master.pack(fill=tk.X, padx=25, side=tk.BOTTOM, pady=(0, 25))

        right_panel = tk.Frame(top_container, bg=self.bg_main, width=330)
        right_panel.pack(side=tk.RIGHT, fill=tk.BOTH, padx=(15, 0))

        actions_frame = ttk.LabelFrame(right_panel, text=" QUICK ACTIONS ")
        actions_frame.pack(fill=tk.BOTH, expand=True)

        btn_style = {"bg": self.bg_box, "fg": self.fg_text, "activebackground": "#44475A", "activeforeground": "#FFFFFF", "bd": 0, "pady": 6, "font": ('Helvetica', 9), "cursor": "hand2"}
        
        ttk.Label(actions_frame, text="SERVER CONTROLS", foreground=self.fg_dim, font=('Helvetica', 8, 'bold')).pack(anchor=tk.W, padx=15, pady=(15, 4))
        tk.Button(actions_frame, text="Force Restart Server", command=self.action_force_restart, **btn_style).pack(fill=tk.X, padx=15, pady=3)
        tk.Button(actions_frame, text="Manual Update Game", command=self.action_manual_update, **btn_style).pack(fill=tk.X, padx=15, pady=3)
        
        ttk.Label(actions_frame, text="CONFIGURATIONS", foreground=self.fg_dim, font=('Helvetica', 8, 'bold')).pack(anchor=tk.W, padx=15, pady=(15, 4))
        tk.Button(actions_frame, text="Edit config_cli.ini (Manager)", command=self.action_open_config_old, **btn_style).pack(fill=tk.X, padx=15, pady=3)
        tk.Button(actions_frame, text="Edit ServerSettings.ini (Game)", command=self.action_open_config_new, **btn_style).pack(fill=tk.X, padx=15, pady=3)
        
        ttk.Label(actions_frame, text="FOLDERS & BACKUP", foreground=self.fg_dim, font=('Helvetica', 8, 'bold')).pack(anchor=tk.W, padx=15, pady=(15, 4))
        
        folder_frame = tk.Frame(actions_frame, bg=self.bg_panel)
        folder_frame.pack(fill=tk.X, padx=15, pady=3)
        tk.Button(folder_frame, text=r"Content\Paks", command=self.action_open_folder_paks, **btn_style).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        tk.Button(folder_frame, text=r"Binaries\Win64", command=self.action_open_folder_win64, **btn_style).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(3, 0))
        
        tk.Button(actions_frame, text="Backup Server Data", command=self.action_backup_files, bg="#FFB86C", fg="#282A36", activebackground="#E6A661", bd=0, pady=8, font=('Helvetica', 9, 'bold'), cursor="hand2").pack(fill=tk.X, padx=15, pady=(8, 20))

        log_frame = ttk.LabelFrame(self.root, text=" TERMINAL LOG ")
        log_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=(0, 20))

        self.log_area = scrolledtext.ScrolledText(log_frame, bg="#181825", fg="#A6E3A1", insertbackground="white",
                                                  font=("Consolas", 10), bd=0, highlightthickness=0, padx=10, pady=10)
        self.log_area.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        self.log_area.configure(state='disabled')

    def create_indicator_row(self, parent, text):
        frame = tk.Frame(parent, bg=self.bg_panel)
        frame.pack(fill=tk.X, padx=20, pady=15)
        indicator = tk.Label(frame, text="●", fg="#FF5555", bg=self.bg_panel, font=('Helvetica', 12))
        indicator.pack(side=tk.LEFT)
        lbl = tk.Label(frame, text=text, fg=self.fg_text, bg=self.bg_panel, font=('Helvetica', 10))
        lbl.pack(side=tk.LEFT, padx=10)
        return indicator

    # ============ ระบบแจ้งเตือน DISCORD ============
    
    def send_discord_ready(self, server_name):
        if not self.cfg.discord_webhook or requests is None:
            return
        def task():
            try:
                ip = requests.get('https://api.ipify.org', timeout=5).text
            except Exception:
                ip = "Unknown"
            
            query_port = int(self.cfg.game_port) + 2
            msg = f"🖥️ **{server_name}**\n🟢 ONLINE `{ip}:{query_port}`\n✅ RCON Connected & Server Ready!"
            
            try:
                requests.post(self.cfg.discord_webhook, json={"content": msg}, timeout=5)
                self.logger.info("📢 แจ้งเตือน Discord: เซิร์ฟเวอร์ออนไลน์แล้ว!")
            except Exception as e:
                self.logger.warning(f"⚠️ ไม่สามารถส่งข้อความ Discord ได้: {e}")
        threading.Thread(target=task, daemon=True).start()

    def send_discord_offline(self):
        if not self.cfg.discord_webhook or requests is None:
            return
        def task():
            msg = "🛑 เซิร์ฟเวอร์ปิดการใช้งานแล้ว (OFFLINE)"
            try:
                requests.post(self.cfg.discord_webhook, json={"content": msg}, timeout=5)
                self.logger.info("📢 แจ้งเตือน Discord: เซิร์ฟเวอร์ออฟไลน์")
            except Exception as e:
                self.logger.warning(f"⚠️ ไม่สามารถส่งข้อความ Discord ได้: {e}")
        threading.Thread(target=task, daemon=True).start()

    # ============ ลอจิกการทำงานและควบคุมระบบเบื้องหลัง ============

    def start_background_monitoring(self):
        def run_loop():
            self.set_indicator(self.indicator_runner, True)
            while self.running:
                steam_ok = os.path.exists(self.steamcmd_exe)
                self.set_indicator(self.indicator_steam, steam_ok)

                # อัปเกรดระบบตรวจสอบ โดยการยิงคำสั่งเข้าไปดึงเวลาเซิร์ฟเวอร์
                server_online = self.check_rcon_ready(self.cfg.rcon_host, self.cfg.rcon_port, self.cfg.rcon_password)
                self.set_indicator(self.indicator_rcon, server_online)

                current_name = self.get_scum_server_name()
                if self.lbl_server_title.winfo_exists():
                    self.root.after(0, lambda name=current_name: self.lbl_server_title.config(text=name))

                if server_online:
                    self.update_status_ui("ONLINE", self.color_accent, "STOP SERVER", "#FF5555")
                    
                    if not self.was_server_online:
                        self.send_discord_ready(current_name)
                        self.was_server_online = True
                else:
                    if self.server_process and self.server_process.poll() is None:
                        self.update_status_ui("STARTING...", "#F1FA8C", "STOP SERVER", "#FF5555")
                    else:
                        self.update_status_ui("OFFLINE", "#FF5555", "START SERVER", self.color_accent)
                    
                    if self.was_server_online:
                        self.was_server_online = False
                
                time.sleep(4)

        threading.Thread(target=run_loop, daemon=True).start()

    def check_rcon_ready(self, host: str, port: int, password: str) -> bool:
        """ฟังก์ชันเชื่อมต่อ RCON, ล็อกอิน, และส่งคำสั่ง #CheckServerTime เพื่อยืนยันสถานะเซิร์ฟเวอร์"""
        def recvall(sock, count):
            buf = b''
            while count > 0:
                newbuf = sock.recv(count)
                if not newbuf: return None
                buf += newbuf
                count -= len(newbuf)
            return buf

        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2.0)
                s.connect((host, port))
                
                # 1. ยืนยันตัวตน (Auth)
                auth_pkt = struct.pack('<ii', 1, 3) + password.encode('utf-8') + b'\x00\x00'
                s.sendall(struct.pack('<i', len(auth_pkt)) + auth_pkt)
                
                auth_ok = False
                for _ in range(2):
                    size_data = recvall(s, 4)
                    if not size_data: break
                    size = struct.unpack('<i', size_data)[0]
                    data = recvall(s, size)
                    if not data: break
                    resp_id, resp_type = struct.unpack('<ii', data[:8])
                    if resp_type == 2: # รหัสยืนยันผลการล็อกอิน
                        if resp_id == -1: return False # รหัสผ่านผิด
                        auth_ok = True
                        break
                
                if not auth_ok: return False

                # 2. ส่งคำสั่งเช็กเวลา
                cmd = "#CheckServerTime"
                cmd_pkt = struct.pack('<ii', 2, 2) + cmd.encode('utf-8') + b'\x00\x00'
                s.sendall(struct.pack('<i', len(cmd_pkt)) + cmd_pkt)
                
                # 3. อ่านผลลัพธ์ที่เซิร์ฟเวอร์ตอบกลับมา
                size_data = recvall(s, 4)
                if not size_data: return False
                size = struct.unpack('<i', size_data)[0]
                data = recvall(s, size)
                if not data: return False
                
                resp_text = data[8:-2].decode('utf-8', errors='ignore')
                
                # ตรวจสอบว่ามีคำว่า Server local time อยู่ในข้อความตอบกลับหรือไม่
                return "Server local time" in resp_text
        except Exception:
            return False

    def update_status_ui(self, status_text, status_color, btn_text, btn_color):
        def update():
            if self.lbl_status_badge.winfo_exists():
                self.lbl_status_badge.config(text=status_text, bg=status_color, fg="#282A36" if status_text != "OFFLINE" else "#FFFFFF")
                self.btn_master.config(text=btn_text, bg=btn_color, fg="#282A36" if btn_text == "START SERVER" else "#FFFFFF")
        self.root.after(0, update)

    def set_indicator(self, widget, is_ok: bool):
        color = self.color_accent if is_ok else "#FF5555"
        self.root.after(0, lambda: widget.config(fg=color) if widget.winfo_exists() else None)

    # ============ Event Handling ของปุ่มกดต่างๆ ============

    def toggle_server_action(self):
        if self.btn_master.cget('text') == "START SERVER":
            self.logger.info("🎬 เริ่มต้นกระบวนการเปิดระบบเซิร์ฟเวอร์...")
            self.btn_master.config(state=tk.DISABLED)
            
            def run_start_task():
                self.logger.info("🧹 ตรวจสอบและเคลียร์โปรเซสเซิร์ฟเวอร์ที่อาจค้างอยู่เบื้องหลัง...")
                if sys.platform == 'win32':
                    subprocess.run("tasklist /FI \"IMAGENAME eq SCUMServer.exe\" | findstr SCUMServer.exe && taskkill /F /IM SCUMServer.exe", shell=True, startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW)
                    time.sleep(1)

                if self.cfg.auto_update and os.path.exists(self.steamcmd_exe):
                    self.logger.info("🔄 กำลังตรวจสอบอัปเดตตัวเกมผ่าน SteamCMD...")
                    cmd = [self.steamcmd_exe, "+force_install_dir", self.cfg.install_path, "+login", "anonymous", "+app_update", self.cfg.app_id, "+quit"]
                    try:
                        subprocess.run(cmd, startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW, check=True)
                        self.logger.info("✓ ตรวจสอบและอัปเดตโครงสร้างไฟล์เรียบร้อย")
                    except Exception as e:
                        self.logger.error(f"❌ ระบบ SteamCMD อัปเดตล้มเหลว: {e}")

                if not os.path.exists(self.server_exe):
                    self.logger.error(f"❌ ไม่พบไฟล์เซิร์ฟเวอร์เกมที่พาธ: {self.server_exe}")
                    self.root.after(0, lambda: self.btn_master.config(state=tk.NORMAL))
                    return

                cmd_game = [self.server_exe, f"-port={self.cfg.game_port}"] + self.cfg.args.split()
                try:
                    self.server_process = subprocess.Popen(
                        cmd_game, cwd=os.path.dirname(self.server_exe),
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        text=True, encoding='utf-8', errors='ignore',
                        startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW
                    )
                    self.logger.info(f"🚀 บูตตัวเกมเรียบร้อยแล้ว (PID: {self.server_process.pid})")
                    
                    def read_game_output():
                        important = ['error', 'warning', 'joined', 'disconnected', 'ready', 'started']
                        for line in iter(self.server_process.stdout.readline, ''):
                            line = line.strip()
                            if any(k in line.lower() for k in important):
                                self.logger.info(f"[GAME] {line}")
                    threading.Thread(target=read_game_output, daemon=True).start()

                except Exception as e:
                    self.logger.error(f"❌ ไม่สามารถเปิดโปรเซสเซิร์ฟเวอร์เกมได้: {e}")
                
                self.root.after(0, lambda: self.btn_master.config(state=tk.NORMAL))

            threading.Thread(target=run_start_task, daemon=True).start()

        else:
            self.logger.info("🛑 สั่งการระงับและหยุดการทำงานของเซิร์ฟเวอร์...")
            if self.server_process:
                try:
                    if sys.platform == 'win32':
                        subprocess.run(f"taskkill /F /T /PID {self.server_process.pid}", shell=True, startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW)
                    else:
                        self.server_process.terminate()
                    self.logger.info("✓ สั่งหยุดโปรเซสเกมเรียบร้อยแล้ว (OFFLINE)")
                except Exception as e:
                    self.logger.error(f"❌ การสั่งปิดโปรเซสขัดข้อง: {e}")
                self.server_process = None
                self.send_discord_offline()
            else:
                if sys.platform == 'win32':
                    subprocess.run("tasklist /FI \"IMAGENAME eq SCUMServer.exe\" | findstr SCUMServer.exe && taskkill /F /IM SCUMServer.exe", shell=True, startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW)
                    self.logger.info("✓ เคลียร์การทำงานหลงเหลือของ SCUMServer.exe สำเร็จ")
                self.send_discord_offline()

    def action_force_restart(self):
        self.logger.info("⚡ สั่งการรีสตาร์ทเซิร์ฟเวอร์แบบแมนนวลในทันที...")
        if self.server_process or self.check_rcon_ready(self.cfg.rcon_host, self.cfg.rcon_port, self.cfg.rcon_password):
            self.toggle_server_action()
            self.root.after(3000, self.toggle_server_action)
        else:
            self.toggle_server_action()

    def action_manual_update(self):
        self.logger.info("🛠️ เริ่มการอัปเดตไฟล์เกมแบบกำหนดเอง...")
        self.btn_master.config(state=tk.DISABLED)
        def run_manual_update():
            if os.path.exists(self.steamcmd_exe):
                cmd = [self.steamcmd_exe, "+force_install_dir", self.cfg.install_path, "+login", "anonymous", "+app_update", self.cfg.app_id, "validate", "+quit"]
                try:
                    subprocess.run(cmd, startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW, check=True)
                    self.logger.info("✓ ตรวจสอบความถูกต้องและอัปเดตเกมสำเร็จแล้ว")
                except Exception as e:
                    self.logger.error(f"❌ เกิดข้อผิดพลาดในการรัน SteamCMD: {e}")
            else:
                self.logger.error("❌ ไม่พบตัวรันไฟล์โปรแกรม steamcmd.exe")
            self.root.after(0, lambda: self.btn_master.config(state=tk.NORMAL))
        threading.Thread(target=run_manual_update, daemon=True).start()

    def action_open_config_old(self):
        ini_file = 'config_cli.ini'
        if os.path.exists(ini_file):
            os.system(f'start notepad.exe "{ini_file}"' if sys.platform == 'win32' else f'xdg-open "{ini_file}"')
            self.logger.info("📂 เปิดไฟล์คอนฟิกโปรแกรมจัดการ config_cli.ini แล้ว")
        else:
            self.logger.warning("❌ ไม่พบไฟล์คอนฟิก config_cli.ini")

    def action_open_config_new(self):
        game_ini = os.path.join(self.cfg.install_path, "SCUM", "Saved", "Config", "WindowsServer", "ServerSettings.ini")
        if os.path.exists(game_ini):
            os.system(f'start notepad.exe "{game_ini}"' if sys.platform == 'win32' else f'xdg-open "{game_ini}"')
            self.logger.info("📂 เปิดไฟล์ตั้งค่าระบบตัวเกม ServerSettings.ini แล้ว")
        else:
            self.logger.warning("❌ ไม่พบไฟล์คอนฟิก ServerSettings.ini ในโฟลเดอร์เซิร์ฟเวอร์")

    def action_open_folder_paks(self):
        target_dir = os.path.join(self.cfg.install_path, "SCUM", "Content", "Paks")
        if os.path.exists(target_dir):
            if sys.platform == 'win32':
                os.startfile(target_dir)
            self.logger.info("📂 เปิดโฟลเดอร์เซิร์ฟเวอร์เกม: SCUM\\Content\\Paks")
        else:
            self.logger.warning("❌ ไม่พบไดเรกทอรีโฟลเดอร์ Content\\Paks")

    def action_open_folder_win64(self):
        target_dir = os.path.join(self.cfg.install_path, "SCUM", "Binaries", "Win64")
        if os.path.exists(target_dir):
            if sys.platform == 'win32':
                os.startfile(target_dir)
            self.logger.info("📂 เปิดโฟลเดอร์เซิร์ฟเวอร์เกม: SCUM\\Binaries\\Win64")
        else:
            self.logger.warning("❌ ไม่พบไดเรกทอรีโฟลเดอร์ Binaries\\Win64")

    def action_backup_files(self):
        import shutil
        backup_dir = f"backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        try:
            os.makedirs(backup_dir, exist_ok=True)
            self.logger.info(f"🔄 กำลังเตรียมสำรองข้อมูลลงใน: {backup_dir} ...")

            if os.path.exists('config_cli.ini'):
                shutil.copy2('config_cli.ini', backup_dir)
            
            mods_path = os.path.join(self.cfg.install_path, "SCUM", "Content", "Paks", "~mods")
            if os.path.exists(mods_path):
                shutil.copytree(mods_path, os.path.join(backup_dir, "~mods"), dirs_exist_ok=True)
                
            config_path = os.path.join(self.cfg.install_path, "SCUM", "Saved", "Config", "WindowsServer")
            if os.path.exists(config_path):
                shutil.copytree(config_path, os.path.join(backup_dir, "WindowsServer"), dirs_exist_ok=True)
                
            saves_path = os.path.join(self.cfg.install_path, "SCUM", "Saved", "SaveFiles")
            if os.path.exists(saves_path):
                shutil.copytree(saves_path, os.path.join(backup_dir, "SaveFiles"), dirs_exist_ok=True)
                
            self.logger.info(f"💾 สำรองข้อมูล Mods, Config และ SaveFiles เสร็จสมบูรณ์!")
            messagebox.showinfo("Backup Success", f"สำรองข้อมูลสำเร็จ!\nไฟล์ทั้งหมดถูกจัดเก็บไว้ที่โฟลเดอร์:\n{backup_dir}")
        except Exception as e:
            self.logger.error(f"❌ การเขียนข้อมูลสำรองระบบขัดข้อง: {e}")

    def shutdown_app(self):
        self.running = False
        if self.server_process:
            if sys.platform == 'win32':
                subprocess.run(f"taskkill /F /T /PID {self.server_process.pid}", shell=True, startupinfo=_startupinfo(), creationflags=CREATE_NO_WINDOW)
        self.root.destroy()

# ============ Main Entry Point ============

if __name__ == "__main__":
    root = tk.Tk()
    app = ScumServerGUIApp(root)
    root.protocol("WM_DELETE_WINDOW", app.shutdown_app)
    root.mainloop()