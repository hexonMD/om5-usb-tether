local Application = import "LrApplication"
local Dialogs = import "LrDialogs"
local Files = import "LrFileUtils"
local Path = import "LrPathUtils"
local Progress = import "LrProgressScope"
local Strings = import "LrStringUtils"
local Tasks = import "LrTasks"

local function commandFor(live, ticket)
    local root = Path.parent(_PLUGIN.path)
    local launcher = Path.child(root, WIN_ENV and "Launch Windows.cmd" or "Launch Mac.command")
    if Files.exists(launcher) ~= "file" then
        error("Add Jewelry Studio.lrplugin from the extracted folder beside the Studio launchers.", 0)
    end
    local mode = live and "lightroom-capture" or "lightroom-sample"
    local token = ticket:gsub(".", function(c) return string.format("%02x", c:byte()) end)
    local args = mode .. " --ticket-hex " .. token
    if not WIN_ENV then
        return "/bin/sh '" .. launcher:gsub("'", "'\\''") .. "' " .. args
    end
    -- The shell only receives an encoded command. Pass the directory to cmd via an
    -- environment variable with delayed expansion, so %, !, and & in it stay literal.
    local root64 = Strings.encodeBase64(root):gsub("%s", "")
    local script = table.concat({
        "$ErrorActionPreference='Stop'",
        "$env:JEWELRY_LR_ROOT=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" .. root64 .. "'))",
        "$p=New-Object Diagnostics.ProcessStartInfo",
        "$p.FileName=$env:ComSpec",
        [[$p.Arguments='/d /v:on /s /c "pushd "!JEWELRY_LR_ROOT!" && "Launch Windows.cmd" ]] .. args .. [["']],
        "$p.UseShellExecute=$false", "$p.CreateNoWindow=$true",
        "$child=[Diagnostics.Process]::Start($p)", "$child.WaitForExit()", "exit $child.ExitCode",
    }, "\n")
    local utf16 = script:gsub(".", function(c) return c .. "\0" end)
    return "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand " .. Strings.encodeBase64(utf16):gsub("%s", "")
end

local function start(live)
    if _G.jewelryShootActive then
        Dialogs.showBezel("A Jewelry Studio job is already running.")
        return
    end
    _G.jewelryShootActive = true
    Tasks.startAsyncTask(function()
        local ticket, progress, saved
        local ok, problem = Tasks.pcall(function()
            local folders = Dialogs.runOpenPanel {
                title = live and "Choose where to save the camera JPEG" or "Choose where to save the sample focus stack",
                prompt = "Choose folder", canChooseFiles = false, canChooseDirectories = true,
                canCreateDirectories = true, allowsMultipleSelection = false,
            }
            if not folders or not folders[1] then return end
            if live and Dialogs.confirm("Take one photo now?",
                "Release the shutter once and import the camera JPEGs into this catalog. A camera focus bracket can produce several files.",
                "Take photo", "Cancel") ~= "ok" then return end
            local catalog = Application.activeCatalog()
            local candidate = Files.chooseUniqueFileName(Path.child(Path.getStandardFilePath("temp"), "Jewelry-Studio-request"))
            if Files.exists(candidate) then error("The capture request folder is already in use.", 0) end
            local created, fresh = Files.createAllDirectories(candidate)
            if not created or fresh == false then error("Could not create a new capture request folder.", 0) end
            ticket = candidate
            local file = assert(io.open(Path.child(ticket, "output-folder.txt"), "wb"))
            local written, writeError = file:write(folders[1])
            local closed, closeError = file:close()
            assert(written and closed, writeError or closeError or "Could not write the capture request.")

            progress = Progress { title = live and "Capturing over USB" or "Processing sample focus stack" }
            progress:setCancelable(false)
            local code = Tasks.execute(commandFor(live, ticket))
            if code ~= 0 then
                local note = Path.child(ticket, "error.txt")
                local message = Files.exists(note) == "file" and Files.readFile(note)
                error(message or ("Studio exited with code " .. tostring(code) .. ". Check the setup in README.md."), 0)
            end
            local receipt = Path.child(ticket, "photo.txt")
            local photoPath = Files.exists(receipt) == "file" and Files.readFile(receipt)
            if not photoPath or not Path.isAbsolute(photoPath) or not photoPath:lower():match("%.jpe?g$")
                or Files.exists(photoPath) ~= "file" then
                error("Studio did not return a saved JPEG path. Check the chosen output folder.", 0)
            end
            saved = photoPath
            local count = tonumber(Files.readFile(Path.child(ticket, "photo-count.txt")))
            if not count or count < 1 or count ~= math.floor(count) then error("Invalid camera file count.", 0) end
            local paths = { saved }
            for index = 2, count do
                local p = Files.readFile(Path.child(ticket, "photo-" .. index .. ".txt"))
                if not p or not Path.isAbsolute(p) or Files.exists(p) ~= "file" then error("Missing capture file.", 0) end
                paths[index] = p
            end
            local photos = {}
            catalog:withWriteAccessDo("Import Jewelry Studio capture", function()
                for index, p in ipairs(paths) do
                    photos[index] = catalog:findPhotoByPath(p) or catalog:addPhoto(p)
                    if not photos[index] then error("Could not import " .. p, 0) end
                end
            end, { timeout = 15 })
            Tasks.pcall(function() catalog:setSelectedPhotos(photos[1], photos) end)
            Dialogs.showBezel(count == 1 and ("Imported " .. Path.leafName(saved)) or ("Imported " .. count .. " JPEGs"))
        end)
        if progress then Tasks.pcall(function() progress:done() end) end
        if ticket then Tasks.pcall(function() Files.delete(ticket) end) end
        _G.jewelryShootActive = false
        if not ok then
            local detail = tostring(problem)
            if saved then detail = detail .. "\n\nSaved photo\n" .. saved end
            Dialogs.message("Jewelry Studio", detail, "critical")
        end
    end)
end

return { start = start }
