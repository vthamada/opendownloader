[CmdletBinding()]
param([string]$Python = 'python')
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Push-Location $project
try {
    if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
        & $Python -m venv '.venv'
        if ($LASTEXITCODE -ne 0) { throw 'Falha ao criar ambiente isolado.' }
    }
    & '.venv\Scripts\python.exe' -m pip install --disable-pip-version-check -r requirements-lock.txt
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao preparar dependências.' }
    & '.venv\Scripts\python.exe' -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) { throw 'Testes não passaram; pacote não criado.' }
    $taskStaged = [IO.Path]::GetFullPath((Join-Path $project '.cache\staged-dist\LumeDownloads'))
    $taskInstalled = [IO.Path]::GetFullPath((Join-Path $project 'dist\LumeDownloads'))
    & '.venv\Scripts\python.exe' -m PyInstaller --noconfirm --distpath '.cache\staged-dist' LumeDownloads.spec
    if ($LASTEXITCODE -ne 0) { throw 'Falha no empacotamento.' }
    & '.venv\Scripts\python.exe' tools\verify_package.py --bundle $taskStaged
    if ($LASTEXITCODE -ne 0) { throw 'A validação dos executáveis falhou.' }
    # Never let PyInstaller clean the installed bundle while its GUI/host is live.
    $taskRunning = @(Get-CimInstance Win32_Process -Filter "Name = 'LumeDownloads.exe' OR Name = 'LumeNativeHost.exe'" |
        Where-Object { $_.ExecutablePath -like ($taskInstalled + '\*') })
    if ($taskRunning.Count -gt 0) { throw "Pacote validado em $taskStaged. Feche o Lume e execute novamente para aplicar; instalação preservada." }
    $taskBackup = [IO.Path]::GetFullPath((Join-Path $project ('.cache\build-backups\LumeDownloads-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))))
    foreach ($taskPath in @($taskStaged,$taskInstalled,$taskBackup)) {
        if (-not $taskPath.StartsWith($project + '\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Destino fora do projeto.' }
    }
    New-Item -ItemType Directory -Path (Split-Path -Parent $taskBackup) -Force | Out-Null
    if (Test-Path -LiteralPath $taskInstalled) { Move-Item -LiteralPath $taskInstalled -Destination $taskBackup }
    Move-Item -LiteralPath $taskStaged -Destination $taskInstalled
    & '.venv\Scripts\python.exe' tools\package_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Falha na criação da distribuição portátil.' }
    Write-Host 'Pronto: dist\LumeDownloads\LumeDownloads.exe'
} finally { Pop-Location }
