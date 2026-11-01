# Instala o runner self-hosted do GitHub Actions DESTE repositorio como CONTAINER LINUX no
# Docker Desktop (ADR-006). No Windows o Smart App Control bloqueia as DLLs do runner
# nativo. Cada repositorio tem o seu runner (container e volume proprios); a imagem
# revenda-runner e a mesma nos dois.
#
# Uso (PowerShell normal, sem administrador):
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\03-instalar-runner.ps1
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\03-instalar-runner.ps1 -Remover
#
# Instalacao:
#   1. confere gh (autenticado), Docker Desktop, cluster kind "revenda" e rede docker "kind";
#   2. docker build -t revenda-runner:<versao> infra/runner (versao = ARG RUNNER_VERSION);
#   3. remove o container antigo (o volume com a configuracao e mantido);
#   4. obtem o token de registro (gh api) e faz docker run na rede "kind", com o socket do
#      Docker, o volume do runner (registro e TF_DATA_DIR) e o bind mount de
#      %USERPROFILE%\.revenda em /revenda-state (MESMO state do 04-subir-ambiente.ps1);
#   5. espera o runner aparecer online no GitHub (labels self-hosted, Linux, X64, kind-local).
#   O token so e passado por variavel de ambiente: nunca aparece na tela nem no log.
#   Se o volume ja tiver uma configuracao valida, o container a reaproveita.
# -Remover: desregistra o runner (token de remocao + config.sh remove num container
#   temporario com o mesmo volume), remove o container e o volume.
# Log: .setup\relatorio-runner.txt. Arquivo somente ASCII (Windows PowerShell 5.1).
param(
    [string]$Repositorio = "Caina-Climaco/fiap-soat-revenda-vendas",
    [string]$Container = "revenda-runner-vendas",
    [string]$Volume = "revenda-runner-vendas-persist",
    [string]$Labels = "kind-local",
    [switch]$Remover
)

$ErrorActionPreference = "Continue"
# Recarrega o PATH do registro: ferramentas instaladas pelo winget nesta sessao
# (kind, terraform) so aparecem em janelas novas do PowerShell.
$env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
$ProgressPreference = "SilentlyContinue"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$log = Join-Path $raiz ".setup\relatorio-runner.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Start-Transcript -Path $log -Force | Out-Null

# Executa um comando nativo mostrando stdout e stderr (inclusive no transcript, que no
# PowerShell 5.1 so registra o que passa pelo pipeline) e devolve o codigo de saida.
function Invocar {
    param([string]$Exe, [string[]]$Argumentos)
    Write-Host ">> $Exe $($Argumentos -join ' ')"
    $global:LASTEXITCODE = 0
    & $Exe @Argumentos 2>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
    } | Out-Host
    return $LASTEXITCODE
}

function Falhar([string]$motivo) {
    Remove-Item Env:\RUNNER_TOKEN -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "ERRO: $motivo"
    Write-Host "Log: $log"
    Stop-Transcript | Out-Null
    exit 1
}

# Existe container/volume/rede com esse nome? (sem poluir a tela com erros)
function Existe([string]$tipo, [string]$nome) {
    & docker $tipo inspect $nome *> $null
    return ($LASTEXITCODE -eq 0)
}

function RunnerNoGitHub([string]$nome) {
    $json = (& gh api "repos/$Repositorio/actions/runners?per_page=100") -join "`n"
    if ($LASTEXITCODE -ne 0 -or -not $json) { return $null }
    return (($json | ConvertFrom-Json).runners | Where-Object { $_.name -eq $nome } | Select-Object -First 1)
}

Write-Host "03-instalar-runner.ps1 - $(Get-Date -Format s)"
$nomeRunner = "$($env:COMPUTERNAME.ToLowerInvariant())-kind-vendas"
$dockerfileDir = Join-Path $raiz "infra\runner"
$versao = ""
$linhaVersao = Select-String -Path (Join-Path $dockerfileDir "Dockerfile") -Pattern '^ARG RUNNER_VERSION=(\S+)' | Select-Object -First 1
if ($linhaVersao) { $versao = $linhaVersao.Matches[0].Groups[1].Value }
if (-not $versao) { Falhar "ARG RUNNER_VERSION nao encontrado em infra\runner\Dockerfile." }
$imagem = "revenda-runner:$versao"
Write-Host "Repositorio: $Repositorio | Runner: $nomeRunner | Imagem: $imagem | Container: $Container"

