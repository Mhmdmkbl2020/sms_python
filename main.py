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
from tkinter import ttk, messagebox

# ---------------------- الإعدادات العامة ----------------------
BASE_DIR = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(__file__)
CONFIG_FILE = os.path.join(BASE_DIR, 'config.json')
MONITOR_DIR = os.path.join(BASE_DIR, 'pdf_files')
os.makedirs(MONITOR_DIR, exist_ok=True)

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
        try:
            with open(CONFIG_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {'whatsapp': True, 'sms': True, 'bluetooth_device': None}

    @staticmethod
    def save_config(config):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)

# ---------------------- إدارة البلوتوث ----------------------
class BluetoothManager:
    def __init__(self):
        self.config = ConfigManager.load_config()
        self.connected_device = self.config.get('bluetooth_device')
        
    def discover_devices(self):
        try:
            result = subprocess.run(
                ['btpair', '-l'],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            devices = []
            for line in result.stdout.split('\n'):
                if '|' in line:
                    parts = line.split('|')
                    devices.append({
                        'name': parts[0].strip(),
                        'address': parts[1].strip()
                    })
            return devices
        except Exception as e:
            logging.error(f"Bluetooth error: {str(e)}")
            return []
        
    def pair_device(self, address):
        try:
            subprocess.run(
                ['btpair', '-c', address],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW
            )
            self.connected_device = address
            self.config['bluetooth_device'] = address
            ConfigManager.save_config(self.config)
            return True
        except Exception as e:
            logging.error(f"Pairing failed: {str(e)}")
            return False

# ---------------------- الخدمة الرئيسية ----------------------
class ServiceManager:
    def __init__(self):
        self.config = ConfigManager.load_config()
        self.whatsapp_enabled = self.config['whatsapp']
        self.sms_enabled = self.config['sms']
        self.bluetooth = BluetoothManager()
        self.driver = None
        self.observer = Observer()
        self.init_browser()
        
    def init_browser(self):
        if self.whatsapp_enabled:
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
        logging.info("Service started")
    
    def stop_monitoring(self):
        self.observer.stop()
        self.observer.join()
        if self.driver:
            self.driver.quit()
        logging.info("Service stopped")
    
    def toggle_service(self, service_name, state):
        self.config[service_name] = state
        ConfigManager.save_config(self.config)
        if service_name == 'whatsapp' and state and not self.driver:
            self.init_browser()

# ---------------------- معالجة الملفات ----------------------
class PDFHandler(FileSystemEventHandler):
    def __init__(self, manager):
        self.manager = manager
    
    def on_created(self, event):
        if not event.is_directory and event.src_path.endswith('.pdf'):
            Thread(target=self.process_pdf, args=(event.src_path,)).start()
    
    def process_pdf(self, path):
        try:
            with open(path, 'rb') as f:
                reader = PdfReader(f)
                text = '\n'.join([page.extract_text() or '' for page in reader.pages])
            
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            if len(lines) < 2:
                raise ValueError("Invalid PDF format")
            
            number = lines[0].lstrip('+').replace(' ', '')
            message = lines[1]
            
            if not (number.isdigit() and len(number) == 9):
                raise ValueError("Invalid phone number")
            
            full_number = f"966{number}"
            
            if self.manager.sms_enabled:
                self.send_sms(full_number, message)
            
            if self.manager.whatsapp_enabled and self.manager.driver:
                self.send_whatsapp(path, full_number, message)
            
            os.remove(path)
            logging.info(f"Processed: {os.path.basename(path)}")
            
        except Exception as e:
            logging.error(f"Processing error: {str(e)}")
            os.rename(path, f"{path}.error")

    def send_sms(self, number, message):
        try:
            # إرسال عبر البلوتوث إذا كان مفعلاً
            if self.manager.bluetooth.connected_device:
                subprocess.run(
                    ['btcom', '-b', self.manager.bluetooth.connected_device, '-s', f"SMS:{number}:{message}"],
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
            else:
                with serial.Serial('COM3', 9600, timeout=1) as modem:
                    modem.write(b'AT+CMGF=1\r')
                    modem.write(f'AT+CMGS="{number}"\r'.encode() + message.encode() + b'\x1A')
        except Exception as e:
            logging.error(f"SMS failed: {str(e)}")

    def send_whatsapp(self, path, number, message):
        try:
            self.manager.driver.find_element(By.XPATH, '//div[@role="textbox"]').send_keys(number + Keys.ENTER)
            time.sleep(2)
            self.manager.driver.find_element(By.XPATH, '//div[@role="textbox"]').send_keys(message + Keys.ENTER)
            self.manager.driver.find_element(By.XPATH, '//div[@title="إرفاق"]').click()
            file_input = self.manager.driver.find_element(By.XPATH, '//input[@type="file"]')
            file_input.send_keys(os.path.abspath(path))
            time.sleep(2)
            self.manager.driver.find_element(By.XPATH, '//div[@aria-label="إرسال"]').click()
        except Exception as e:
            logging.error(f"WhatsApp failed: {str(e)}")
            self.manager.init_browser()

# ---------------------- واجهة التحكم ----------------------
class BluetoothPairingDialog(tk.Toplevel):
    def __init__(self, parent, bluetooth_manager):
        super().__init__(parent)
        self.bluetooth = bluetooth_manager
        self.title("Bluetooth Pairing")
        self.geometry("400x300")
        
        self.devices_list = tk.Listbox(self, width=50)
        self.devices_list.pack(pady=10, fill=tk.BOTH, expand=True)
        
        ttk.Button(
            self,
            text="Refresh Devices",
            command=self.refresh_devices
        ).pack(pady=5)
        
        ttk.Button(
            self,
            text="Pair Selected",
            command=self.pair_selected
        ).pack(pady=5)
        
        self.refresh_devices()
    
    def refresh_devices(self):
        self.devices_list.delete(0, tk.END)
        devices = self.bluetooth.discover_devices()
        for device in devices:
            self.devices_list.insert(tk.END, f"{device['name']} ({device['address']})")
    
    def pair_selected(self):
        selection = self.devices_list.curselection()
        if selection:
            device_str = self.devices_list.get(selection[0])
            address = device_str.split('(')[-1].rstrip(')')
            if self.bluetooth.pair_device(address):
                messagebox.showinfo("Success", "Pairing successful!")
                self.destroy()

class ControlPanel(tk.Tk):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.title("Control Panel")
        self.geometry("300x250")
        
        self.whatsapp_var = tk.BooleanVar(value=self.manager.whatsapp_enabled)
        self.sms_var = tk.BooleanVar(value=self.manager.sms_enabled)
        
        self.create_widgets()
    
    def create_widgets(self):
        ttk.Label(self, text="Service Control").pack(pady=10)
        
        ttk.Checkbutton(
            self,
            text="WhatsApp Service",
            variable=self.whatsapp_var,
            command=lambda: self.manager.toggle_service('whatsapp', self.whatsapp_var.get())
        ).pack(pady=5)
        
        ttk.Checkbutton(
            self,
            text="SMS Service",
            variable=self.sms_var,
            command=lambda: self.manager.toggle_service('sms', self.sms_var.get())
        ).pack(pady=5)
        
        ttk.Button(
            self,
            text="Bluetooth Pairing",
            command=self.show_bluetooth_dialog
        ).pack(pady=10)
        
        ttk.Button(
            self,
            text="Exit",
            command=self.destroy
        ).pack(pady=5)
    
    def show_bluetooth_dialog(self):
        BluetoothPairingDialog(self, self.manager.bluetooth)

# ---------------------- التشغيل الرئيسي ----------------------
if __name__ == "__main__":
    service = ServiceManager()
    service.start_monitoring()
    
    app = ControlPanel(service)
    app.mainloop()
    
    service.stop_monitoring()
