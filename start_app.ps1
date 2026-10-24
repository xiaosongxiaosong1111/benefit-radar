param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 4173,
    [switch]$WithSearch
)

$ErrorActionPreference = 'Stop'
$keyName = 'TENCENTCLOUD_WSA_APIKEY'
$previousKey = [Environment]::GetEnvironmentVariable($keyName, 'Process')
$previousUtf8 = [Environment]::GetEnvironmentVariable('PYTHONUTF8', 'Process')
$secureKey = $null
$keyPointer = [IntPtr]::Zero
$plainKey = $null

try {
    if ($WithSearch) {
        $secureKey = Read-Host '输入新建的元宝搜索 API Key（不显示、不保存）' -AsSecureString
        $keyPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureKey)
        $plainKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($keyPointer)
        if ([string]::IsNullOrWhiteSpace($plainKey)) { throw 'API Key 不能为空。' }
        [Environment]::SetEnvironmentVariable($keyName, $plainKey, 'Process')
        $plainKey = $null
    }

    $env:PYTHONUTF8 = '1'
    & py -B (Join-Path $PSScriptRoot 'policy_server.py') --port $Port
    if ($LASTEXITCODE -ne 0) { throw "服务退出，错误码：$LASTEXITCODE" }
}
finally {
    if ($keyPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($keyPointer)
    }
    if ($null -ne $secureKey) { $secureKey.Dispose() }
    $plainKey = $null
    [Environment]::SetEnvironmentVariable('PYTHONUTF8', $previousUtf8, 'Process')
    if ($WithSearch) {
        [Environment]::SetEnvironmentVariable($keyName, $previousKey, 'Process')
    }
}
