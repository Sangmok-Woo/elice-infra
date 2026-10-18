# proposal.html -> PDF via Edge headless.  usage: powershell -File proposal\print.ps1
param([string]$Out = (Join-Path $PSScriptRoot '..\elice-infra-proposal.pdf'), [string]$Src = 'proposal.html')
$Out = [System.IO.Path]::GetFullPath($Out)
$src = 'file:///' + (Join-Path $PSScriptRoot $Src).Replace('\', '/')
Start-Process -FilePath "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" `
  -ArgumentList '--headless=new', '--disable-gpu', '--no-pdf-header-footer', '--virtual-time-budget=15000', '--run-all-compositor-stages-before-draw', "--print-to-pdf=$Out", $src -Wait
Start-Sleep 2
Write-Output "written: $Out"
