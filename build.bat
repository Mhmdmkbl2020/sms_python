@echo off
set PYTHONOPTIMIZE=1
pyinstaller --noconfirm --onefile --windowed --name PDFService ^
  --add-data "pdf_files;pdf_files" ^
  --add-binary "C:\Windows\System32\btpair.exe;." ^
  --add-binary "C:\Windows\System32\btcom.exe;." ^
  --hidden-import selenium.webdriver.firefox.service ^
  main.py
