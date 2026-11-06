# Development diagnostic only. Never creates users, configuration or financial data.
[CmdletBinding()]
param(
    [string]$BaseUrl = 'http://127.0.0.1:8082',
    [string]$ExpectedIdentity = ''
)
$ErrorActionPreference = 'Stop'
$VerbosePreference = 'SilentlyContinue'
$DebugPreference = 'SilentlyContinue'

function Invoke-FinanceReadOnlyGet {
    param($Client, [Uri]$Uri, [string]$Authorization = '')
    $request = $response = $stream = $content = $cancellation = $null
    try {
        $cancellation = [Threading.CancellationTokenSource]::new()
        $cancellation.CancelAfter(10000)
        $request = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::Get, $Uri)
        [void]$request.Headers.TryAddWithoutValidation('Accept', 'application/json')
        if ($Authorization) {
            [void]$request.Headers.TryAddWithoutValidation('Authorization', $Authorization)
        }
        $response = $Client.SendAsync($request, [Net.Http.HttpCompletionOption]::ResponseHeadersRead,
            $cancellation.Token).GetAwaiter().GetResult()
        $code = [int]$response.StatusCode
        if ($code -ne 200) { return [pscustomobject]@{ Status = $code; Content = '' } }
        $stream = $response.Content.ReadAsStreamAsync().GetAwaiter().GetResult()
        $content = [IO.MemoryStream]::new()
        $buffer = [byte[]]::new(4096)
        while (($count = $stream.ReadAsync($buffer, 0, $buffer.Length,
                    $cancellation.Token).GetAwaiter().GetResult()) -gt 0) {
            if ($content.Length + $count -gt 16384) { throw [FormatException]::new() }
            $content.Write($buffer, 0, $count)
        }
        $text = [Text.UTF8Encoding]::new($false, $true).GetString($content.ToArray())
        return [pscustomobject]@{ Status = $code; Content = $text }
    }
    finally {
        foreach ($resource in @($stream, $content, $response, $request, $cancellation)) {
            if ($null -ne $resource) { $resource.Dispose() }
        }
        $Authorization = $text = $null
    }
}

$handler = $client = $keyInput = $secretInput = $key = $secret = $authorization = $null
$result = [ordered]@{
    connection = 'not_configured'; site_reachable = $false; identity_verified = $false
    available = $false; accounting_setup = 'not_assessed'; posting_enabled = $false; read_only = $true
}
try {
    $origin = $null
    if (-not [Uri]::TryCreate($BaseUrl, [UriKind]::Absolute, [ref]$origin) -or
        $origin.UserInfo -or $origin.Query -or $origin.Fragment -or $origin.AbsolutePath -ne '/' -or
        ($origin.Scheme -ne 'https' -and -not ($origin.Scheme -eq 'http' -and
            $origin.Host -in @('127.0.0.1', 'localhost', 'finance.localhost')))) {
        throw [ArgumentException]::new()
    }
    if (-not $ExpectedIdentity) { $ExpectedIdentity = Read-Host 'Expected nonprivileged integration identity' }
    $ExpectedIdentity = $ExpectedIdentity.Trim()
    if (-not $ExpectedIdentity -or $ExpectedIdentity -in @('Administrator', 'Guest')) {
        throw [ArgumentException]::new()
    }
    Add-Type -AssemblyName System.Net.Http
    $handler = [Net.Http.HttpClientHandler]::new()
    $handler.AllowAutoRedirect = $false
    $handler.UseCookies = $false
    $handler.UseProxy = $false
    $client = [Net.Http.HttpClient]::new($handler)
    $client.Timeout = [TimeSpan]::FromSeconds(10)
    $result.connection = 'unavailable'
    $ping = Invoke-FinanceReadOnlyGet $client ([Uri]::new($origin, '/api/method/ping'))
    if ($ping.Status -eq 200) {
        if (-not $ping.Content.TrimStart().StartsWith('{')) { throw [FormatException]::new() }
        try { $pong = ConvertFrom-Json -InputObject $ping.Content }
        catch { throw [FormatException]::new() }
        if ($pong -isnot [pscustomobject] -or $pong.message -isnot [string] -or
            $pong.message -cne 'pong') { throw [FormatException]::new() }
        $result.site_reachable = $true
        $keyInput = Read-Host 'Frappe API key (private input)' -AsSecureString
        $secretInput = Read-Host 'Frappe API secret (private input)' -AsSecureString
        $key = [Net.NetworkCredential]::new('', $keyInput).Password
        $secret = [Net.NetworkCredential]::new('', $secretInput).Password
        $result.connection = 'not_configured'
        if (-not $key -or -not $secret -or $key -match '[\r\n:]' -or $secret -match '[\r\n]') {
            throw [ArgumentException]::new()
        }
        $authorization = 'token ' + $key + ':' + $secret
        $result.connection = 'unavailable'
        $auth = Invoke-FinanceReadOnlyGet $client ([Uri]::new($origin,
            '/api/method/frappe.auth.get_logged_user')) $authorization
        if ($auth.Status -in @(401, 403)) { $result.connection = 'permission_denied' }
        elseif ($auth.Status -eq 200) {
            if (-not $auth.Content.TrimStart().StartsWith('{')) { throw [FormatException]::new() }
            try { $identity = ConvertFrom-Json -InputObject $auth.Content }
            catch { throw [FormatException]::new() }
            if ($identity -isnot [pscustomobject] -or $identity.message -isnot [string] -or
                $identity.message -cne $ExpectedIdentity) { throw [FormatException]::new() }
            $result.connection = 'authenticated'
            $result.identity_verified = $true
        }
    }
}
catch {
    # Inspect types only. Never emit exception text, native content, URLs or headers.
    $failure = $_.Exception
    while ($null -ne $failure) {
        if ($failure -is [OperationCanceledException] -or $failure -is [TimeoutException]) {
            $result.connection = 'timeout'; break
        }
        if ($failure -is [FormatException] -or $failure -is [Text.DecoderFallbackException]) {
            $result.connection = 'invalid_response'; break
        }
        $failure = $failure.InnerException
    }
}
finally {
    if ($null -ne $client) { $client.Dispose() }
    elseif ($null -ne $handler) { $handler.Dispose() }
    foreach ($privateInput in @($keyInput, $secretInput)) {
        if ($null -ne $privateInput) { $privateInput.Dispose() }
    }
    $key = $secret = $authorization = $keyInput = $secretInput = $auth = $identity = $ping = $pong = $null
}
[pscustomobject]$result