# ------------------------------------------------------------------ pre-requisitos
foreach ($f in @("gh", "docker")) {
    if (-not (Get-Command $f -ErrorAction SilentlyContinue)) {
        Falhar "'$f' nao encontrado no PATH (rode scripts\windows\01-instalar-ferramentas.ps1)."
    }
}
if ((Invocar "gh" @("auth", "status")) -ne 0) { Falhar "gh nao autenticado (gh auth login)." }
if ((Invocar "docker" @("version", "--format", "docker {{.Server.Version}} ({{.Server.Os}})")) -ne 0) {
    Falhar "Docker Desktop nao responde. Inicie o Docker Desktop e tente de novo."
}

# ------------------------------------------------------------------ remocao
if ($Remover) {
    Write-Host "Removendo o runner '$nomeRunner'"
    if (Existe "container" $Container) {
        $null = Invocar "docker" @("stop", "--time", "30", $Container)
    }
    $removidoLocal = $false
    if ((Existe "volume" $Volume) -and (Existe "image" $imagem)) {
        $tokenRemocao = (& gh api -X POST "repos/$Repositorio/actions/runners/remove-token" --jq .token)
        if ($LASTEXITCODE -eq 0 -and $tokenRemocao) {
            # Container temporario com o mesmo volume: restaura a configuracao e roda
            # config.sh remove. O token vai por variavel de ambiente (-e sem valor).
            $env:RUNNER_TOKEN = $tokenRemocao
            # Sem aspas duplas no script: o PowerShell 5.1 as corrompe ao chamar executaveis.
            $script = 'cd /home/runner && for f in .runner .credentials .credentials_rsaparams; do if [ -f persist/$f ]; then cp -f persist/$f .; fi; done; [ -f .runner ] || { echo sem configuracao no volume; exit 3; }; ./config.sh remove --token $RUNNER_TOKEN'
            $codigo = Invocar "docker" @("run", "--rm", "-e", "RUNNER_TOKEN", "-v", "${Volume}:/home/runner/persist", "--entrypoint", "bash", $imagem, "-c", $script)
            Remove-Item Env:\RUNNER_TOKEN -ErrorAction SilentlyContinue
            $removidoLocal = ($codigo -eq 0)
            if (-not $removidoLocal) { Write-Host "AVISO: config.sh remove falhou (codigo $codigo)." }
        } else {
            Write-Host "AVISO: nao foi possivel obter o token de remocao."
        }
    }
    if (-not $removidoLocal) {
        # Garantia: apaga o registro pela API, se ainda existir
        $r = RunnerNoGitHub $nomeRunner
        if ($r) {
            Write-Host "Removendo o registro '$nomeRunner' (id $($r.id)) pela API"
            $null = Invocar "gh" @("api", "-X", "DELETE", "repos/$Repositorio/actions/runners/$($r.id)")
        }
    }
    if (Existe "container" $Container) { $null = Invocar "docker" @("rm", "-f", $Container) }
    if (Existe "volume" $Volume) { $null = Invocar "docker" @("volume", "rm", $Volume) }
    if (RunnerNoGitHub $nomeRunner) { Falhar "o runner '$nomeRunner' ainda aparece no GitHub (Settings > Actions > Runners)." }
    Write-Host "Runner removido. A imagem $imagem continua no Docker local (docker image rm $imagem)."
    Stop-Transcript | Out-Null
    exit 0
}

# ------------------------------------------------------------------ pre-requisitos do cluster
if (-not (Existe "container" "revenda-control-plane")) {
    Falhar "cluster kind 'revenda' nao encontrado (container revenda-control-plane). Rode scripts\windows\04-subir-ambiente.ps1 antes."
}
if (-not (Existe "network" "kind")) {
    Falhar "rede docker 'kind' nao encontrada (ela e criada pelo kind create cluster)."
}
$stateDir = Join-Path $env:USERPROFILE ".revenda"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null
if (-not (Test-Path (Join-Path $stateDir "revenda-vendas.tfstate"))) {
    Write-Host "AVISO: $stateDir\revenda-vendas.tfstate nao existe; o primeiro CD vai criar um state novo."
}
# Runner da Fase 3 (outro repositorio): pode continuar existindo, mas so o desta fase e usado.
if (Existe "container" "revenda-runner") {
    Write-Host "AVISO: o container revenda-runner (Fase 3) ainda existe. Se nao for mais usado:"
    Write-Host "       powershell -File <repositorio da Fase 3>\scripts\windows\03-instalar-runner.ps1 -Remover"
}

