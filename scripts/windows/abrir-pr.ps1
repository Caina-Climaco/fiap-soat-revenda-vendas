# Abre um Pull Request para a main com as mudancas do working tree
# (toda mudanca entra por PR + CI; docs/08-ci-cd-infra.md, secao 4).
#
# Uso (na raiz do repositorio ou de qualquer lugar):
#   powershell -ExecutionPolicy Bypass -File .\scripts\windows\abrir-pr.ps1 `
#       -Branch feat/webhook-pagamento -Titulo "feat(vendas): efetivar venda via webhook" `
#       [-Corpo "texto do PR"] [-AutoMerge] [-Acompanhar]
#
# Passos: fetch; cria a branch a partir da origin/main levando as mudancas nao
# commitadas (ou troca para ela, se ja existir); add -A; commit com o titulo; push -u;
# gh pr create --base main; opcionalmente auto-merge (squash; habilita
# allow_auto_merge no repositorio se preciso) e acompanhamento dos checks; se o PR ja
# estiver mergeado ao final, volta para a main e faz pull.
# Codigos de saida: 0 ok; 1 erro (mensagem no log); 2 PR aberto, mas checks falharam.
# Log: .setup\relatorio-pr.txt (pasta ignorada pelo git).
# Arquivo somente ASCII (compatibilidade com Windows PowerShell 5.1).
param(
    [Parameter(Mandatory = $true)][string]$Branch,
    [Parameter(Mandatory = $true)][string]$Titulo,
    [string]$Corpo = "",
    [switch]$AutoMerge,
    [switch]$Acompanhar
)

$ErrorActionPreference = "Continue"
$raiz = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
Set-Location $raiz
$log = Join-Path $raiz ".setup\relatorio-pr.txt"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
Set-Content -Path $log -Value ("abrir-pr.ps1 - " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) -Encoding ascii
$script:Saida = @()

function Escrever([string]$texto) {
    Write-Host $texto
    Add-Content -Path $log -Value $texto
}

function Abortar([string]$motivo) {
    Escrever ""
    Escrever "ERRO: $motivo"
    Escrever "Nada foi desfeito automaticamente. Log completo em $log"
    exit 1
}

# Executa um comando nativo, registra a saida e confere $LASTEXITCODE.
# Com -PermitirFalha, devolve o codigo de saida em vez de abortar.
function Executar {
    param(
        [string]$Descricao,
        [scriptblock]$Comando,
        [switch]$PermitirFalha
    )
    Escrever ">> $Descricao"
    $global:LASTEXITCODE = 0
    $script:Saida = @(& $Comando 2>&1 | ForEach-Object { "$_" })
    $codigo = $LASTEXITCODE
    foreach ($linha in $script:Saida) { Escrever "   $linha" }
    if ($codigo -ne 0 -and -not $PermitirFalha) {
        Abortar "'$Descricao' falhou (codigo $codigo)."
    }
    return $codigo
}

Escrever "Branch: $Branch"
Escrever "Titulo: $Titulo"

# ------------------------------------------------------------------ validacoes
if ($Branch -eq "main" -or $Branch -eq "master") {
    Abortar "a branch do PR nao pode ser a main. Use feat/*, fix/*, docs/* ou infra/*."
}
if ($Branch -notmatch '^(feat|fix|docs|infra|ci|build|chore|refactor|test)/[A-Za-z0-9._/-]+$') {
    Escrever "AVISO: '$Branch' foge da convencao feat/*, fix/*, docs/*, infra/* (docs/08, secao 4.2)."
}
$padraoTitulo = '^(feat|fix|docs|refactor|test|chore|ci|build|perf|style|revert)(\([a-z0-9._-]+\))?!?: .+'
if ($Titulo -notmatch $padraoTitulo) {
    Abortar "titulo fora do padrao Conventional Commits (o CI reprovaria o PR). Exemplo: 'feat(vendas): efetivar venda via webhook'."
}
foreach ($ferramenta in @("git", "gh")) {
    if (-not (Get-Command $ferramenta -ErrorAction SilentlyContinue)) {
        Abortar "'$ferramenta' nao encontrado no PATH (rode scripts\windows\01-instalar-ferramentas.ps1)."
    }
}
$null = Executar "git rev-parse --show-toplevel" { git rev-parse --show-toplevel }
$topo = ($script:Saida | Select-Object -First 1)
if (-not $topo -or ((Resolve-Path $topo).Path -ne (Resolve-Path $raiz).Path)) {
    Abortar "o script deve estar em scripts\windows de um repositorio git (raiz detectada: '$topo')."
}
$null = Executar "gh auth status" { gh auth status }

