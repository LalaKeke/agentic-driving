[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
    [string]$LoginHost = 'tongji-u21012',
    [ValidateRange(1024,65535)]
    [int]$LocalProxyPort = 7890,
    [ValidateRange(1024,65535)]
    [int]$RemoteProxyPort = 17890,
    [ValidatePattern('^$|^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
    [string]$ComputeNode = '',
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9_.-]*$')]
    [string]$RemoteUser = 'u21012',
    [ValidateRange(1,65535)]
    [int]$NodeSshPort = 10022,
    [string]$ComputeKnownHostsFile = '',
    [ValidateRange(15,120)]
    [int]$ConnectTimeoutSeconds = 60
)
$ErrorActionPreference = 'Stop'
$client = [System.Net.Sockets.TcpClient]::new()
try {
    $connection = $client.ConnectAsync('127.0.0.1', $LocalProxyPort)
    if (-not $connection.Wait(3000) -or -not $client.Connected) {
        throw "No local proxy listening on 127.0.0.1:$LocalProxyPort"
    }
} finally { $client.Dispose() }
$arguments = @('-tt','-o','StrictHostKeyChecking=yes','-o','ExitOnForwardFailure=yes',
    '-o',"ConnectTimeout=$ConnectTimeoutSeconds",'-o','ServerAliveInterval=30','-o','ServerAliveCountMax=3',
    '-R',"127.0.0.1:${RemoteProxyPort}:127.0.0.1:${LocalProxyPort}")
if ($ComputeNode) {
    if (-not $ComputeKnownHostsFile -or -not (Test-Path -LiteralPath $ComputeKnownHostsFile)) {
        throw 'Compute mode requires a known-hosts file verified for the allocated node.'
    }
    $knownHosts = (Resolve-Path -LiteralPath $ComputeKnownHostsFile).Path
    $arguments += @('-J',$LoginHost,'-p',"$NodeSshPort",'-o',"UserKnownHostsFile=$knownHosts", "$RemoteUser@$ComputeNode")
} else { $arguments += $LoginHost }
Write-Host "Keep this SSH session open. Remote proxy: http://127.0.0.1:$RemoteProxyPort"
Write-Host "After login: source /share/home/u21012/hzy/agentic-driving/hpc/use_proxy.sh $RemoteProxyPort"
& ssh @arguments
exit $LASTEXITCODE
