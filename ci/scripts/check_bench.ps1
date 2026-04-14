# check_bench.ps1  —  Vérification connectivité banc WipeWash (Windows)
# ============================================================
# Appelé par Jenkins au stage "Bench Connectivity"
# Teste : ping RPiBCM, ping RPiSIM, Redis, port TCP 5555 / 5000 / 5556
#
# Usage (PowerShell) :
#   .\ci\scripts\check_bench.ps1 -BcmHost 10.20.0.25 -SimHost 10.20.0.7
# ============================================================
param(
    [string]$BcmHost  = "10.20.0.25",
    [string]$SimHost  = "10.20.0.7",
    [int]   $RedisPort = 6379,
    [string]$Python   = ".venv\Scripts\python.exe"
)

$ErrorActionPreference = "Continue"
$allOk = $true

function Test-TCPPort {
    param([string]$Host, [int]$Port, [string]$Label)
    $tcp = New-Object System.Net.Sockets.TcpClient
    $async = $tcp.BeginConnect($Host, $Port, $null, $null)
    $wait  = $async.AsyncWaitHandle.WaitOne(2000, $false)
    if ($wait -and -not $tcp.Client.Connected -eq $false) {
        try { $tcp.EndConnect($async) } catch {}
    }
    $ok = $tcp.Connected
    $tcp.Close()
    $icon = if ($ok) { "  [OK]" } else { "  [FAIL]" }
    Write-Host "$icon $Label ($Host`:$Port)"
    return $ok
}

Write-Host ""
Write-Host "=============================================="
Write-Host "  WipeWash Bench Connectivity Check"
Write-Host "  BCM  : $BcmHost"
Write-Host "  SIM  : $SimHost"
Write-Host "=============================================="
Write-Host ""

# ── Ping RPiBCM ───────────────────────────────────────────────────────────────
Write-Host "[1] Ping RPiBCM ($BcmHost)..."
if (Test-Connection -ComputerName $BcmHost -Count 2 -Quiet) {
    Write-Host "  [OK] Ping RPiBCM OK"
} else {
    Write-Warning "  [WARN] Ping RPiBCM échoué (non bloquant — WiFi peut bloquer l'ICMP)"
}

# ── Ping RPiSIM ───────────────────────────────────────────────────────────────
Write-Host "[2] Ping RPiSIM ($SimHost)..."
if (Test-Connection -ComputerName $SimHost -Count 2 -Quiet) {
    Write-Host "  [OK] Ping RPiSIM OK"
} else {
    Write-Warning "  [WARN] Ping RPiSIM échoué"
}

# ── Redis ─────────────────────────────────────────────────────────────────────
Write-Host "[3] Redis $BcmHost`:$RedisPort..."
$redisOk = Test-TCPPort -Host $BcmHost -Port $RedisPort -Label "Redis"
if ($redisOk) {
    # Test PING Redis via Python
    $pingResult = & $Python -c @"
import sys, redis
try:
    r = redis.Redis('$BcmHost', $RedisPort, socket_connect_timeout=3)
    r.ping()
    print('  [OK] Redis PONG recu')
except Exception as e:
    print(f'  [FAIL] Redis: {e}')
    sys.exit(1)
"@
    Write-Host $pingResult
    if ($LASTEXITCODE -ne 0) { $allOk = $false }
} else {
    $allOk = $false
}

# ── Port 5555 (LIN/CAN events) ────────────────────────────────────────────────
Write-Host "[4] Port 5555 (LIN/CAN events)..."
if (-not (Test-TCPPort -Host $BcmHost -Port 5555 -Label "LIN/CAN events")) {
    Write-Warning "  [WARN] Port 5555 inaccessible — bcm_tcp_broadcast peut ne pas tourner"
}

# ── Port 5000 (Motor/Vehicle) ─────────────────────────────────────────────────
Write-Host "[5] Port 5000 (Motor/Vehicle TCP)..."
if (-not (Test-TCPPort -Host $BcmHost -Port 5000 -Label "Motor BCM")) {
    Write-Warning "  [WARN] Port 5000 BCM inaccessible"
}
if (-not (Test-TCPPort -Host $SimHost -Port 5000 -Label "Motor SIM")) {
    Write-Warning "  [WARN] Port 5000 SIM inaccessible"
}

# ── Port 5556 (Pump data) ─────────────────────────────────────────────────────
Write-Host "[6] Port 5556 (Pump data)..."
if (-not (Test-TCPPort -Host $BcmHost -Port 5556 -Label "Pump data")) {
    Write-Warning "  [WARN] Port 5556 inaccessible"
}

# ── Résumé ────────────────────────────────────────────────────────────────────
Write-Host ""
Write-Host "=============================================="
if ($allOk) {
    Write-Host "  RESULTAT : BANC OK — tous les services critiques répondent"
    Write-Host "=============================================="
    exit 0
} else {
    Write-Host "  RESULTAT : BANC PARTIEL — services Redis manquants"
    Write-Host "  Vérifiez que bcm_rte.py et bcm_tcp_broadcast.py tournent sur le RPiBCM"
    Write-Host "=============================================="
    exit 2
}
