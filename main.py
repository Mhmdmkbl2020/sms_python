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
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog
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
        
    async def discover_devices(self):
        try:
            devices = await discover()
            return [{'name': d.name, 'address': d.address} for d in devices]
        except Exception as e:
            logging.error(f"Bluetooth discovery error: {str(e)}")
            return []
            
    async def connect_device(self, address):
        try:
            async with BleakClient(address) as client:
                if await client.is_connected():
                    self.config['bluetooth_device'] = address
                    ConfigManager.save_config(self.config)
                    return True
        except Exception as e:
            logging.error(f"Connection failed: {str(e)}")
            return False
            
    async def send_file(self, file_path):
        if not self.config['bluetooth_device']:
            return False
        try:
            async with BleakClient(self.config['bluetooth_device']) as client:
                if await client.is_connected():
                    with open(file_path, 'rb') as f:
                        data = f.read()
                        await client.write_gatt_char("0000ffe1-0000-1000-8000-00805f9b34fb", data)
                    return True
        except Exception as e:
            logging.error(f"Bluetooth send failed: {str(e)}")
            return False

# ---------------------- الخدمة الرئيسية ----------------------
class ServiceManager:
    def __init__(self, headless=False):
        self.config = ConfigManager.load_config()
        self.bluetooth = BluetoothManager()
        self.observer = Observer()
        self.driver = None
        self.headless = headless
        self.init_browser()

    def init_browser(self):
        if self.config['whatsapp']:
            try:
                options = Options()
                profile_path = os.path.join(BASE_DIR, 'firefox_profile')
                options.add_argument(f"--user-data-dir={profile_path}")
                
                if self.headless:
                    options.add_argument("--headless")
                    
                service = Service(GeckoDriverManager().install())
                self.driver = webdriver.Firefox(service=service, options=options)
                self.driver.get("https://web.whatsapp.com")
                
                # انتظار حتى 60 ثانية لتحميل الصفحة
                WebDriverWait(self.driver, 60).until(
                    EC.presence_of_element_located((By.XPATH, '//div[@role="textbox"]'))
                )
                
            except Exception as e:
                logging.error(f"Browser init failed: {str(e)}")

    def check_whatsapp_login(self):
        """تحقق من حالة اتصال الواتساب"""
        try:
            self.driver.find_element(By.XPATH, '//div[@role="textbox"]')
            return True
        except:
            return False

    def send_whatsapp(self, path, number, message):
        if not self.driver:
            return
            
        try:
            # إعادة التهيئة إذا كانت الجلسة منتهية
            if not self.check_whatsapp_login():
                self.driver.quit()
                self.init_browser()
                
            # إرسال الرسالة
            search_box = self.driver.find_element(By.XPATH, '//div[@role="textbox"]')
            search_box.send_keys(number + Keys.ENTER)
            time.sleep(2)
            
            message_box = self.driver.find_element(By.XPATH, '//div[@role="textbox"][@contenteditable="true"]')
            message_box.send_keys(message + Keys.ENTER)
            
            # إرفاق الملف
            self.driver.find_element(By.XPATH, '//div[@title="إرفاق"]').click()
            file_input = self.driver.find_element(By.XPATH, '//input[@type="file"]')
            file_input.send_keys(os.path.abspath(path))
            time.sleep(2)
            self.driver.find_element(By.XPATH, '//div[@aria-label="إرسال"]').click()
            
        except Exception as e:
            logging.error(f"WhatsApp sending failed: {str(e)}")
            self.init_browser()

    def start_monitoring(self):
        event_handler = PDFHandler(self)
        self.observer.schedule(event_handler, MONITOR_DIR, recursive=False)
        self.observer.start()
        logging.info("Monitoring started")

    def stop_monitoring(self):
        self.observer.stop()
        self.observer.join()
        if self.driver:
            self.driver.quit()
        logging.info("Monitoring stopped")

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
            
            # إرسال SMS
            if self.manager.config['sms']:
                self.send_sms(full_number, message)
                
            # إرسال WhatsApp
            if self.manager.config['whatsapp'] and self.manager.driver:
                self.send_whatsapp(path, full_number, message)
                
            # إرسال Bluetooth
            if self.manager.config['bluetooth_device']:
                asyncio.run(self.manager.bluetooth.send_file(path))
                
            os.remove(path)
            logging.info(f"Processed file: {os.path.basename(path)}")
            
        except Exception as e:
            logging.error(f"Processing error: {str(e)}")
            os.rename(path, f"{path}.error")

    def send_sms(self, number, message):
        try:
            with serial.Serial(self.manager.config['com_port'], 9600, timeout=1) as modem:
                modem.write(b'AT+CMGF=1\r')
                time.sleep(1)
                modem.write(f'AT+CMGS="{number}"\r'.encode())
                time.sleep(1)
                modem.write(message.encode() + b'\x1A')
        except Exception as e:
            logging.error(f"SMS sending failed: {str(e)}")

    def send_whatsapp(self, path, number, message):
        self.manager.send_whatsapp(path, number, message)

