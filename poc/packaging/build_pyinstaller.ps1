param(
    [ValidateSet("onedir", "onefile")]
    [string]$Mode = "onedir"
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$name = "crm_poc_pyi_$Mode"
$python = ".\.venv\Scripts\python.exe"
& $python -m PyInstaller --noconfirm --clean "--$Mode" --name $name `
    --distpath "dist\pyinstaller_$Mode" --workpath "build\pyinstaller_$Mode" `
    --specpath "build\pyinstaller_$Mode" `
    --paths src `
    --collect-all streamlit `
    --collect-all jpholiday `
    --add-data "$PWD\src\crm_poc\exe_ui;crm_poc\exe_ui" `
    --add-data "$PWD\src\crm_poc\resources;crm_poc\resources" `
    src\crm_poc\launcher.py
exit $LASTEXITCODE
