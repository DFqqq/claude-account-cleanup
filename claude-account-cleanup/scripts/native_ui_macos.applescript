on run argv
    set wantedPid to (item 1 of argv) as integer
    set wantedAction to item 2 of argv
    set wantedName to item 3 of argv
    tell application "System Events"
        set matchingProcesses to application processes whose unix id is wantedPid
        if (count of matchingProcesses) is not 1 then error "Exact process unavailable"
        set appProcess to item 1 of matchingProcesses
        if (count of windows of appProcess) is not 1 then error "Use existing UI tools to identify the exact window"
        set appWindow to window 1 of appProcess
        set allItems to entire contents of appWindow
        if wantedAction is "inspect" then
            set report to ""
            set lineCount to 0
            repeat with elem in allItems
                try
                    set elemRole to role of elem
                    if elemRole is in {"AXButton", "AXMenuButton", "AXMenuItem"} then
                        set elemName to name of elem as text
                        if (length of elemName) < 150 then
                            set report to report & elemRole & tab & elemName & linefeed
                            set lineCount to lineCount + 1
                        end if
                    end if
                end try
                if lineCount >= 80 then exit repeat
            end repeat
            return report
        end if
        if wantedAction is not in {"open-menu", "logout", "confirm-logout"} then error "Unsupported action"
        if wantedName is not in {"Settings", "Account", "Menu", "设置", "账户", "账号", "菜单", "Log out", "Logout", "Sign out", "退出登录", "退出账号", "登出"} then error "Unsupported control"
        set targets to {}
        repeat with elem in allItems
            try
                if (role of elem) is in {"AXButton", "AXMenuButton", "AXMenuItem"} and (name of elem as text) is wantedName then set end of targets to elem
            end try
        end repeat
        if (count of targets) is not 1 then error "Exact unique control required"
        perform action "AXPress" of item 1 of targets
        return "action-invoked; independent verification required"
    end tell
end run