# ------------------------------------------------------------------ branch
$null = Executar "git fetch origin --prune" { git fetch origin --prune }

$null = Executar "branch atual" { git rev-parse --abbrev-ref HEAD }
$atual = ($script:Saida | Select-Object -First 1)
Escrever "Branch atual: $atual"

if ($atual -eq "main") {
    $null = Executar "commits da origin/main ausentes localmente" { git rev-list --count HEAD..origin/main } -PermitirFalha
    $atras = ($script:Saida | Select-Object -First 1)
    if ($atras -and $atras -ne "0") {
        Escrever "AVISO: a main local esta $atras commit(s) atras da origin/main. O PR pode exigir atualizacao da branch antes do merge."
    }
}

if ($atual -ne $Branch) {
    # git switch leva as mudancas NAO commitadas (working tree e index) para a branch de
    # destino; se alguma conflitar com o destino, o git recusa e o script aborta.
    $dicaConflito = "Se o git recusou por conflito com mudancas locais, rode 'git stash', execute o script de novo e depois 'git stash pop'."
    $existeLocal = Executar "branch local existe?" { git show-ref --verify --quiet "refs/heads/$Branch" } -PermitirFalha
    if ($existeLocal -eq 0) {
        $codigo = Executar "git switch $Branch (leva as mudancas do working tree)" { git switch $Branch } -PermitirFalha
        if ($codigo -ne 0) { Abortar "git switch $Branch falhou (codigo $codigo). $dicaConflito" }
    } else {
        $existeRemota = Executar "branch remota existe?" { git ls-remote --exit-code --heads origin $Branch } -PermitirFalha
        if ($existeRemota -eq 0) {
            $codigo = Executar "git switch --track origin/$Branch" { git switch --track "origin/$Branch" } -PermitirFalha
            if ($codigo -ne 0) { Abortar "git switch --track origin/$Branch falhou (codigo $codigo). $dicaConflito" }
        } else {
            # Branch nova sempre a partir da origin/main atualizada (nao da main local).
            $null = Executar "commits locais de '$atual' fora da origin/main" { git rev-list --count origin/main..HEAD } -PermitirFalha
            $locais = ($script:Saida | Select-Object -First 1)
            if ($locais -and $locais -ne "0") {
                Escrever "AVISO: '$atual' tem $locais commit(s) que nao estao na origin/main; eles NAO entram neste PR (so as mudancas nao commitadas)."
            }
            $codigo = Executar "git switch -c $Branch origin/main" { git switch --no-track -c $Branch origin/main } -PermitirFalha
            if ($codigo -ne 0) { Abortar "git switch -c $Branch origin/main falhou (codigo $codigo). $dicaConflito" }
        }
    }
}

# ------------------------------------------------------------------ commit e push
$null = Executar "git add -A" { git add -A }
$null = Executar "mudancas preparadas" { git diff --cached --name-status }
if ($script:Saida.Count -gt 0) {
    $null = Executar "git commit" { git commit -m $Titulo }
} else {
    Escrever "Nenhuma mudanca nova para commitar."
}
$null = Executar "commits da branch em relacao a origin/main" { git rev-list --count origin/main..HEAD }
$aFrente = ($script:Saida | Select-Object -First 1)
if (-not $aFrente -or $aFrente -eq "0") {
    Abortar "a branch '$Branch' nao tem commits em relacao a origin/main; nao ha o que propor em um PR."
}
$null = Executar "git push -u origin $Branch" { git push -u origin $Branch }

# ------------------------------------------------------------------ Pull Request
$existePr = Executar "PR existente para a branch?" { gh pr view $Branch --json state --jq ".state" } -PermitirFalha
$estadoPr = ""
if ($existePr -eq 0) { $estadoPr = ($script:Saida | Select-Object -First 1) }

