@echo off
echo Building 3MF to GLB Converter...
python -m PyInstaller --onefile --name "3mf2glb" converter.py
echo.
echo Build complete! Executable is in the dist\ folder.
pause
