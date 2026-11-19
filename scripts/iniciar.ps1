param([switch]$SoloConfigurar)
# Bot de admisiones por WhatsApp - arranque local en Windows
# Instala lo necesario la primera vez, pide las claves, y deja corriendo el servidor + el túnel público.

$ErrorActionPreference = 'Stop'
$Raiz = Split-Path -Parent $PSScriptRoot
Set-Location $Raiz
$EnvFile = Join-Path $Raiz '.env'
$Herramientas = Join-Path $Raiz 'herramientas'
$Puerto = 8000

function Titulo($t) { Write-Host ''; Write-Host "=== $t ===" -ForegroundColor Magenta }
function Ok($t) { Write-Host "  [OK] $t" -ForegroundColor Green }
function Aviso($t) { Write-Host "  [!] $t" -ForegroundColor Yellow }
function Fallo($t) { Write-Host ''; Write-Host "  [X] $t" -ForegroundColor Red; Write-Host ''; exit 1 }

# ---------------------------------------------------------------- .env
function LeerEnv {
    $h = @{}
    if (Test-Path $EnvFile) {
        foreach ($l in [IO.File]::ReadAllLines($EnvFile)) {
            if ($l -match '^\s*([A-Z0-9_]+)\s*=\s*(.*)$') { $h[$matches[1]] = $matches[2].Trim() }
        }
    }
    return $h
}
function GuardarEnv($h) {
    $lineas = $h.Keys | Sort-Object | ForEach-Object { "$_=$($h[$_])" }
    [IO.File]::WriteAllLines($EnvFile, [string[]]$lineas, (New-Object Text.UTF8Encoding $false))
}
function Aleatorio([int]$bytes) {
    $b = New-Object byte[] $bytes
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($b)
    return (($b | ForEach-Object { $_.ToString('x2') }) -join '')
}
function LeerSecreto($prompt) {
    $s = Read-Host $prompt -AsSecureString
    $p = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($p) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($p) }
}
function Pedir($h, $clave, $explicacion, [switch]$Secreto, [switch]$Opcional) {
    $actual = $h[$clave]
    $tiene = -not [string]::IsNullOrWhiteSpace($actual)
    if ($tiene -and $Secreto) { $extra = ' (ya guardada: Enter para dejarla igual)' }
    elseif ($tiene) { $extra = " (actual: $actual - Enter para dejarlo)" }
    elseif ($Opcional) { $extra = ' (opcional: Enter para omitir)' }
    else { $extra = '' }
    Write-Host ''
    Write-Host "  $explicacion" -ForegroundColor Gray
    if ($Secreto) { $v = LeerSecreto "  $clave$extra" } else { $v = Read-Host "  $clave$extra" }
    $v = "$v".Trim()
    if ($v) { $h[$clave] = $v }
}

function Configurar {
    Titulo 'Configuracion de claves'
    Write-Host '  Pega cada valor y presiona Enter. Las claves secretas no se ven mientras las pegas (es normal).'
    Write-Host '  Para pegar en esta ventana: clic derecho.' -ForegroundColor Gray
    $h = LeerEnv
    Pedir $h 'GEMINI_API_KEY' 'API key de Gemini (aistudio.google.com > Claves de API > copiar).' -Secreto
    Pedir $h 'NGROK_AUTHTOKEN' 'Authtoken de ngrok (dashboard.ngrok.com > Your Authtoken > copiar).' -Secreto
    Pedir $h 'NGROK_DOMAIN' 'Dominio fijo de ngrok, ej: algo-algo.ngrok-free.dev (dashboard.ngrok.com > Domains).'
    Pedir $h 'WHATSAPP_TOKEN' 'Token permanente de WhatsApp (Meta > Usuarios del sistema > Generar token).' -Secreto -Opcional
    Pedir $h 'WHATSAPP_APP_SECRET' 'Clave secreta de la app de Meta (Configuracion de la app > Basica).' -Secreto -Opcional
    Pedir $h 'WHATSAPP_PHONE_NUMBER_ID' 'Identificador de numero de telefono principal (Meta > WhatsApp > Configuracion de la API).' -Opcional
    if (-not $h['NGROK_DOMAIN']) { $h['NGROK_DOMAIN'] = '' }
    $h['NGROK_DOMAIN'] = ($h['NGROK_DOMAIN'] -replace '^https?://', '').TrimEnd('/')
    if (-not $h['SECRET_KEY']) { $h['SECRET_KEY'] = Aleatorio 32 }
    if (-not $h['WHATSAPP_VERIFY_TOKEN']) { $h['WHATSAPP_VERIFY_TOKEN'] = 'verif-' + (Aleatorio 12) }
    if (-not $h['NOMBRE_BOT']) { $h['NOMBRE_BOT'] = 'Sofía' }
    if (-not $h['NOMBRE_INSTITUCION']) { $h['NOMBRE_INSTITUCION'] = 'UNIMINUTO Girardot' }
    if (-not $h['GEMINI_MODEL'] -or $h['GEMINI_MODEL'] -eq 'gemini-3.7-flash') { $h['GEMINI_MODEL'] = 'gemini-3.6-flash' }
    $h['MINUTOS_KEEPALIVE'] = '0'
    if (-not $h.ContainsKey('DATABASE_URL')) { $h['DATABASE_URL'] = '' }
    GuardarEnv $h
    Ok 'Claves guardadas en el archivo .env de esta carpeta (no lo compartas).'
}

