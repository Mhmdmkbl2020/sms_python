import os
import sys
import time
import json
import logging
import serial
import subprocess
from threading import Thread, Event
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from PyPDF2 import PdfReader
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from webdriver_manager.firefox import GeckoDriverManager
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

# ---------------------- الإعدادات العامة ----------------------
BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
MONITOR_DIR = os.path.join(BASE_DIR, 'pdf_files')
BT_TOOLS_DIR = os.path.join(BASE_DIR, 'bluetooth_tools')

os.makedirs(MONITOR_DIR, exist_ok=True)
os.makedirs(BT_TOOLS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(os.path.join(BASE_DIR, 'service.log')),
        logging.StreamHandler()
    ]
)

# ---------------------- إدارة الإعدادات ----------------------
class ConfigManager:
    @staticmethod
    def load_config():
        default_config = {
            'whatsapp': True,
            'sms': True,
            'com_port': 'COM3'
        }
        try:
            with open(CONFIG_FILE, 'r') as f:
                return {**default_config, **json.load(f)}
        except FileNotFoundError:
            return default_config

    @staticmethod
    def save_config(config):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)

# ---------------------- إدارة البلوتوث ----------------------
class BluetoothManager:
    def __init__(self):
        self.config = ConfigManager.load_config()
        
    def get_bt_tool_path(self, tool_name):
        local_path = os.path.join(BT_TOOLS_DIR, f"{tool_name}.exe")
        if os.path.exists(local_path):
            return local_path
        raise FileNotFoundError(f"{tool_name} not found")

    def pair_device(self, address):
        try:
            subprocess.run(
                [self.get_bt_tool_path('btpair'), '-c', address],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            return True
        except Exception as e:
            logging.error(f"Pairing failed: {str(e)}")
            return False

# ---------------------- الخدمة الرئيسية ----------------------
class ServiceManager:
    def __init__(self):
        self.config = ConfigManager.load_config()
        self.bluetooth = BluetoothManager()
        self.observer = Observer()
        self.driver = None
        self.init_browser()
        
    def init_browser(self):
        if self.config['whatsapp']:
            try:
                options = Options()
                options.add_argument("--headless")
                service = Service(GeckoDriverManager().cache_manager.install())
                self.driver = webdriver.Firefox(service=service, options=options)
                self.driver.get("https://web.whatsapp.com")
                time.sleep(15)
            except Exception as e:
                logging.error(f"Browser init failed: {str(e)}")
    
    def start_monitoring(self):
        self.observer.schedule(PDFHandler(self), MONITOR_DIR, recursive=False)
        self.observer.start()
    
    def stop_monitoring(self):
        self.observer.stop()
        self.observer.join()
        if self.driver:
            self.driver.quit()

# ---------------------- واجهة التحكم ----------------------
class ControlPanel(tk.Tk):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.title("Control Panel")
        self.geometry("500x350")
        
        self.create_widgets()
        self.update_status()
    
    def create_widgets(self):
        # إطار حالة الأجهزة
        status_frame = ttk.LabelFrame(self, text="حالة الأجهزة")
        status_frame.pack(pady=10, fill='x', padx=10)
        
        # مؤشر GSM
        self.gsm_label = ttk.Label(status_frame, text="مودم GSM: غير متصل")
        self.gsm_label.grid(row=0, column=0, padx=10)
        
        # قائمة المنافذ
        self.port_var = tk.StringVar(value=self.manager.config['com_port'])
        self.port_menu = ttk.Combobox(status_frame, textvariable=self.port_var)
        self.port_menu.grid(row=0, column=1, padx=10)
        ttk.Button(
            status_frame,
            text="تحديث المنافذ",
            command=self.refresh_ports
        ).grid(row=0, column=2, padx=10)
        
        # مؤشر البلوتوث
        self.bt_label = ttk.Label(status_frame, text="بلوتوث: غير متصل")
        self.bt_label.grid(row=1, column=0, pady=10)
        
        # إطار التحكم
        control_frame = ttk.LabelFrame(self, text="التحكم بالخدمات")
        control_frame.pack(pady=10, fill='x', padx=10)
        
        ttk.Checkbutton(
            control_frame,
            text="تفعيل الواتساب",
            variable=tk.BooleanVar(value=self.manager.config['whatsapp']),
            command=lambda: self.toggle_service('whatsapp')
        ).pack(pady=5, anchor='w')
        
        ttk.Checkbutton(
            control_frame,
            text="تفعيل الرسائل النصية",
            variable=tk.BooleanVar(value=self.manager.config['sms']),
            command=lambda: self.toggle_service('sms')
        ).pack(pady=5, anchor='w')
        
        ttk.Button(
            control_frame,
            text="إقران جهاز بلوتوث",
            command=self.show_bluetooth_dialog
        ).pack(pady=10)
        
        ttk.Button(
            self,
            text="خروج",
            command=self.destroy
        ).pack(pady=10)
    
    def toggle_service(self, service_name):
        new_state = not self.manager.config[service_name]
        self.manager.config[service_name] = new_state
        ConfigManager.save_config(self.manager.config)
        if service_name == 'whatsapp' and new_state:
            self.manager.init_browser()
    
    def refresh_ports(self):
        ports = []
        for port in ['COM%s' % (i + 1) for i in range(256)]:
            try:
                s = serial.Serial(port)
                s.close()
                ports.append(port)
            except:
                pass
        self.port_menu['values'] = ports
        self.manager.config['com_port'] = self.port_var.get()
        ConfigManager.save_config(self.manager.config)
    
    def update_status(self):
        # تحديث حالة المودم
        try:
            with serial.Serial(self.manager.config['com_port'], timeout=1):
                self.gsm_label.config(text="مودم GSM: متصل")
        except:
            self.gsm_label.config(text="مودم GSM: غير متصل")
        
        # تحديث حالة البلوتوث
        self.after(1000, self.update_status)
    
    def show_bluetooth_dialog(self):
        # ... (كود إدارة البلوتوث السابق) ...

if __name__ == "__main__":
    service = ServiceManager()
    service.start_monitoring()
    app = ControlPanel(service)
    app.mainloop()
    service.stop_monitoring()
