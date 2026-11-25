# setup_autostart.ps1
# Run this ONCE as Administrator to register the ecosystem startup tasks.
# After this, everything starts automatically when you log in to Windows.

$ProjectDir = "C:\Users\horat\Downloads\3 Layer Coder Ecosystem"

Write-Host "Setting up Codex Futura autostart tasks..." -ForegroundColor Cyan

# --- Task 1: Budget Guardian (silent background process) ---
$bgAction = New-ScheduledTaskAction `
    -Execute "$ProjectDir\.venv\Scripts\pythonw.exe" `
    -Argument "budget_guardian.py" `
    -WorkingDirectory $ProjectDir

$bgTrigger  = New-ScheduledTaskTrigger -AtLogon
$bgSettings = New-ScheduledTaskSettingsSet `
    -Hidden `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask `
    -TaskName   "CodexFutura-BudgetGuardian" `
    -Action     $bgAction `
    -Trigger    $bgTrigger `
    -Settings   $bgSettings `
    -RunLevel   Highest `
    -Force

Write-Host "  Budget Guardian registered (hidden, runs at login)" -ForegroundColor Green

# --- Task 2: Dashboard (opens browser tab at login) ---
$dashAction = New-ScheduledTaskAction `
    -Execute "$ProjectDir\start_dashboard.bat" `
    -WorkingDirectory $ProjectDir

$dashTrigger  = New-ScheduledTaskTrigger -AtLogon
$dashSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0)

Register-ScheduledTask `
    -TaskName   "CodexFutura-Dashboard" `
    -Action     $dashAction `
    -Trigger    $dashTrigger `
    -Settings   $dashSettings `
    -RunLevel   Highest `
    -Force

Write-Host "  Dashboard registered (opens browser at login)" -ForegroundColor Green

Write-Host ""
Write-Host "Done. Restart or log out/in to activate." -ForegroundColor Cyan
Write-Host "To remove: Unregister-ScheduledTask -TaskName 'CodexFutura-BudgetGuardian' -Confirm:`$false" -ForegroundColor DarkGray
Write-Host "           Unregister-ScheduledTask -TaskName 'CodexFutura-Dashboard' -Confirm:`$false" -ForegroundColor DarkGray
