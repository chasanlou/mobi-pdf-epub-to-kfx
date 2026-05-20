param(
    [switch]$NoMessageBox,
    [string]$ConfigPath,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$InputFiles
)

$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)

$MobitoolExe = 'E:\奇奇怪怪小程序\MobiTool.exe'
$MobitoolOutputRoot = 'E:\Maga_Output'
$KccExe = 'C:\Kindle Previewer 3\lib\fc\bin\KCC_10.1.3.exe'
$KccOutputRoot = Join-Path $MobitoolOutputRoot 'cbz'
$KfxGuiExe = 'E:\kfx\kckfxgen-gui.exe'
$KfxCliScript = 'E:\kckfxgen_cli_runtime\run_kckfxgen_cli.py'
$KfxCliRuntime = 'E:\kckfxgen_cli_runtime'
$KfxPythonPath = ''
$ExtractPythonPath = ''
$KfxOutputRoot = 'D:\漫画'
$LogFile = Join-Path $PSScriptRoot '转换日志.txt'
$MobiExtractScript = Join-Path $PSScriptRoot 'extract_mobi_images.py'
$BatchKfxScript = Join-Path $PSScriptRoot 'batch_kfxgen.py'

$StableSeconds = 12
$MobiToolTimeoutMinutes = 45
$KccTimeoutMinutes = 90
$KfxTimeoutMinutes = 90
$script:ActiveKccProcessId = $null

function Apply-Config {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return }
    $cfg = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($cfg.kcc_path) { $script:KccExe = [string]$cfg.kcc_path }
    if ($cfg.image_output_dir) { $script:MobitoolOutputRoot = [string]$cfg.image_output_dir }
    if ($cfg.cbz_output_dir) { $script:KccOutputRoot = [string]$cfg.cbz_output_dir }
    if ($cfg.kfx_output_dir) { $script:KfxOutputRoot = [string]$cfg.kfx_output_dir }
    if ($cfg.kfx_python_path) { $script:KfxPythonPath = [string]$cfg.kfx_python_path }
    if ($cfg.extract_python_path) { $script:ExtractPythonPath = [string]$cfg.extract_python_path }
    if (-not $cfg.cbz_output_dir -and $cfg.image_output_dir) {
        $script:KccOutputRoot = Join-Path $script:MobitoolOutputRoot 'cbz'
    }
}

Apply-Config -Path $ConfigPath

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class NativeWin {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern bool PostMessage(IntPtr hWnd, uint msg, IntPtr wParam, IntPtr lParam);
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int X, int Y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint dwFlags, uint dx, uint dy, uint dwData, UIntPtr dwExtraInfo);
    public struct RECT { public int Left; public int Top; public int Right; public int Bottom; }
}
'@

function Write-Log {
    param([string]$Message)
    $line = '[{0:yyyy-MM-dd HH:mm:ss}] {1}' -f (Get-Date), $Message
    Add-Content -LiteralPath $LogFile -Value $line -Encoding UTF8
    Write-Host $line
}

function Stop-ActiveChildProcesses {
    if ($script:ActiveKccProcessId) {
        Get-Process -Id $script:ActiveKccProcessId -ErrorAction SilentlyContinue | Stop-Process -Force
        $script:ActiveKccProcessId = $null
    }
}

function Assert-Path {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) {
        throw "$Name 不存在：$Path"
    }
}

function Ensure-Directory {
    param([string]$Path, [string]$Name)
    if (-not (Test-Path -LiteralPath $Path)) {
        Write-Log "创建$Name：$Path"
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Set-ClipboardText {
    param([string]$Text)
    [System.Windows.Forms.Clipboard]::SetText($Text)
}

function Send-Keys {
    param([string]$Keys, [int]$DelayMs = 250)
    [System.Windows.Forms.SendKeys]::SendWait($Keys)
    Start-Sleep -Milliseconds $DelayMs
}

function Wait-Window {
    param([string]$ProcessName, [string]$TitleLike, [int]$TimeoutSeconds = 30)
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $proc = Get-Process -Name $ProcessName -ErrorAction SilentlyContinue |
            Where-Object { $_.MainWindowHandle -ne 0 -and $_.MainWindowTitle -like $TitleLike } |
            Select-Object -First 1
        if ($proc) { return $proc }
        Start-Sleep -Milliseconds 300
    } while ((Get-Date) -lt $deadline)
    throw "等待窗口超时：$ProcessName / $TitleLike"
}

function Focus-Window {
    param([System.Diagnostics.Process]$Process)
    [NativeWin]::ShowWindow($Process.MainWindowHandle, 9) | Out-Null
    [NativeWin]::SetForegroundWindow($Process.MainWindowHandle) | Out-Null
    Start-Sleep -Milliseconds 500
}

function Click-ClientPoint {
    param([IntPtr]$Hwnd, [int]$X, [int]$Y)
    $lParam = [IntPtr](($Y -shl 16) -bor ($X -band 0xffff))
    [NativeWin]::PostMessage($Hwnd, 0x0201, [IntPtr]1, $lParam) | Out-Null
    Start-Sleep -Milliseconds 80
    [NativeWin]::PostMessage($Hwnd, 0x0202, [IntPtr]0, $lParam) | Out-Null
    Start-Sleep -Milliseconds 350
}

function Click-ClientPointForeground {
    param([IntPtr]$Hwnd, [int]$X, [int]$Y)
    $rect = New-Object NativeWin+RECT
    [NativeWin]::GetWindowRect($Hwnd, [ref]$rect) | Out-Null
    [NativeWin]::SetCursorPos($rect.Left + $X, $rect.Top + $Y) | Out-Null
    Start-Sleep -Milliseconds 120
    [NativeWin]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 80
    [NativeWin]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)
    Start-Sleep -Milliseconds 250
}