# ---------------------------------------------------------------- Python
function RefrescarPath {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}
function BuscarPython {
    foreach ($c in @('py', 'python', 'python3')) {
        if (Get-Command $c -ErrorAction SilentlyContinue) {
            try {
                $v = & $c -c "import sys;print('%d.%d' % sys.version_info[:2])" 2>$null
                if ($v -and ([version]"$v" -ge [version]'3.10')) { return $c }
            } catch { }
        }
    }
    return $null
}
function PrepararPython {
    Titulo 'Python'
    $py = BuscarPython
    if (-not $py) {
        Aviso 'Python no esta instalado. Instalando Python 3.12 (unos minutos)...'
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
            Fallo 'No se encontro winget. Instala Python 3.12 desde https://www.python.org/downloads/ (marca "Add python.exe to PATH") y vuelve a abrir INICIAR_BOT.bat'
        }
        winget install -e --id Python.Python.3.12 --scope user --accept-package-agreements --accept-source-agreements
        RefrescarPath
        $py = BuscarPython
        if (-not $py) { Fallo 'Python se instalo pero Windows aun no lo ve. Cierra esta ventana y abre INICIAR_BOT.bat otra vez.' }
    }
    Ok "Python encontrado ($py)"

    $venvPy = Join-Path $Raiz '.venv\Scripts\python.exe'
    if (-not (Test-Path $venvPy)) {
        Write-Host '  Creando entorno del bot...'
        & $py -m venv (Join-Path $Raiz '.venv')
    }
    $req = Join-Path $Raiz 'requirements.txt'
    $hashActual = (Get-FileHash $req -Algorithm SHA256).Hash
    $hashFile = Join-Path $Raiz '.venv\req.hash'
    $hashGuardado = ''
    if (Test-Path $hashFile) { $hashGuardado = (Get-Content $hashFile -Raw).Trim() }
    if ($hashActual -ne $hashGuardado) {
        Write-Host '  Instalando dependencias (solo la primera vez o si cambian)...'
        & $venvPy -m pip install --upgrade pip --quiet --disable-pip-version-check
        & $venvPy -m pip install -r $req --quiet --disable-pip-version-check
        if ($LASTEXITCODE -ne 0) { Fallo 'No se pudieron instalar las dependencias. Revisa la conexion a internet.' }
        Set-Content $hashFile $hashActual
    }
    Ok 'Dependencias listas'
    return $venvPy
}

