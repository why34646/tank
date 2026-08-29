# installers 目录说明

本目录后期存放：
1. `TankTrouble.spec`       — PyInstaller 打包脚本（`--onedir` 模式，入口 `../main.py`）
2. `TankTrouble.iss`        — Inno Setup 安装/卸载脚本（含 AppId、AppVersion、桌面快捷方式、注册表卸载项）
3. `build.bat`              — 一键打包：PyInstaller → Inno Setup 编译

## 架构阶段占位命令（后续填写）

### PyInstaller
```
pyinstaller --noconfirm --clean --onedir ^
  --name TankTrouble ^
  --add-data "app/assets;app/assets" ^
  ..\main.py
```

### Inno Setup 编译
```
"C:\Program Files (x86)\Inno Setup 6\ISCC.exe" TankTrouble.iss
```
