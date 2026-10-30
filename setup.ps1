<#
.SYNOPSIS
    Установка голосового запуска World of Tanks: зависимости, проверка модели,
    ярлык в автозагрузке Windows.

.EXAMPLE
    .\setup.ps1              # установить и добавить в автозагрузку
    .\setup.ps1 -Start       # установить и сразу запустить
    .\setup.ps1 -Remove      # убрать из автозагрузки
#>
param(
    [switch]$Remove,
    [switch]$Start,
    [switch]$NoPip
)

$ErrorActionPreference = 'Stop'
$app = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath('Startup')
$linkPath = Join-Path $startup 'WoT голосовой запуск.lnk'

function Find-Python {
    $candidates = @(
        (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe')
    ) | Where-Object { $_ -and (Test-Path $_) }

    if (-not $candidates) { throw 'Не нашёл Python. Установи его с python.org и повтори.' }
    return $candidates[0]
}

if ($Remove) {
    if (Test-Path $linkPath) {
        Remove-Item $linkPath -Force
        Write-Host "Ярлык удалён: $linkPath"
    } else {
        Write-Host 'Ярлыка в автозагрузке нет — и так чисто.'
    }
    exit 0
}

$python = Find-Python
$pythonw = Join-Path (Split-Path -Parent $python) 'pythonw.exe'
Write-Host "Python: $python"

if (-not $NoPip) {
    Write-Host 'Ставлю зависимости…'
    & $python -m pip install --quiet --disable-pip-version-check -r (Join-Path $app 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'pip не смог поставить зависимости' }
}

$modelDir = Join-Path $app 'model\vosk-model-small-ru-0.22'
$zip = Join-Path $app 'model.zip'

function Get-ModelArchive {
    param([string]$AppDir, [string]$ZipPath)

    $urls = @(
        'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip',
        'https://raw.githubusercontent.com/kercre123/vosk-models/main/vosk-model-small-ru-0.22.zip'
    )

    foreach ($url in $urls) {
        Write-Host "Качаю модель: $url"
        try {
            if (Get-Command curl.exe -ErrorAction SilentlyContinue) {
                # -4: у некоторых провайдеров IPv6-маршрут до зеркала битый
                & curl.exe -4 -L --fail --retry 3 --retry-delay 2 -o $ZipPath $url
            } else {
                Invoke-WebRequest -Uri $url -OutFile $ZipPath -UseBasicParsing
            }
        } catch {
            Write-Warning "Не вышло: $($_.Exception.Message)"
            continue
        }
        if ((Test-Path $ZipPath) -and ((Get-Item $ZipPath).Length -gt 30MB)) {
            return $ZipPath
        }
    }
    throw 'Не удалось скачать модель. Скачай vosk-model-small-ru-0.22.zip вручную и положи рядом с app.py.'
}

if (-not (Test-Path $modelDir)) {
    if (-not (Test-Path $zip)) {
        Get-ModelArchive -AppDir $app -ZipPath $zip | Out-Null
    }
    Write-Host 'Распаковываю модель…'
    New-Item -ItemType Directory -Force -Path (Join-Path $app 'model') | Out-Null
    Expand-Archive -Path $zip -DestinationPath (Join-Path $app 'model') -Force
    Remove-Item $zip -Force -ErrorAction SilentlyContinue
}

# Запуск через pythonw — без чёрного окна консоли.
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($linkPath)
$sc.TargetPath = $pythonw
$sc.Arguments = '"' + (Join-Path $app 'app.py') + '"'
$sc.WorkingDirectory = $app
$sc.WindowStyle = 7
$sc.Description = 'World of Tanks по кодовой фразе'
$sc.Save()
Write-Host "Автозагрузка: $linkPath"

if ($Start) {
    Start-Process -FilePath $pythonw -ArgumentList ('"' + (Join-Path $app 'app.py') + '"') -WorkingDirectory $app
    Write-Host 'Запущено. Ищи танк в системном трее.'
}

Write-Host ''
Write-Host 'Готово. Проверка фразы без микрофона:'
Write-Host "  & `"$python`" `"$app\app.py`" --simulate `"юхан`""
