@echo off
cd /d "%~dp0"
echo PopGun - Gunluk Pop Calma Listesi baslatiliyor...
echo Tarayicinizda http://127.0.0.1:5001 adresi acilacak.
start "" http://127.0.0.1:5001
python server.py
pause