# ---------------------------------------------------------------- ngrok
function ProbarNgrok($exe) {
    try { & $exe version 2>$null | Out-Null; return ($LASTEXITCODE -eq 0) } catch { return $false }
}
function PrepararNgrok($cfg) {
    Titulo 'Tunel publico (ngrok)'
    $exe = Join-Path $Herramientas 'ngrok.exe'
    if (-not (Test-Path $exe)) {
        $global = Get-Command ngrok -ErrorAction SilentlyContinue
        if ($global) { $exe = $global.Source }
        else {
            Write-Host '  Descargando ngrok...'
            New-Item -ItemType Directory -Force $Herramientas | Out-Null
            $zip = Join-Path $Herramientas 'ngrok.zip'
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest 'https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-windows-amd64.zip' -OutFile $zip -UseBasicParsing
                Expand-Archive $zip -DestinationPath $Herramientas -Force
                Remove-Item $zip -Force
            } catch {
                Aviso 'La descarga directa fallo; intentando con winget...'
                winget install -e --id ngrok.ngrok --accept-package-agreements --accept-source-agreements
                RefrescarPath
                $global = Get-Command ngrok -ErrorAction SilentlyContinue
                if (-not $global) { Fallo 'No se pudo instalar ngrok. Descargalo de https://ngrok.com/download y pon ngrok.exe en la carpeta "herramientas".' }
                $exe = $global.Source
            }
        }
    }
    if (-not (ProbarNgrok $exe)) {
        # Windows o el antivirus no dejan ejecutar ngrok.exe descargado en esta carpeta: probar la version instalada con winget
        Aviso 'Windows no dejo ejecutar ngrok desde esta carpeta (Acceso denegado). Probando instalarlo con winget...'
        winget install -e --id ngrok.ngrok --accept-package-agreements --accept-source-agreements | Out-Null
        RefrescarPath
        $global = Get-Command ngrok -ErrorAction SilentlyContinue
        if ($global -and (ProbarNgrok $global.Source)) { $exe = $global.Source }
        else {
            Write-Host ''
            Write-Host '  [X] El antivirus o una politica de Windows esta bloqueando ngrok.exe (Acceso denegado).' -ForegroundColor Red
            Write-Host '      Que hacer:' -ForegroundColor Yellow
            Write-Host '      1. Abre Seguridad de Windows > Proteccion antivirus y contra amenazas > Historial de proteccion.'
            Write-Host '         Si aparece ngrok bloqueado o en cuarentena: Acciones > Permitir en el dispositivo.'
            Write-Host '      2. En Seguridad de Windows agrega una exclusion para esta carpeta:'
            Write-Host "         $Raiz"
            Write-Host '      3. Si el PC es de la universidad y el antivirus lo administra TI, pideles permitir:'
            Write-Host "         $exe  y los dominios *.ngrok.com, *.ngrok-free.dev"
            Write-Host '      4. Mueve la carpeta del bot fuera de Descargas, por ejemplo a C:\BotAdmisiones, y vuelve a abrir INICIAR_BOT.bat.'
            Write-Host ''
            exit 1
        }
    }
    & $exe config add-authtoken $cfg['NGROK_AUTHTOKEN'] 2>$null | Out-Null
    if ($LASTEXITCODE -ne 0) { Fallo 'El authtoken de ngrok no es valido. Ejecuta CONFIGURAR_CLAVES.bat y pegalo de nuevo.' }
    Ok 'ngrok listo'
    return $exe
}

function TunelActivo {
    try {
        $r = Invoke-RestMethod 'http://127.0.0.1:4040/api/tunnels' -TimeoutSec 3
        return (@($r.tunnels).Count -gt 0)
    } catch { return $false }
}
function IniciarTunel($exe, $url) {
    Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
    Start-Process $exe -ArgumentList @('http', "--url=$url", "$Puerto", '--log=stdout') -WindowStyle Hidden | Out-Null
}

# ---------------------------------------------------------------- evitar que el PC se suspenda
Add-Type -Namespace Win32 -Name Energia -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
function MantenerDespierto([bool]$si) {
    if ($si) { [Win32.Energia]::SetThreadExecutionState([uint32]2147483649) | Out-Null }   # CONTINUOUS | SYSTEM_REQUIRED
    else { [Win32.Energia]::SetThreadExecutionState([uint32]2147483648) | Out-Null }
}

# ================================================================ inicio
Write-Host ''
Write-Host '  BOT DE ADMISIONES - WhatsApp + IA + panel de asesores' -ForegroundColor Magenta

if ($SoloConfigurar) { Configurar; exit 0 }