# ---------------------- واجهة التحكم ----------------------
class ControlPanel(tk.Tk):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.title("PDF Service Controller")
        self.geometry("500x400")
        self.protocol("WM_DELETE_WINDOW", self.shutdown)
        self.create_widgets()
        self.update_status()

    def create_widgets(self):
        # إطار الحالة
        status_frame = ttk.LabelFrame(self, text="حالة النظام")
        status_frame.pack(pady=10, fill='x', padx=10)
        
        # حالة المودم
        ttk.Label(status_frame, text="مودم GSM:").grid(row=0, column=0, padx=5)
        self.gsm_status = ttk.Label(status_frame, text="غير متصل", foreground="red")
        self.gsm_status.grid(row=0, column=1)
        
        # حالة البلوتوث
        ttk.Label(status_frame, text="بلوتوث:").grid(row=1, column=0, padx=5)
        self.bt_status = ttk.Label(status_frame, text="غير متصل", foreground="red")
        self.bt_status.grid(row=1, column=1)
        
        # تغيير المنفذ
        ttk.Button(
            status_frame,
            text="تغيير المنفذ",
            command=self.change_com_port
        ).grid(row=0, column=2, padx=10)
        
        # إعدادات الخدمات
        service_frame = ttk.LabelFrame(self, text="الخدمات المفعّلة")
        service_frame.pack(pady=10, fill='x', padx=10)
        
        self.whatsapp_var = tk.BooleanVar(value=self.manager.config['whatsapp'])
        ttk.Checkbutton(
            service_frame,
            text="خدمة الواتساب",
            variable=self.whatsapp_var,
            command=lambda: self.toggle_service('whatsapp')
        ).pack(pady=5, anchor='w')
        
        self.sms_var = tk.BooleanVar(value=self.manager.config['sms'])
        ttk.Checkbutton(
            service_frame,
            text="خدمة الرسائل النصية",
            variable=self.sms_var,
            command=lambda: self.toggle_service('sms')
        ).pack(pady=5, anchor='w')
        
        # إدارة البلوتوث
        ttk.Button(
            self,
            text="إدارة أجهزة البلوتوث",
            command=self.manage_bluetooth
        ).pack(pady=10)
        
        # زر الإغلاق النهائي
        ttk.Button(
            self,
            text="إيقاف الخدمة",
            command=self.shutdown
        ).pack(pady=10)

    def toggle_service(self, service_name):
        new_state = getattr(self, f"{service_name}_var").get()
        self.manager.config[service_name] = new_state
        ConfigManager.save_config(self.manager.config)
        
        if service_name == 'whatsapp' and new_state:
            self.manager.init_browser()

    def change_com_port(self):
        new_port = simpledialog.askstring("تغيير المنفذ", "أدخل المنفذ الجديد (مثال: COM4):")
        if new_port:
            self.manager.config['com_port'] = new_port
            ConfigManager.save_config(self.manager.config)

    async def show_bluetooth_devices(self):
        devices = await self.manager.bluetooth.discover_devices()
        if devices:
            device_list = "\n".join([f"{d['name']} ({d['address']})" for d in devices])
            selected_address = simpledialog.askstring(
                "إدارة البلوتوث",
                f"الأجهزة المتاحة:\n{device_list}\nأدخل عنوان الجهاز:"
            )
            if selected_address:
                if await self.manager.bluetooth.connect_device(selected_address):
                    messagebox.showinfo("نجاح", "تم الإقران بنجاح!")
                else:
                    messagebox.showerror("خطأ", "فشل عملية الإقران")

    def manage_bluetooth(self):
        Thread(target=lambda: asyncio.run(self.show_bluetooth_devices())).start()

    def update_status(self):
        # تحديث حالة المودم
        try:
            with serial.Serial(self.manager.config['com_port'], timeout=1):
                self.gsm_status.config(text="متصل", foreground="green")
        except:
            self.gsm_status.config(text="غير متصل", foreground="red")
            
        # تحديث حالة البلوتوث
        if self.manager.config['bluetooth_device']:
            self.bt_status.config(text="متصل", foreground="green")
        else:
            self.bt_status.config(text="غير متصل", foreground="red")
            
        self.after(1000, self.update_status)

    def shutdown(self):
        self.manager.stop_monitoring()
        self.destroy()

# ---------------------- التشغيل الرئيسي ----------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--headless', action='store_true', help='التشغيل في الخلفية')
    args = parser.parse_args()

    service = ServiceManager(headless=args.headless)
    service.start_monitoring()

    if not args.headless:
        app = ControlPanel(service)
        app.mainloop()
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            service.stop_monitoring()
