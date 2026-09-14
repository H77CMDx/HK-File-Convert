call "C:\Users\Hiros\Documents\Coding stuff\VSCode\File Convert\.venv\Scripts\activate.bat"
pyinstaller --onedir --add-data "icon.ico;." --icon "icon.ico" --noconsole "main.py"
pause