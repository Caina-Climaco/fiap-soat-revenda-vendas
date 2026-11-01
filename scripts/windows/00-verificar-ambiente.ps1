# Verifica as ferramentas necessarias para o projeto e o runner self-hosted.
# Uso: powershell -ExecutionPolicy Bypass -File .\scripts\windows\00-verificar-ambiente.ps1
$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$saida = Join-Path $raiz ".setup\relatorio-ambiente.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $saida) | Out-Null

function Testar($nome, $cmd) {
    $exe = Get-Command $nome -ErrorAction SilentlyContinue
    if ($null -eq $exe) { return "$nome : NAO ENCONTRADO" }
    try { $v = (Invoke-Expression $cmd 2>&1 | Select-Object -First 1) } catch { $v = $_.Exception.Message }
    return "$nome : $($exe.Source) | $v"
}

$linhas = @()
$linhas += "Data: $(Get-Date -Format s)"
$linhas += "Windows: $((Get-CimInstance Win32_OperatingSystem).Caption) $((Get-CimInstance Win32_OperatingSystem).Version)"
$linhas += "RAM total (GB): $([math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1))"
$linhas += "CPU logicos: $((Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors)"
$linhas += "Disco C livre (GB): $([math]::Round((Get-PSDrive C).Free/1GB,1))"
$linhas += "PowerShell: $($PSVersionTable.PSVersion)"
$linhas += "Admin nesta sessao: $(([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator))"
$linhas += "---- ferramentas"
$linhas += Testar "winget" "winget --version"
$linhas += Testar "git" "git --version"
$linhas += Testar "docker" "docker version --format '{{.Server.Version}}'"
$linhas += Testar "kind" "kind version"
$linhas += Testar "kubectl" "kubectl version --client"
$linhas += Testar "terraform" "terraform -version"
$linhas += Testar "helm" "helm version --short"
$linhas += Testar "gh" "gh --version"
$linhas += Testar "python" "python --version"
$linhas += Testar "wsl" "wsl --status"
$linhas += "---- docker"
$linhas += (docker info --format "Server={{.ServerVersion}} OS={{.OperatingSystem}} CPUs={{.NCPU}} Mem={{.MemTotal}}" 2>&1 | Select-Object -First 2)
$linhas += (docker ps -a --format "{{.Names}} {{.Status}}" 2>&1 | Select-Object -First 15)
$linhas += "---- kind clusters"
$linhas += (kind get clusters 2>&1 | Select-Object -First 10)
$linhas += "---- git config global"
$linhas += "user.name=$(git config --global user.name)"
$linhas += "user.email=$(git config --global user.email)"
$linhas += "credential.helper=$(git config --global credential.helper)"
$linhas += "---- gh auth"
$linhas += (gh auth status 2>&1 | Select-Object -First 6)
$linhas += "---- portas em uso (8080, 15432, 27017, 3000, 9090, 16686)"
$linhas += (Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object { $_.LocalPort -in 8080,15432,27017,3000,9090,16686 } | ForEach-Object { "porta $($_.LocalPort) PID $($_.OwningProcess) $((Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue).ProcessName)" })

$linhas | Out-File -Encoding utf8 $saida
$linhas
Write-Host "`nRelatorio salvo em $saida"
