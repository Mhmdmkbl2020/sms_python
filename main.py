import os
import sys
import time
import json
import logging
import serial
import asyncio
from threading import Thread
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
from bleak import BleakClient, discover

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
            return {
                'whatsapp': True,
                'sms': True,
                'com_port': 'COM3',
                'bluetooth_device': None
            }

    @staticmethod
    def save_config(config):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)

# ---------------------- إدارة البلوتوث ----------------------
class BluetoothManager:
    def __init__(self):
        self.config = ConfigManager.load_config()
        self.loop = asyncio.new_event_loop()
        self.connected = False
        
    async def discover_devices(self):
        """اكتشاف أجهزة البلوتوث المتاحة"""
        try:
            devices = await discover()
            return [{'name': d.name, 'address': d.address} for d in devices]
        except Exception as e:
            logging.error(f"Bluetooth error: {str(e)}")
            return []
        
    async def connect_device(self, address):
        """الاتصال بجهاز بلوتوث"""
        try:
            async with BleakClient(address) as client:
                self.connected = await client.is_connected()
                if self.connected:
                    self.config['bluetooth_device'] = address
                    ConfigManager.save_config(self.config)
                    return True
        except Exception as e:
            logging.error(f"Connection failed: {str(e)}")
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
        logging.info("Service started")
    
    def stop_monitoring(self):
        self.observer.stop()
        self.observer.join()
        if self.driver:
            self.driver.quit()
        logging.info("Service stopped")

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
            
            if self.manager.config['sms']:
                self.send_sms(full_number, message)
            
            if self.manager.config['whatsapp'] and self.manager.driver:
                self.send_whatsapp(path, full_number, message)
            
            if self.manager.bluetooth.config['bluetooth_device']:
                asyncio.run(self.send_bluetooth(path))
            
            os.remove(path)
            
        except Exception as e:
            logging.error(f"Processing error: {str(e)}")
            os.rename(path, f"{path}.error")

    def send_sms(self, number, message):
        try:
            with serial.Serial(self.manager.config['com_port'], 9600, timeout=1) as modem:
                modem.write(b'AT+CMGF=1\r')
                modem.write(f'AT+CMGS="{number}"\r'.encode() + message.encode() + b'\x1A')
        except Exception as e:
            logging.error(f"SMS failed: {str(e)}")

    async def send_bluetooth(self, file_path):
        """إرسال الملف عبر البلوتوث"""
        try:
            async with BleakClient(self.manager.bluetooth.config['bluetooth_device']) as client:
                if await client.is_connected():
                    with open(file_path, 'rb') as f:
                        data = f.read()
                        await client.write_gatt_char("0000ffe1-0000-1000-8000-00805f9b34fb", data)
        except Exception as e:
            logging.error(f"Bluetooth send failed: {str(e)}")

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
class ControlPanel(tk.Tk):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.title("PDF Service Controller")
        self.geometry("500x400")
        
        self.create_widgets()
        self.update_status()
    
    def create_widgets(self):
        # إطار حالة الأجهزة
        status_frame = ttk.LabelFrame(self, text="Device Status")
        status_frame.pack(pady=10, fill='x', padx=10)
        
        # GSM
        ttk.Label(status_frame, text="مودم GSM:").grid(row=0, column=0, padx=5)
        self.gsm_status = ttk.Label(status_frame, text="غير متصل", foreground="red")
        self.gsm_status.grid(row=0, column=1)
        
        # Bluetooth
        ttk.Label(status_frame, text="بلوتوث:").grid(row=1, column=0, padx=5)
        self.bt_status = ttk.Label(status_frame, text="غير متصل", foreground="red")
        self.bt_status.grid(row=1, column=1)
        
        # إعدادات المنفذ
        ttk.Button(
            status_frame,
            text="تغيير المنفذ",
            command=self.change_port
        ).grid(row=0, column=2, padx=10)
        
        # التحكم بالخدمات
        control_frame = ttk.LabelFrame(self, text="الخدمات")
        control_frame.pack(pady=10, fill='x', padx=10)
        
        self.whatsapp_var = tk.BooleanVar(value=self.manager.config['whatsapp'])
        ttk.Checkbutton(
            control_frame,
            text="خدمة الواتساب",
            variable=self.whatsapp_var,
            command=lambda: self.toggle_service('whatsapp')
        ).pack(pady=5, anchor='w')
        
        self.sms_var = tk.BooleanVar(value=self.manager.config['sms'])
        ttk.Checkbutton(
            control_frame,
            text="خدمة الرسائل النصية",
            variable=self.sms_var,
            command=lambda: self.toggle_service('sms')
        ).pack(pady=5, anchor='w')
        
        # Bluetooth Pairing
        ttk.Button(
            control_frame,
            text="إدارة البلوتوث",
            command=self.show_bluetooth_dialog
        ).pack(pady=10)
        
        # إغلاق
        ttk.Button(
            self,
            text="خروج",
            command=self.destroy
        ).pack(pady=10)
    
    def toggle_service(self, service_name):
        new_state = getattr(self, f"{service_name}_var").get()
        self.manager.config[service_name] = new_state
        ConfigManager.save_config(self.manager.config)
        if service_name == 'whatsapp' and new_state:
            self.manager.init_browser()
    
    def change_port(self):
        new_port = simpledialog.askstring("تغيير المنفذ", "أدخل المنفذ الجديد (مثال: COM4):")
        if new_port:
            self.manager.config['com_port'] = new_port
            ConfigManager.save_config(self.manager.config)
    
    async def show_bluetooth_dialog_async(self):
        devices = await self.manager.bluetooth.discover_devices()
        if devices:
            choice = simpledialog.askstring("اختيار جهاز", "أدخل عنوان الجهاز:")
            if choice:
                if await self.manager.bluetooth.connect_device(choice):
                    messagebox.showinfo("Success", "تم الإقران بنجاح!")
    
    def show_bluetooth_dialog(self):
        Thread(target=lambda: asyncio.run(self.show_bluetooth_dialog_async())).start()
    