function Invoke-UiButton {
    param([System.Diagnostics.Process]$Process, [string]$AutomationId)
    $root = [Windows.Automation.AutomationElement]::RootElement
    $pidCond = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::ProcessIdProperty, [int]$Process.Id)
    $win = $root.FindFirst([Windows.Automation.TreeScope]::Children, $pidCond)
    if (-not $win) { throw "找不到窗口：$($Process.ProcessName)" }
    $idCond = New-Object Windows.Automation.PropertyCondition([Windows.Automation.AutomationElement]::AutomationIdProperty, $AutomationId)
    $button = $win.FindFirst([Windows.Automation.TreeScope]::Descendants, $idCond)
    if (-not $button) { throw "找不到按钮：$AutomationId" }
    $pattern = $button.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern)
    $pattern.Invoke()
    Start-Sleep -Milliseconds 500
}

function Wait-FileStable {
    param([string]$Path, [datetime]$NotOlderThan, [int]$TimeoutMinutes, [string]$Label)
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $lastLength = -1
    $lastWrite = [datetime]::MinValue
    $stableSince = $null
    do {
        if (Test-Path -LiteralPath $Path) {
            $item = Get-Item -LiteralPath $Path
            if ($item.LastWriteTime -ge $NotOlderThan) {
                if ($item.Length -eq $lastLength -and $item.LastWriteTime -eq $lastWrite) {
                    if (-not $stableSince) { $stableSince = Get-Date }
                    if (((Get-Date) - $stableSince).TotalSeconds -ge $StableSeconds) {
                        return $item
                    }
                } else {
                    $lastLength = $item.Length
                    $lastWrite = $item.LastWriteTime
                    $stableSince = Get-Date
                }
            }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "等待文件完成超时：$Label ($Path)"
}

function Wait-FolderStable {
    param([string]$Path, [datetime]$NotOlderThan, [int]$TimeoutMinutes, [string]$Label)
    $deadline = (Get-Date).AddMinutes($TimeoutMinutes)
    $lastCount = -1
    $lastSize = -1
    $lastWrite = [datetime]::MinValue
    $stableSince = $null
    do {
        if (Test-Path -LiteralPath $Path) {
            $files = Get-ChildItem -LiteralPath $Path -Recurse -File -ErrorAction SilentlyContinue
            $count = @($files).Count
            $size = ($files | Measure-Object -Property Length -Sum).Sum
            if ($null -eq $size) { $size = 0 }
            $write = (Get-Item -LiteralPath $Path).LastWriteTime
            if ($write -ge $NotOlderThan -and $count -gt 0) {
                if ($count -eq $lastCount -and $size -eq $lastSize -and $write -eq $lastWrite) {
                    if (-not $stableSince) { $stableSince = Get-Date }
                    if (((Get-Date) - $stableSince).TotalSeconds -ge $StableSeconds) {
                        return Get-Item -LiteralPath $Path
                    }
                } else {
                    $lastCount = $count
                    $lastSize = $size
                    $lastWrite = $write
                    $stableSince = Get-Date
                }
            }
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    throw "等待文件夹完成超时：$Label ($Path)"
}

function Select-InputFilesWithWindow {
    $selected = New-Object System.Collections.Generic.List[string]
    $form = New-Object Windows.Forms.Form
    $form.Text = '拖入漫画文件开始转换'
    $form.Width = 560
    $form.Height = 220
    $form.StartPosition = 'CenterScreen'
    $form.AllowDrop = $true
    $label = New-Object Windows.Forms.Label
    $label.Dock = 'Fill'
    $label.TextAlign = 'MiddleCenter'
    $label.Font = New-Object Drawing.Font('Microsoft YaHei UI', 12)
    $label.Text = "把 .mobi / .epub / .pdf 或图片文件夹拖到这里。`r`n会按文件名逐本处理，只清理对应的中间文件。"
    $form.Controls.Add($label)
    $form.Add_DragEnter({
        if ($_.Data.GetDataPresent([Windows.Forms.DataFormats]::FileDrop)) {
            $_.Effect = [Windows.Forms.DragDropEffects]::Copy
        }
    })
    $form.Add_DragDrop({
        foreach ($path in $_.Data.GetData([Windows.Forms.DataFormats]::FileDrop)) {
            if ((Test-Path -LiteralPath $path -PathType Container) -or ([IO.Path]::GetExtension($path) -in @('.mobi', '.epub', '.pdf'))) { $selected.Add($path) }
        }
        $form.Close()
    })
    [void]$form.ShowDialog()
    return $selected.ToArray()
}

function Run-BookImageExtract {
    param([string]$BookPath, [string]$BaseName)
    $targetFolder = Join-Path $MobitoolOutputRoot $BaseName
    if (Test-Path -LiteralPath $targetFolder) {
        Write-Log "删除同名旧图片文件夹：$targetFolder"
        Remove-Item -LiteralPath $targetFolder -Recurse -Force
    }

    $started = Get-Date
    $ext = [IO.Path]::GetExtension($BookPath).ToLowerInvariant()
    Write-Log "开始图片提取：$BookPath"
    Push-Location $PSScriptRoot
    try {
        $extractResult = Invoke-NativeProcessCaptured `
            -FilePath $script:ExtractPython.FilePath `
            -Arguments (@($script:ExtractPython.PrefixArgs) + @($MobiExtractScript, $BookPath, $targetFolder)) `
            -WorkingDirectory $PSScriptRoot `
            -Environment @{
                PYTHONUTF8 = '1'
                PYTHONIOENCODING = 'utf-8'
            }
        $extractOutput = @()
        if ($extractResult.StdOut) {
            $extractOutput += ($extractResult.StdOut -split "`r?`n")
        }
        if ($extractResult.StdErr) {
            $extractOutput += ($extractResult.StdErr -split "`r?`n")
        }
        $extractExitCode = $extractResult.ExitCode
    } finally {
        Pop-Location
    }
    foreach ($line in @($extractOutput)) {
        if ($line) {
            Write-Log "图片提取器：$line"
        }
    }
    if ($extractExitCode -ne 0) {
        throw "$ext 图片提取失败，退出码：$extractExitCode"
    }
    Wait-FolderStable -Path $targetFolder -NotOlderThan $started -TimeoutMinutes $MobiToolTimeoutMinutes -Label "$BaseName 图片文件夹" | Out-Null
    Write-Log "图片提取完成：$targetFolder"
    return $targetFolder
}

function Run-Kcc {
    param([string]$ImageFolder, [string]$BaseName)
    $ImageFolder = (Resolve-Path -LiteralPath $ImageFolder).Path
    $cbzPath = Join-Path $KccOutputRoot ($BaseName + '.cbz')
    if (Test-Path -LiteralPath $cbzPath) {
        Write-Log "删除同名旧 CBZ：$cbzPath"
        Remove-Item -LiteralPath $cbzPath -Force
    }

    $started = Get-Date
    Write-Log "KCC 输出 CBZ：$ImageFolder"
    Write-Log "KCC 输入文件夹确认：$ImageFolder"
    Start-Process -FilePath $KccExe -WorkingDirectory (Split-Path $KccExe) | Out-Null
    $proc = Wait-Window -ProcessName ([IO.Path]::GetFileNameWithoutExtension($KccExe)) -TitleLike '*Kindle Comic Converter*'
    Focus-Window $proc
    Invoke-UiButton -Process $proc -AutomationId 'QApplicationMessaging.mainWindow.centralWidget.buttonWidget.directoryButton'
    Start-Sleep -Milliseconds 1000
    Set-ClipboardText $ImageFolder
    Send-Keys '^l' 300
    Send-Keys '^a' 150
    Send-Keys '^v' 300
    Send-Keys '{ENTER}' 800
    Send-Keys '%c' 800
    Focus-Window $proc
    Invoke-UiButton -Process $proc -AutomationId 'QApplicationMessaging.mainWindow.centralWidget.buttonWidget.convertButton'
    Wait-FileStable -Path $cbzPath -NotOlderThan $started -TimeoutMinutes $KccTimeoutMinutes -Label "$BaseName CBZ" | Out-Null
    Get-Process -Id $proc.Id -ErrorAction SilentlyContinue | Stop-Process -Force
    Write-Log "CBZ 输出完成：$cbzPath"
    return $cbzPath
}

function Add-KccInputFolder {
    param([System.Diagnostics.Process]$Process, [string]$ImageFolder)
    Focus-Window $Process
    Invoke-UiButton -Process $Process -AutomationId 'QApplicationMessaging.mainWindow.centralWidget.buttonWidget.directoryButton'
    Start-Sleep -Milliseconds 1000
    Set-ClipboardText $ImageFolder
    Send-Keys '^l' 300
    Send-Keys '^a' 150
    Send-Keys '^v' 300
    Send-Keys '{ENTER}' 800
    Send-Keys '%c' 800
}

function Run-KccBatch {
    param([object[]]$Items)
    if ($Items.Count -eq 0) { return @() }

    $started = Get-Date
    foreach ($item in $Items) {
        $item.ImageFolder = (Resolve-Path -LiteralPath $item.ImageFolder).Path
        $item.CbzPath = Join-Path $KccOutputRoot ($item.BaseName + '.cbz')
        if (Test-Path -LiteralPath $item.CbzPath) {
            Write-Log "删除同名旧 CBZ：$($item.CbzPath)"
            Remove-Item -LiteralPath $item.CbzPath -Force
        }
    }

    Write-Log "KCC 批量加入 $($Items.Count) 个图片文件夹。"
    Start-Process -FilePath $KccExe -WorkingDirectory (Split-Path $KccExe) | Out-Null
    $proc = Wait-Window -ProcessName ([IO.Path]::GetFileNameWithoutExtension($KccExe)) -TitleLike '*Kindle Comic Converter*'
    $script:ActiveKccProcessId = $proc.Id
    try {
        foreach ($item in $Items) {
            Write-Log "KCC 加入队列：$($item.ImageFolder)"
            Add-KccInputFolder -Process $proc -ImageFolder $item.ImageFolder
        }
        Focus-Window $proc
        Write-Log "KCC 开始批量输出 CBZ。"
        Invoke-UiButton -Process $proc -AutomationId 'QApplicationMessaging.mainWindow.centralWidget.buttonWidget.convertButton'
        foreach ($item in $Items) {
            Wait-FileStable -Path $item.CbzPath -NotOlderThan $started -TimeoutMinutes $KccTimeoutMinutes -Label "$($item.BaseName) CBZ" | Out-Null
            Write-Log "CBZ 输出完成：$($item.CbzPath)"
        }
    } finally {
        Get-Process -Id $proc.Id -ErrorAction SilentlyContinue | Stop-Process -Force
        $script:ActiveKccProcessId = $null
    }

    return @($Items | ForEach-Object { $_.CbzPath })
}

function Get-BookMetadataFromName {
    param([string]$BaseName)
    $idx = $BaseName.IndexOf('-')
    if ($idx -ge 0) {
        return @{
            Title = $BaseName.Substring(0, $idx)
            Author = $BaseName.Substring($idx + 1)
            Publisher = ''
        }
    }
    return @{
        Title = $BaseName
        Author = ''
        Publisher = ''
    }
}

function ConvertTo-ProcessArgument {
    param([string]$Value)
    if ($null -eq $Value) { return '""' }
    if ($Value -notmatch '[\s"]') { return $Value }
    $escaped = $Value -replace '(\\*)"', '$1$1\"'
    $escaped = $escaped -replace '(\\+)$', '$1$1'
    return '"' + $escaped + '"'
}

function Invoke-NativeProcessCaptured {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [hashtable]$Environment
    )

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName = $FilePath
    $psi.Arguments = (($Arguments | ForEach-Object { ConvertTo-ProcessArgument $_ }) -join ' ')
    $psi.WorkingDirectory = $WorkingDirectory
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    if ($psi.PSObject.Properties.Name -contains 'StandardOutputEncoding') {
        $psi.StandardOutputEncoding = [System.Text.UTF8Encoding]::new($false)
        $psi.StandardErrorEncoding = [System.Text.UTF8Encoding]::new($false)
    }
    foreach ($key in $Environment.Keys) {
        $psi.EnvironmentVariables[$key] = [string]$Environment[$key]
    }

    $proc = [System.Diagnostics.Process]::new()
    $proc.StartInfo = $psi
    [void]$proc.Start()
    $stdout = $proc.StandardOutput.ReadToEnd()
    $stderr = $proc.StandardError.ReadToEnd()
    $proc.WaitForExit()

    return [pscustomobject]@{
        ExitCode = $proc.ExitCode
        StdOut = $stdout
        StdErr = $stderr
    }
}

function Join-CommandForLog {
    param([object]$Command)
    return (@($Command.FilePath) + @($Command.PrefixArgs)) -join ' '
}

function Test-PythonCommand {
    param(
        [string]$FilePath,
        [string[]]$PrefixArgs = @(),
        [switch]$RequirePython310,
        [string[]]$RequiredModules = @()
    )
    try {
        $moduleCheck = ''
        foreach ($module in $RequiredModules) {
            $moduleCheck += "import $module; "
        }
        $code = $moduleCheck + "import sys; print(sys.version_info.major); print(sys.version_info.minor)"
        $result = Invoke-NativeProcessCaptured `
            -FilePath $FilePath `
            -Arguments (@($PrefixArgs) + @('-c', $code)) `
            -WorkingDirectory $PSScriptRoot `
            -Environment @{
                PYTHONUTF8 = '1'
                PYTHONIOENCODING = 'utf-8'
            }
        if ($result.ExitCode -ne 0) { return $false }
        $lines = @($result.StdOut -split "`r?`n" | Where-Object { $_ -ne '' })
        if ($RequirePython310) {
            return ($lines.Count -ge 2 -and $lines[0] -eq '3' -and $lines[1] -eq '10')
        }
        return $true
    } catch {
        return $false
    }
}

function Resolve-PythonCommand {
    param(
        [string]$ConfiguredPath,
        [string]$Purpose,
        [switch]$RequirePython310,
        [string[]]$RequiredModules = @()
    )

    $candidates = New-Object System.Collections.Generic.List[object]
    if ($ConfiguredPath) {
        $candidates.Add([pscustomobject]@{ FilePath = $ConfiguredPath; PrefixArgs = @(); Label = '设置路径' })
    }
    $candidates.Add([pscustomobject]@{ FilePath = 'py'; PrefixArgs = @('-3.10'); Label = 'py -3.10' })

    $commonPaths = @()
    if ($env:LOCALAPPDATA) { $commonPaths += (Join-Path $env:LOCALAPPDATA 'Programs\Python\Python310\python.exe') }
    if ($env:ProgramFiles) { $commonPaths += (Join-Path $env:ProgramFiles 'Python310\python.exe') }
    if (${env:ProgramFiles(x86)}) { $commonPaths += (Join-Path ${env:ProgramFiles(x86)} 'Python310\python.exe') }
    foreach ($path in $commonPaths) {
        if ($path -and (Test-Path -LiteralPath $path)) {
            $candidates.Add([pscustomobject]@{ FilePath = $path; PrefixArgs = @(); Label = '常见安装路径' })
        }
    }

    $pathPythons = @(Get-Command python -ErrorAction SilentlyContinue | ForEach-Object { $_.Source } | Select-Object -Unique)
    foreach ($path in $pathPythons) {
        $candidates.Add([pscustomobject]@{ FilePath = $path; PrefixArgs = @(); Label = 'PATH' })
    }

    foreach ($candidate in $candidates) {
        if (Test-PythonCommand -FilePath $candidate.FilePath -PrefixArgs $candidate.PrefixArgs -RequirePython310:$RequirePython310 -RequiredModules $RequiredModules) {
            Write-Log "$Purpose 使用 Python：$(Join-CommandForLog $candidate) [$($candidate.Label)]"
            return $candidate
        }
    }

    $need = if ($RequirePython310) { 'Python 3.10' } else { '可用 Python' }
    $modules = if ($RequiredModules.Count -gt 0) { '，并安装模块：' + ($RequiredModules -join ', ') } else { '' }
    throw "$Purpose 找不到$need$modules。请安装 Python 3.10，或在 ui_settings.json 中设置 kfx_python_path / extract_python_path。"
}

function Run-KfxGen {
    param([string]$CbzPath, [string]$BaseName)
    $kfxPath = Join-Path $KfxOutputRoot ($BaseName + '.kfx')
    if (Test-Path -LiteralPath $kfxPath) {
        Write-Log "删除同名旧 KFX：$kfxPath"
        Remove-Item -LiteralPath $kfxPath -Force
    }

    $started = Get-Date
    Write-Log "kckfxgen 输入 CBZ：$CbzPath"
    Write-Log "kckfxgen 输出目录：$KfxOutputRoot"
    $meta = Get-BookMetadataFromName -BaseName $BaseName
    Write-Log "KFX 元数据：作品名=[$($meta.Title)] 作者=[$($meta.Author)] 出版社=[]"
    $oldPythonPath = $env:PYTHONPATH
    $oldPythonUtf8 = $env:PYTHONUTF8
    $oldPythonIoEncoding = $env:PYTHONIOENCODING
    try {
        $env:PYTHONPATH = $KfxCliRuntime
        $env:PYTHONUTF8 = '1'
        $env:PYTHONIOENCODING = 'utf-8'
        Push-Location $KfxCliRuntime
        try {
            $kfxArgs = @(
                $KfxCliScript,
                '--output', $KfxOutputRoot,
                '--page-progression', 'rtl',
                '--layout-view', 'virtual',
                '--virtual-panel-axis', 'vertical',
                '--title', $meta.Title
            )
            if ($meta.Author -ne '') {
                $kfxArgs += @('--author', $meta.Author)
            }
            $kfxArgs += $CbzPath
            Write-Log "kckfxgen 参数：$($kfxArgs -join ' | ')"
            $kfxResult = Invoke-NativeProcessCaptured `
                -FilePath $script:KfxPython.FilePath `
                -Arguments (@($script:KfxPython.PrefixArgs) + $kfxArgs) `
                -WorkingDirectory $KfxCliRuntime `
                -Environment @{
                    PYTHONPATH = $KfxCliRuntime
                    PYTHONUTF8 = '1'
                    PYTHONIOENCODING = 'utf-8'
                }
            $kfxOutput = @()
            if ($kfxResult.StdOut) {
                $kfxOutput += ($kfxResult.StdOut -split "`r?`n")
            }
            if ($kfxResult.StdErr) {
                $kfxOutput += ($kfxResult.StdErr -split "`r?`n")
            }
            $kfxExitCode = $kfxResult.ExitCode
        } finally {
            Pop-Location
        }
        foreach ($line in @($kfxOutput)) {
            if ($line) {
                Write-Log "kckfxgen：$line"
            }
        }
        if ($kfxExitCode -ne 0) {
            throw "kckfxgen CLI 转换失败，退出码：$kfxExitCode"
        }
    } finally {
        $env:PYTHONPATH = $oldPythonPath
        $env:PYTHONUTF8 = $oldPythonUtf8
        $env:PYTHONIOENCODING = $oldPythonIoEncoding
    }

    if (-not (Test-Path -LiteralPath $kfxPath)) {
        $generated = Get-ChildItem -LiteralPath $KfxOutputRoot -Filter '*.kfx' -File |
            Where-Object { $_.LastWriteTime -ge $started } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if (-not $generated) {
            throw "kckfxgen 已结束，但没有找到本次新生成的 KFX 文件。"
        }
        Write-Log "重命名 KFX 为拖入文件同名：$($generated.FullName) -> $kfxPath"
        Move-Item -LiteralPath $generated.FullName -Destination $kfxPath -Force
    }

    Wait-FileStable -Path $kfxPath -NotOlderThan $started -TimeoutMinutes $KfxTimeoutMinutes -Label "$BaseName KFX" | Out-Null
    Write-Log "KFX 输出完成：$kfxPath"
    return $kfxPath
}

function Run-KfxGenBatch {
    param([object[]]$Items)
    if ($Items.Count -eq 0) { return @() }

    Write-Log "kckfxgen 分批转换 $($Items.Count) 个 CBZ。"
    $index = 0
    foreach ($item in $Items) {
        $index += 1
        $started = Get-Date
        $meta = Get-BookMetadataFromName -BaseName $item.BaseName
        $item.KfxPath = Join-Path $KfxOutputRoot ($item.BaseName + '.kfx')
        Write-Log "KFX 任务 [$index/$($Items.Count)]：$($item.CbzPath)"
        Write-Log "KFX 元数据：作品名=[$($meta.Title)] 作者=[$($meta.Author)] 出版社=[]"
        if (Test-Path -LiteralPath $item.KfxPath) {
            Write-Log "删除同名旧 KFX：$($item.KfxPath)"
            Remove-Item -LiteralPath $item.KfxPath -Force
        }

        $kfxArgs = @(
            $KfxCliScript,
            '--output', $KfxOutputRoot,
            '--page-progression', 'rtl',
            '--layout-view', 'virtual',
            '--virtual-panel-axis', 'vertical',
            '--title', $meta.Title
        )
        if ($meta.Author -ne '') {
            $kfxArgs += @('--author', $meta.Author)
        }
        $kfxArgs += $item.CbzPath
        Write-Log "kckfxgen 参数 [$index/$($Items.Count)]：$($kfxArgs -join ' | ')"
        $kfxResult = Invoke-NativeProcessCaptured `
            -FilePath $script:KfxPython.FilePath `
            -Arguments (@($script:KfxPython.PrefixArgs) + $kfxArgs) `
            -WorkingDirectory $KfxCliRuntime `
            -Environment @{
                PYTHONPATH = "$KfxCliRuntime;$PSScriptRoot"
                PYTHONUTF8 = '1'
                PYTHONIOENCODING = 'utf-8'
            }

        $kfxOutput = @()
        if ($kfxResult.StdOut) {
            $kfxOutput += ($kfxResult.StdOut -split "`r?`n")
        }
        if ($kfxResult.StdErr) {
            $kfxOutput += ($kfxResult.StdErr -split "`r?`n")
        }
        foreach ($line in @($kfxOutput)) {
            if ($line) {
                Write-Log "kckfxgen：$line"
            }
        }
        if ($kfxResult.ExitCode -ne 0) {
            throw "kckfxgen 转换失败 [$index/$($Items.Count)]，退出码：$($kfxResult.ExitCode)"
        }

        if (-not (Test-Path -LiteralPath $item.KfxPath)) {
            $generated = Get-ChildItem -LiteralPath $KfxOutputRoot -Filter '*.kfx' -File |
                Where-Object { $_.LastWriteTime -ge $started } |
                Sort-Object LastWriteTime -Descending |
                Select-Object -First 1
            if (-not $generated) {
                throw "kckfxgen 已结束，但没有找到本次新生成的 KFX 文件：$($item.BaseName)"
            }
            Write-Log "重命名 KFX 为拖入文件同名：$($generated.FullName) -> $($item.KfxPath)"
            Move-Item -LiteralPath $generated.FullName -Destination $item.KfxPath -Force
        }
        Wait-FileStable -Path $item.KfxPath -NotOlderThan $started -TimeoutMinutes $KfxTimeoutMinutes -Label "$($item.BaseName) KFX" | Out-Null
        Write-Log "KFX 输出完成 [$index/$($Items.Count)]：$($item.KfxPath)"
    }
    return @($Items | ForEach-Object { $_.KfxPath })
}

function Cleanup-Intermediate {
    param([string]$ImageFolder, [string]$CbzPath, [bool]$DeleteImageFolder = $true)
    if ($DeleteImageFolder -and (Test-Path -LiteralPath $ImageFolder)) {
        Write-Log "删除中间图片文件夹：$ImageFolder"
        Remove-Item -LiteralPath $ImageFolder -Recurse -Force
    } elseif (-not $DeleteImageFolder) {
        Write-Log "保留原始图片文件夹：$ImageFolder"
    }
    if (Test-Path -LiteralPath $CbzPath) {
        Write-Log "删除中间 CBZ：$CbzPath"
        Remove-Item -LiteralPath $CbzPath -Force
    }
}

function Cleanup-OldKfxBackups {
    param([string]$BaseName)
    $pattern = $BaseName + '.old-*.kfx'
    $oldFiles = @(Get-ChildItem -LiteralPath $KfxOutputRoot -Filter $pattern -File -ErrorAction SilentlyContinue)
    foreach ($old in $oldFiles) {
        Write-Log "删除旧 KFX 备份：$($old.FullName)"
        Remove-Item -LiteralPath $old.FullName -Force
    }
}

function Resolve-InputItem {
    param([string]$Path)
    if (-not $Path -or -not (Test-Path -LiteralPath $Path)) { return $null }
    $resolved = (Resolve-Path -LiteralPath $Path).Path
    if (Test-Path -LiteralPath $resolved -PathType Container) {
        return [pscustomobject]@{
            SourcePath = $resolved
            BaseName = Split-Path $resolved -Leaf
            Kind = 'folder'
            NeedsExtract = $false
            ImageFolder = $resolved
            DeleteImageFolder = $false
        }
    }
    $ext = [IO.Path]::GetExtension($resolved).ToLowerInvariant()
    if ($ext -notin @('.mobi', '.epub', '.pdf')) { return $null }
    return [pscustomobject]@{
        SourcePath = $resolved
        BaseName = [IO.Path]::GetFileNameWithoutExtension($resolved)
        Kind = $ext.TrimStart('.')
        NeedsExtract = $true
        ImageFolder = ''
        DeleteImageFolder = $true
    }
}

Ensure-Directory $MobitoolOutputRoot '图片提取输出目录'
Ensure-Directory $KccOutputRoot 'CBZ 输出目录'
Ensure-Directory $KfxOutputRoot 'KFX 输出目录'
Assert-Path $MobiExtractScript '图片提取脚本'
Assert-Path $KccExe 'KCC'
Assert-Path $KfxGuiExe 'kckfxgen'
Assert-Path $KfxCliScript 'kckfxgen CLI'
Assert-Path $KfxCliRuntime 'kckfxgen CLI runtime'
Assert-Path $BatchKfxScript 'kckfxgen 批量包装脚本'
if ($KfxPythonPath -and -not (Test-Path -LiteralPath $KfxPythonPath)) {
    throw "设置里的 kfx_python_path 不存在：$KfxPythonPath"
}
if ($ExtractPythonPath -and -not (Test-Path -LiteralPath $ExtractPythonPath)) {
    throw "设置里的 extract_python_path 不存在：$ExtractPythonPath"
}
$script:ExtractPython = Resolve-PythonCommand -ConfiguredPath $ExtractPythonPath -Purpose '图片提取' -RequiredModules @('mobi', 'fitz')
$script:KfxPython = Resolve-PythonCommand -ConfiguredPath $KfxPythonPath -Purpose 'kckfxgen' -RequirePython310

if (-not $InputFiles -or $InputFiles.Count -eq 0) {
    $InputFiles = Select-InputFilesWithWindow
}

$inputItems = @($InputFiles | ForEach-Object { Resolve-InputItem -Path $_ } | Where-Object { $null -ne $_ })

if ($inputItems.Count -eq 0) {
    if (-not $NoMessageBox) {
        [Windows.Forms.MessageBox]::Show('没有收到 .mobi / .epub / .pdf 或图片文件夹。', '漫画转 KFX') | Out-Null
    }
    exit 1
}

Write-Log "收到 $($inputItems.Count) 个输入项目。"
Write-Log "KCC 路径：$KccExe"
Write-Log "图片输出目录：$MobitoolOutputRoot"
Write-Log "CBZ 输出目录：$KccOutputRoot"
Write-Log "KFX 输出目录：$KfxOutputRoot"
$done = New-Object System.Collections.Generic.List[string]
$items = New-Object System.Collections.Generic.List[object]

foreach ($inputItem in $inputItems) {
    $base = $inputItem.BaseName
    try {
        if ($inputItem.NeedsExtract) {
            Write-Log "开始提取 [$($inputItem.Kind)]：$base"
            $imageFolder = Run-BookImageExtract -BookPath $inputItem.SourcePath -BaseName $base
        } else {
            Write-Log "直接使用图片文件夹：$($inputItem.ImageFolder)"
            $imageFolder = $inputItem.ImageFolder
        }
        $items.Add([pscustomobject]@{
            SourcePath = $inputItem.SourcePath
            BaseName = $base
            ImageFolder = $imageFolder
            CbzPath = Join-Path $KccOutputRoot ($base + '.cbz')
            KfxPath = Join-Path $KfxOutputRoot ($base + '.kfx')
            DeleteImageFolder = $inputItem.DeleteImageFolder
        })
    } catch {
        Write-Log "失败：$base，$($_.Exception.Message)"
        if (-not $NoMessageBox) {
            [Windows.Forms.MessageBox]::Show("处理失败：$base`r`n$($_.Exception.Message)`r`n详见日志：$LogFile", '漫画转 KFX') | Out-Null
        }
        throw
    }
}

try {
    Run-KccBatch -Items $items.ToArray() | Out-Null
    $kfxPaths = Run-KfxGenBatch -Items $items.ToArray()
    foreach ($item in $items) {
        Cleanup-Intermediate -ImageFolder $item.ImageFolder -CbzPath $item.CbzPath -DeleteImageFolder $item.DeleteImageFolder
        Cleanup-OldKfxBackups -BaseName $item.BaseName
        $done.Add($item.KfxPath)
        Write-Log "完成：$($item.BaseName)"
    }
} catch {
    Write-Log "批量转换失败：$($_.Exception.Message)"
    if (-not $NoMessageBox) {
        [Windows.Forms.MessageBox]::Show("批量转换失败：$($_.Exception.Message)`r`n详见日志：$LogFile", '漫画转 KFX') | Out-Null
    }
    throw
} finally {
    Stop-ActiveChildProcesses
}

if (-not $NoMessageBox) {
    [Windows.Forms.MessageBox]::Show(("全部完成：{0} 个文件。`r`n输出目录：{1}" -f $done.Count, $KfxOutputRoot), '漫画转 KFX') | Out-Null
}













