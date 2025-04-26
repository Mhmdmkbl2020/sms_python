@echo off
set PYTHONOPTIMIZE=1
pyinstaller --noconfirm --onefile --windowed --name PDFService ^
  --add-data "pdf_files;pdf_files" ^
  --hidden-import selenium.webdriver.firefox.service ^
  main.py
