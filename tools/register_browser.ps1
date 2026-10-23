<#
.SYNOPSIS
Plan and register Lume Native Messaging using the application's shared owner.
.DESCRIPTION
Uses HKCU only for Chrome/Edge. No browser policies, profiles or runtimes change.
The plan is displayed before ShouldProcess authorizes the application to apply it.
#>
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[a-p]{32}$')]
    [string] $ExtensionId,
    [Parameter(Mandatory = $true)]
    [string] $AppExecutable,
    [string] $PreviousManifestDirectory,
    [string] $ManifestDirectory
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$taskHost = (Get-Item -LiteralPath $AppExecutable).FullName
if (-not [IO.File]::Exists($taskHost) -or [IO.Path]::GetExtension($taskHost) -ine '.exe') {
    throw 'AppExecutable deve apontar para LumeNativeHost.exe existente.'
}
$taskArguments = @('--browser-setup', '--extension-id', $ExtensionId)
if (-not $ManifestDirectory) {
    # Preserve this adapter's original source/portable default; parsed registry
    # ownership and changes remain exclusively in the Python owner.
    $taskConfigRoot = Split-Path -Parent $PSScriptRoot
    if (-not [IO.File]::Exists((Join-Path $taskConfigRoot 'LumeNativeHost.exe'))) {
        $taskConfigRoot = Join-Path $taskConfigRoot 'dist'
    }
    $ManifestDirectory = Join-Path $taskConfigRoot 'BrowserHost'
}
if ($ManifestDirectory) {
    $taskArguments += @('--manifest-directory', [IO.Path]::GetFullPath($ManifestDirectory))
}
if ($PreviousManifestDirectory) {
    $taskArguments += @('--previous-manifest-directory', [IO.Path]::GetFullPath($PreviousManifestDirectory))
}
& $taskHost @taskArguments
if ($LASTEXITCODE -ne 0) { throw 'O Lume recusou o plano de integração; nenhuma gravação solicitada.' }
if ($PSCmdlet.ShouldProcess($taskHost, 'Aplicar integração local do Lume com backup para Chrome e Edge')) {
    & $taskHost @taskArguments '--apply'
    if ($LASTEXITCODE -ne 0) { throw 'O Lume não concluiu a integração. Consulte o recibo e o backup informado.' }
}
