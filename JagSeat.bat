@echo off
rem Double-click to open the JagSeat window. Edit the IP below if the board's address changes.
set JAGSEAT_HOST=192.168.12.241
start "" pythonw "%~dp0jagseat.py" %*
