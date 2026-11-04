$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = [Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type @'
using System;
using System.Text;
using System.Runtime.InteropServices;
public static class CleanupNativeWindow {
  public delegate bool Callback(IntPtr h, IntPtr p);
  [DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, Callback callback, IntPtr p);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll",CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll")] public static extern bool IsChild(IntPtr parent, IntPtr child);
  [DllImport("user32.dll")] public static extern IntPtr SendMessageTimeout(IntPtr h,uint msg,IntPtr w,IntPtr l,uint flags,uint timeout,out IntPtr result);
}
'@
try {
    $spec = ([Console]::In.ReadToEnd()) | ConvertFrom-Json
    if ($spec.scope -ne 'old-only' -or -not $spec.profile_confirmed -or -not $spec.drafts_saved) { throw 'scope' }
    $root = [System.Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$spec.window_handle)
    if ($null -eq $root -or $root.Current.ProcessId -ne [int]$spec.pid) { throw 'window identity' }
    $tree = [System.Windows.Automation.TreeScope]::Descendants
    $elements = $root.FindAll($tree, [System.Windows.Automation.Condition]::TrueCondition)
    function Same-Id($element, $ids) {
        return (($element.GetRuntimeId() -join ',') -eq ($ids -join ','))
    }
    if ($spec.action -eq 'inspect') {
        $controls = @()
        foreach ($el in $elements) {
            if ($el.Current.IsPassword) { continue }
            $label = $el.Current.Name
            if ($label.Length -gt 150) { continue }
            $role = $el.Current.ControlType.ProgrammaticName
            if ($role -notmatch 'Button|MenuItem|Hyperlink' -and $label -notmatch '^(https?://)?([a-z0-9-]+\.)?claude\.ai/?$') { continue }
            $controls += @{ name=$label; role=$role; runtime_id=@($el.GetRuntimeId()); offscreen=$el.Current.IsOffscreen }
            if ($controls.Count -ge 100) { break }
        }
        if ($controls.Count -eq 0) {
            # Legacy native Button controls only. Useful when UIA providers are
            # absent/offscreen; never enumerate the global desktop or edit text.
            $legacy = [Collections.Generic.List[object]]::new()
            $callback = [CleanupNativeWindow+Callback]{
                param([IntPtr]$h,[IntPtr]$unused)
                $name=[Text.StringBuilder]::new(180)
                $class=[Text.StringBuilder]::new(100)
                [void][CleanupNativeWindow]::GetClassName($h,$class,100)
                if ($class.ToString() -match '^Button$|^WindowsForms10\.BUTTON\.') {
                    [void][CleanupNativeWindow]::GetWindowText($h,$name,180)
                    $legacy.Add(@{name=$name.ToString();role='Native.Button';runtime_id=@(-987654,$h.ToInt64());offscreen=$true})
                }
                return ($legacy.Count -lt 100)
            }
            [void][CleanupNativeWindow]::EnumChildWindows([IntPtr][long]$spec.window_handle,$callback,[IntPtr]::Zero)
            $controls=@($legacy.ToArray())
        }
        @{ status='inspected'; pid=[int]$spec.pid; window_handle=[long]$spec.window_handle; controls=$controls } | ConvertTo-Json -Depth 8 -Compress
        exit 0
    }
    if ($spec.runtime_id.Count -eq 2 -and $spec.runtime_id[0] -eq -987654) {
        if ($spec.action -eq 'remove-site') { throw 'web site deletion requires exact UIA container' }
        $h=[IntPtr][long]$spec.runtime_id[1]
        $owner=[uint32]0
        [void][CleanupNativeWindow]::GetWindowThreadProcessId($h,[ref]$owner)
        $label=[Text.StringBuilder]::new(180)
        $class=[Text.StringBuilder]::new(100)
        [void][CleanupNativeWindow]::GetWindowText($h,$label,180)
        [void][CleanupNativeWindow]::GetClassName($h,$class,100)
        $known = switch ($spec.action) {
            'open-menu' { @('Settings','Account','Menu','设置','账户','账号','菜单') }
            'logout' { @('Log out','Logout','Sign out','退出登录','退出账号','登出') }
            'confirm-logout' { @('Log out','Logout','Sign out','退出登录','退出账号','登出') }
            default { @() }
        }
        if ($owner -ne [int]$spec.pid -or -not [CleanupNativeWindow]::IsChild([IntPtr][long]$spec.window_handle,$h) -or $class.ToString() -notmatch '^Button$|^WindowsForms10\.BUTTON\.' -or $label.ToString() -cne $spec.control_name -or $known -cnotcontains $label.ToString()) { throw 'legacy control changed' }
        $result=[IntPtr]::Zero
        $sent=[CleanupNativeWindow]::SendMessageTimeout($h,0x00F5,[IntPtr]::Zero,[IntPtr]::Zero,2,1500,[ref]$result)
        if ($sent -eq [IntPtr]::Zero) { throw 'native control timeout; inspect before retry' }
        @{status='action-invoked';backend='native-button';logout_independently_verified=$false} | ConvertTo-Json -Compress
        exit 0
    }
    $target = $null
    foreach ($el in $elements) {
        if ((Same-Id $el $spec.runtime_id) -and $el.Current.Name -ceq $spec.control_name) { $target=$el; break }
    }
    if ($null -eq $target -or $target.Current.IsPassword -or -not $target.Current.IsEnabled) { throw 'control changed' }
    $allowed = @{
        'open-menu'=@('Settings','Account','Menu','设置','账户','账号','菜单')
        'logout'=@('Log out','Logout','Sign out','退出登录','退出账号','登出')
        'confirm-logout'=@('Log out','Logout','Sign out','退出登录','退出账号','登出')
        'remove-site'=@('Delete','Remove','Clear','删除','移除','清除')
    }
    if ($allowed[$spec.action] -cnotcontains $target.Current.Name) { throw 'unsupported action' }
    if ($spec.action -eq 'remove-site') {
        if ($spec.entry_kind -ne 'browser' -or $spec.origin -ne 'https://claude.ai') { throw 'site scope' }
        $container=$null
        foreach ($el in $elements) {
            if (Same-Id $el $spec.site_container_runtime_id) { $container=$el; break }
        }
        if ($null -eq $container -or $container.Current.Name -notmatch '^(https?://)?([a-z0-9-]+\.)?claude\.ai/?$') { throw 'exact site row required' }
        $walker=[System.Windows.Automation.TreeWalker]::ControlViewWalker
        $cursor=$target
        $inside=$false
        for ($i=0;$i -lt 30 -and $null -ne $cursor;$i++) {
            if (Same-Id $cursor $spec.site_container_runtime_id) { $inside=$true; break }
            $cursor=$walker.GetParent($cursor)
        }
        if (-not $inside) { throw 'delete button outside exact site row' }
    }
    $pattern=$null
    if ($target.TryGetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern,[ref]$pattern)) {
        $pattern.Invoke()
    } elseif ($spec.action -eq 'open-menu' -and $target.TryGetCurrentPattern([System.Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$pattern)) {
        $pattern.Expand()
    } else { throw 'control is not invocable' }
    @{ status='action-invoked'; logout_independently_verified=$false } | ConvertTo-Json -Compress
    exit 0
} catch {
    @{ status='needs-agent-ui'; reason='Exact control unavailable, changed, or system access denied' } | ConvertTo-Json -Compress
    exit 2
}