if ($estadoPr -eq "OPEN") {
    Escrever "Ja existe PR aberto para '$Branch'; o push acima o atualizou."
} else {
    if (-not $Corpo) {
        $template = Join-Path $raiz ".github\pull_request_template.md"
        if (Test-Path $template) {
            $Corpo = Get-Content -Path $template -Raw -Encoding UTF8
        } else {
            $Corpo = "## O que muda e por que`n`n$Titulo`n`n## Como testar`n`nCI verde (qualidade, testes, imagem, infra); apos o merge, o CD implanta no kind e roda o e2e."
        }
    }
    $arquivoCorpo = Join-Path $raiz ".setup\corpo-pr.md"
    [System.IO.File]::WriteAllText($arquivoCorpo, $Corpo, (New-Object System.Text.UTF8Encoding($false)))
    $null = Executar "gh pr create" { gh pr create --base main --head $Branch --title $Titulo --body-file $arquivoCorpo }
}
$null = Executar "URL do PR" { gh pr view $Branch --json url --jq ".url" }
$urlPr = ($script:Saida | Select-Object -First 1)

if ($AutoMerge) {
    # Merge automatico (squash) assim que os checks obrigatorios passarem.
    # Exige allow_auto_merge=true no repositorio; se falhar, habilita e tenta de novo.
    $codigo = Executar "gh pr merge --squash --auto --delete-branch" { gh pr merge $Branch --squash --auto --delete-branch } -PermitirFalha
    if ($codigo -ne 0) {
        Escrever "Auto-merge recusado; habilitando allow_auto_merge no repositorio e tentando de novo."
        $null = Executar "nome do repositorio" { gh repo view --json nameWithOwner --jq ".nameWithOwner" }
        $repo = ($script:Saida | Select-Object -First 1)
        if (-not $repo) { Abortar "nao foi possivel descobrir o repositorio (gh repo view)." }
        $null = Executar "gh api -X PATCH repos/$repo -F allow_auto_merge=true" { gh api -X PATCH "repos/$repo" -F allow_auto_merge=true --jq ".allow_auto_merge" }
        $null = Executar "gh pr merge --squash --auto --delete-branch (2a tentativa)" { gh pr merge $Branch --squash --auto --delete-branch }
    }
}

# ------------------------------------------------------------------ acompanhamento
$checksOk = $true
if ($Acompanhar) {
    # Logo apos o push os checks podem ainda nao estar registrados: espera ate 2 min.
    $registrados = $false
    for ($i = 0; $i -lt 24; $i++) {
        $global:LASTEXITCODE = 0
        $lista = @(gh pr checks $Branch 2>&1 | ForEach-Object { "$_" })
        if (($lista -join " ") -notmatch "no checks reported") { $registrados = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $registrados) {
        Escrever "AVISO: nenhum check registrado no PR apos 2 minutos."
        $checksOk = $false
    } else {
        Escrever ">> gh pr checks --watch (saida ao vivo)"
        $global:LASTEXITCODE = 0
        gh pr checks $Branch --watch --interval 15
        $codigoChecks = $LASTEXITCODE
        $null = Executar "resultado dos checks" { gh pr checks $Branch } -PermitirFalha
        if ($codigoChecks -ne 0) {
            $checksOk = $false
            Escrever "AVISO: ha checks com falha (codigo $codigoChecks). Corrija na branch '$Branch' e rode o script de novo."
        }
    }
    if ($checksOk -and $AutoMerge) {
        Escrever "Checks verdes; aguardando o auto-merge (ate 2 min)..."
        for ($i = 0; $i -lt 24; $i++) {
            $global:LASTEXITCODE = 0
            $estado = (gh pr view $Branch --json state --jq ".state" 2>$null | Select-Object -First 1)
            if ($estado -eq "MERGED") { break }
            Start-Sleep -Seconds 5
        }
    }
}

# ------------------------------------------------------------------ volta para a main se mergeado
$global:LASTEXITCODE = 0
$estadoFinal = (gh pr view $Branch --json state --jq ".state" 2>$null | Select-Object -First 1)
Escrever "Estado do PR: $estadoFinal"
if ($estadoFinal -eq "MERGED") {
    $null = Executar "git switch main" { git switch main }
    $null = Executar "git pull --ff-only origin main" { git pull --ff-only origin main }
    $null = Executar "remove a branch local ja mergeada (squash)" { git branch -D $Branch } -PermitirFalha
    Escrever "PR mergeado; o CD (runner self-hosted) implanta o novo commit da main."
} else {
    Escrever "Voce continua na branch '$Branch'. Quando o PR for mergeado: git switch main; git pull --ff-only"
}

Escrever ""
Escrever "PR: $urlPr"
Escrever "Log: $log"
if (-not $checksOk) { exit 2 }
exit 0