$cfg = LeerEnv
if (-not $cfg['GEMINI_API_KEY'] -or -not $cfg['NGROK_AUTHTOKEN'] -or -not $cfg['NGROK_DOMAIN']) {
    Aviso 'Faltan claves. Vamos a configurarlas (solo la primera vez).'
    Configurar
    $cfg = LeerEnv
}
if (-not $cfg['WHATSAPP_TOKEN']) { Aviso 'Aun no hay token de WhatsApp: el panel y "Probar bot" funcionan, pero no llegaran mensajes reales.' }

$venvPy = PrepararPython
$ngrokExe = PrepararNgrok $cfg
$dominio = $cfg['NGROK_DOMAIN']
$url = "https://$dominio"

if (Get-NetTCPConnection -LocalPort $Puerto -State Listen -ErrorAction SilentlyContinue) {
    Fallo "El puerto $Puerto ya esta en uso. Probablemente el bot ya esta abierto en otra ventana."
}

Titulo 'Arrancando'
MantenerDespierto $true
$env:PYTHONIOENCODING = 'utf-8'
$servidor = $null
try {
    IniciarTunel $ngrokExe $url
    $servidor = Start-Process $venvPy -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Puerto",
        '--proxy-headers', '--forwarded-allow-ips=*', '--no-access-log') -NoNewWindow -PassThru
    Start-Sleep -Seconds 5

    Write-Host ''
    Write-Host '  ================================================================' -ForegroundColor Green
    Write-Host '   BOT ENCENDIDO - deja esta ventana abierta y el PC con internet' -ForegroundColor Green
    Write-Host '  ================================================================' -ForegroundColor Green
    Write-Host ''
    Write-Host "   Panel para asesores (celular o PC):  $url" -ForegroundColor White
    Write-Host "   Crear cuenta de asesor:              $url/registro"
    Write-Host ''
    Write-Host "   Para Meta (solo se configura una vez):"
    Write-Host "     URL de devolucion de llamada:  $url/webhook"
    Write-Host "     Token de verificacion:         $($cfg['WHATSAPP_VERIFY_TOKEN'])"
    Write-Host ''
    Write-Host '   La primera vez que alguien abra el panel, ngrok muestra un aviso: tocar "Visit Site".' -ForegroundColor Gray
    Write-Host '   Para apagar el bot: cierra esta ventana o presiona Ctrl+C.' -ForegroundColor Gray
    Write-Host ''
    Start-Process $url

    $caidas = 0
    $inicioCaidas = Get-Date
    $fallosTunel = 0
    while ($true) {
        Start-Sleep -Seconds 5
        if ($servidor.HasExited) {
            $caidas += 1
            if ($caidas -ge 3 -and ((Get-Date) - $inicioCaidas).TotalSeconds -lt 60) {
                Fallo 'El servidor falla al arrancar (3 veces seguidas). Toma captura del error de arriba y enviasela a quien te ayuda con el bot.'
            }
            if ($caidas -eq 1 -or ((Get-Date) - $inicioCaidas).TotalSeconds -ge 60) { $caidas = 1; $inicioCaidas = Get-Date }
            Aviso 'El servidor se detuvo; reiniciando...'
            $servidor = Start-Process $venvPy -ArgumentList @('-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', "$Puerto",
                '--proxy-headers', '--forwarded-allow-ips=*', '--no-access-log') -NoNewWindow -PassThru
        }
        if (-not (TunelActivo)) {
            $fallosTunel += 1
            if ($fallosTunel -ge 3) {
                Aviso 'El tunel no responde (puede ser el internet); reconectando...'
                IniciarTunel $ngrokExe $url
                $fallosTunel = 0
                Start-Sleep -Seconds 10
            }
        } else { $fallosTunel = 0 }
    }
}
finally {
    Get-Process ngrok -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
    if ($servidor -and -not $servidor.HasExited) { Stop-Process -Id $servidor.Id -Force -ErrorAction SilentlyContinue }
    MantenerDespierto $false
    Write-Host ''
    Write-Host '  Bot apagado. Los mensajes que lleguen mientras tanto se recibiran al encenderlo (hasta 7 dias).' -ForegroundColor Yellow
}
