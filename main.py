import os
import sys
import time
import json
import logging
import serial
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
from tkinter import ttk

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
            return {'whatsapp': True, 'sms': True}

    @staticmethod
    def save_config(config):
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f)

# ---------------------- الخدمة الرئيسية ----------------------
class ServiceManager:
    def __init__(self):
        self.config = ConfigManager.load_config()
        self.whatsapp_enabled = self.config['whatsapp']
        self.sms_enabled = self.config['sms']
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
                time.sleep(15)  # وقت لمسح QR code
            except Exception as e:
                logging.error(f"تعذر تهيئة المتصفح: {str(e)}")
    
    def start_monitoring(self):
        self.observer.schedule(PDFHandler(self), MONITOR_DIR, recursive=False)
        self.observer.start()
        logging.info("بدأت مراقبة المجلد")
    
    def stop_monitoring(self):
        self.observer.stop()
        self.observer.join()
        if self.driver:
            self.driver.quit()
        logging.info("توقفت المراقبة")
    
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
                text = '\n'.join(page.extract_text() or '' for page in reader.pages)
            
            # استخراج الرقم والرسالة
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            if len(lines) < 2:
                raise ValueError("الملف لا يحتوي على بيانات كافية")
            
            number = lines[0].lstrip('+').replace(' ', '')  # إزالة الرمز والمسافات
            message = lines[1]
            
            # التحقق من صحة الرقم
            if not (number.isdigit() and len(number) == 9):
                raise ValueError("رقم غير صحيح")
            
            full_number = f"966{number}"  # إضافة رمز الدولة
            
            # الإرسال حسب الإعدادات
            if self.manager.sms_enabled:
                self.send_sms(full_number, message)
            
            if self.manager.whatsapp_enabled and self.manager.driver:
                self.send_whatsapp(path, full_number, message)
            
            os.remove(path)
            logging.info(f"تم معالجة الملف: {os.path.basename(path)}")
            
        except Exception as e:
            logging.error(f"خطأ في المعالجة: {str(e)}")
            os.rename(path, f"{path}.error")

    def send_sms(self, number, message):
        try:
            with serial.Serial('COM3', 9600, timeout=1) as modem:
                modem.write(b'AT+CMGF=1\r')
                modem.write(f'AT+CMGS="{number}"\r'.encode())
                modem.write(message.encode() + b'\x1A')
                logging.info(f"تم إرسال SMS إلى {number}")
        except Exception as e:
            logging.error(f"فشل إرسال SMS: {str(e)}")

    def send_whatsapp(self, path, number, message):
        try:
            self.manager.driver.find_element(By.XPATH, '//div[@role="textbox"]').send_keys(number + Keys.ENTER)
            time.sleep(2)
            
            # إرسال الرسالة النصية
            self.manager.driver.find_element(By.XPATH, '//div[@role="textbox"]').send_keys(message + Keys.ENTER)
            
            # إرسال الملف
            self.manager.driver.find_element(By.XPATH, '//div[@title="إرفاق"]').click()
            file_input = self.manager.driver.find_element(By.XPATH, '//input[@type="file"]')
            file_input.send_keys(os.path.abspath(path))
            time.sleep(2)
            self.manager.driver.find_element(By.XPATH, '//div[@aria-label="إرسال"]').click()
            
            logging.info(f"تم إرسال الواتساب إلى {number}")
        except Exception as e:
            logging.error(f"فشل إرسال واتساب: {str(e)}")
            self.manager.init_browser()

# ---------------------- واجهة التحكم ----------------------
class ControlPanel(tk.Tk):
    def __init__(self, manager):
        super().__init__()
        self.manager = manager
        self.title("لوحة التحكم")
        self.geometry("300x200")
        
        self.whatsapp_var = tk.BooleanVar(value=self.manager.whatsapp_enabled)
        self.sms_var = tk.BooleanVar(value=self.manager.sms_enabled)
        
        self.create_widgets()
    
    def create_widgets(self):
        ttk.Label(self, text="اختر الخدمات المطلوبة:").pack(pady=10)
        
        ttk.Checkbutton(
            self,
            text="خدمة الواتساب",
            variable=self.whatsapp_var,
            command=lambda: self.manager.toggle_service('whatsapp', self.whatsapp_var.get())
        ).pack(pady=5)
        
        ttk.Checkbutton(
            self,
            text="خدمة الرسائل النصية",
            variable=self.sms_var,
            command=lambda: self.manager.toggle_service('sms', self.sms_var.get())
        ).pack(pady=5)
        
        ttk.Button(
            self,
            text="خروج",
            command=self.destroy
        ).pack(pady=10)

# ---------------------- التشغيل الرئيسي ----------------------
if __name__ == "__main__":
    service = ServiceManager()
    service.start_monitoring()
    
    app = ControlPanel(service)
    app.mainloop()
    
    service.stop_monitoring()