# ------------------------------------------------------------------ imagem
$codigo = Invocar "docker" @("build", "--pull", "-t", $imagem, $dockerfileDir)
if ($codigo -ne 0) { Falhar "docker build da imagem do runner falhou (codigo $codigo)." }
$null = Invocar "docker" @("run", "--rm", "--entrypoint", "bash", $imagem, "-c",
    "kind version && kubectl version --client && terraform version && python3 --version && docker --version && jq --version && git --version")

# ------------------------------------------------------------------ container
if (Existe "container" $Container) {
    Write-Host "Removendo o container antigo $Container (o volume $Volume e mantido)"
    if ((Invocar "docker" @("rm", "-f", $Container)) -ne 0) { Falhar "docker rm -f $Container falhou." }
}

$token = (& gh api -X POST "repos/$Repositorio/actions/runners/registration-token" --jq .token)
if ($LASTEXITCODE -ne 0 -or -not $token) { Falhar "nao foi possivel obter o token de registro (escopo repo do gh?)." }

# -e NOME sem valor: o docker le o valor do ambiente deste processo (token fora da linha de comando)
$env:RUNNER_TOKEN = $token
$env:RUNNER_REPO_URL = "https://github.com/$Repositorio"
$env:RUNNER_NAME = $nomeRunner
$env:RUNNER_LABELS = $Labels
$codigo = Invocar "docker" @(
    "run", "-d",
    "--name", $Container,
    "--restart", "unless-stopped",
    "--network", "kind",
    "-v", "/var/run/docker.sock:/var/run/docker.sock",
    "-v", "${Volume}:/home/runner/persist",
    "--mount", "type=bind,source=$stateDir,target=/revenda-state",
    "-e", "RUNNER_TOKEN", "-e", "RUNNER_REPO_URL", "-e", "RUNNER_NAME", "-e", "RUNNER_LABELS",
    $imagem
)
Remove-Item Env:\RUNNER_TOKEN -ErrorAction SilentlyContinue
$token = $null
if ($codigo -ne 0) { Falhar "docker run do runner falhou (codigo $codigo)." }

# ------------------------------------------------------------------ confirmacao
$status = ""
for ($i = 1; $i -le 36; $i++) {
    Start-Sleep -Seconds 5
    $r = RunnerNoGitHub $nomeRunner
    if ($r) {
        $status = "$($r.status) [$((@($r.labels | ForEach-Object { $_.name })) -join ', ')]"
        if ($r.status -eq "online") { break }
    }
    $estado = (& docker inspect -f "{{.State.Status}}" $Container 2>$null)
    if ($estado -ne "running") { Write-Host "Container $Container em estado '$estado'"; break }
}
$null = Invocar "docker" @("logs", "--tail", "30", $Container)
if ($status -like "online*") {
    Write-Host "Runner '$nomeRunner' ONLINE: $status"
} else {
    Write-Host "AVISO: runner '$nomeRunner' ainda nao aparece online (status: '$status')."
    Write-Host "       Veja: docker logs -f $Container   e   Settings > Actions > Runners no GitHub."
}
Write-Host ""
Write-Host "Teste de acesso ao cluster a partir do container:"
$null = Invocar "docker" @("exec", $Container, "bash", "-c",
    "kind export kubeconfig --internal --name revenda >/dev/null && kubectl get nodes -o wide && curl -sS -o /dev/null -w 'kong %{http_code} (404 = no ar, sem rota na raiz)\n' http://revenda-control-plane:30080/")
Write-Host ""
Write-Host "Proximo passo: merge na main ou  gh workflow run cd.yml -R $Repositorio"
Write-Host "Log: $log"
Stop-Transcript | Out-Null
exit 0
