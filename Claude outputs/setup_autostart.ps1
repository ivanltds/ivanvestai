# Configura o IvanVestAI bot (main.py) pra rodar automaticamente sempre
# que voce fizer login no Windows, e reiniciar sozinho se o processo cair.
#
# COMO RODAR: botao direito no PowerShell > "Executar como administrador",
# depois rode este script (uma vez so -- pode rodar de novo se precisar
# mudar algo, ele remove a tarefa anterior antes de recriar).

$botDir = "C:\Users\ivanl\OneDrive\Documents\projetos\ivanvestai-agents\bot"
$pythonPath = (Get-Command python).Source
$taskName = "IvanVestAI-Bot"

if (-not (Test-Path $botDir)) {
    Write-Host "Pasta do bot nao encontrada em $botDir -- confira o caminho." -ForegroundColor Red
    exit 1
}

Write-Host "Python encontrado em: $pythonPath"
Write-Host "Pasta do bot: $botDir"

# Remove uma tarefa anterior com o mesmo nome, se existir
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action = New-ScheduledTaskAction -Execute $pythonPath -Argument "main.py" -WorkingDirectory $botDir
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME

$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 50 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -DontStopOnIdleEnd

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Sobe o IvanVestAI bot (main.py) automaticamente no login, reiniciando sozinho se o processo cair (ate 50x, a cada 2min)." `
    | Out-Null

Write-Host ""
Write-Host "Tarefa '$taskName' criada com sucesso." -ForegroundColor Green
Write-Host "O bot vai iniciar sozinho no proximo login do Windows." -ForegroundColor Green
Write-Host ""
Write-Host "Pra testar AGORA sem precisar deslogar:" -ForegroundColor Yellow
Write-Host "  Start-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Pra ver o status:" -ForegroundColor Yellow
Write-Host "  Get-ScheduledTask -TaskName '$taskName' | Get-ScheduledTaskInfo"
Write-Host ""
Write-Host "Pra DESATIVAR (sem apagar a tarefa):" -ForegroundColor Yellow
Write-Host "  Disable-ScheduledTask -TaskName '$taskName'"
Write-Host ""
Write-Host "Pra REMOVER de vez:" -ForegroundColor Yellow
Write-Host "  Unregister-ScheduledTask -TaskName '$taskName' -Confirm:`$false"
