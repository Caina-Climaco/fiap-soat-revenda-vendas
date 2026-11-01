# Instala as ferramentas que faltam (kind, Terraform, Helm) e inicia o Docker Desktop.
# Uso: powershell -ExecutionPolicy Bypass -File .\scripts\windows\01-instalar-ferramentas.ps1
$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$log = Join-Path $raiz ".setup\relatorio-instalacao.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Start-Transcript -Path $log -Force | Out-Null

$pacotes = @(
    @{ id = "Kubernetes.kind";     cmd = "kind" },
    @{ id = "Hashicorp.Terraform"; cmd = "terraform" },
    @{ id = "Helm.Helm";           cmd = "helm" }
)
foreach ($p in $pacotes) {
    if (Get-Command $p.cmd -ErrorAction SilentlyContinue) {
        Write-Host "[ok] $($p.cmd) ja instalado"
    } else {
        Write-Host "[instalando] $($p.id)"
        winget install --id $p.id --exact --scope user --silent --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[retentando sem --scope user] $($p.id)"
            winget install --id $p.id --exact --silent --accept-package-agreements --accept-source-agreements
        }
    }
}

# Atualiza o PATH desta sessao com o que o winget acabou de registrar
$env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" + [Environment]::GetEnvironmentVariable("Path","User")

Write-Host "`n[docker] iniciando Docker Desktop"
$dd = "C:\Program Files\Docker\Docker\Docker Desktop.exe"
if (Test-Path $dd) { Start-Process $dd }
$pronto = $false
for ($i = 0; $i -lt 60; $i++) {
    docker info *> $null
    if ($LASTEXITCODE -eq 0) { $pronto = $true; break }
    Start-Sleep -Seconds 5
}
Write-Host "[docker] pronto: $pronto"

Write-Host "`n---- versoes"
foreach ($c in @("kind version","terraform -version","helm version --short","kubectl version --client","docker version --format '{{.Server.Version}}'")) {
    Write-Host "> $c"
    try { Invoke-Expression $c 2>&1 | Select-Object -First 1 } catch { Write-Host "falhou: $_" }
}
docker info --format "Docker: CPUs={{.NCPU}} Mem={{.MemTotal}} OS={{.OperatingSystem}}" 2>&1
Stop-Transcript | Out-Null
Write-Host "`nLog salvo em $log. Se algum comando aparecer como nao encontrado, feche e abra o PowerShell e rode de novo."
