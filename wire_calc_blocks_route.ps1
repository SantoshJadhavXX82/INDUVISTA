# =============================================================================
# Phase 16.0b -- Wire the calc-blocks route into App.tsx.
#
# Adds two things to D:\INDUVISTA\frontend\src\App.tsx:
#   1. import CalcDefinitionsAdmin from "@/pages/CalcDefinitionsAdmin";
#   2. <Route path="/global/calc-blocks" element={<CalcDefinitionsAdmin />} />
#
# Idempotent: re-runs are no-ops. One-time backup at App.tsx.bak_phase16_0b.
# If App.tsx uses a non-standard pattern (object-based router config,
# named imports, lazy loading), the script reports what to add by hand.
# =============================================================================

$ErrorActionPreference = 'Stop'

$appPath = "D:\INDUVISTA\frontend\src\App.tsx"

if (-not (Test-Path $appPath)) {
    throw "App.tsx not found at $appPath. Adjust the path if your file lives elsewhere."
}

# Backup once.
$bak = "$appPath.bak_phase16_0b"
if (-not (Test-Path $bak)) {
    Copy-Item $appPath $bak
    Write-Host "Backup: $bak" -ForegroundColor Gray
}

$content = Get-Content $appPath -Raw
$originalContent = $content
$changed = $false


# ----------------------------------------------------------------------------
# 1. Add import if missing
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "=== App.tsx import ===" -ForegroundColor Cyan

if ($content -match "CalcDefinitionsAdmin") {
    Write-Host "  CalcDefinitionsAdmin already imported. Skipping." -ForegroundColor Yellow
} else {
    # Look for the last `import X from "@/pages/..."` line, common pattern.
    $pageImports = [regex]::Matches(
        $content, "(?m)^import\s+\w+\s+from\s+[`"']@/pages/[^`"']+[`"'];?\s*\r?\n")

    if ($pageImports.Count -gt 0) {
        $last = $pageImports[$pageImports.Count - 1]
        $insertAt = $last.Index + $last.Length
        $newImport = "import CalcDefinitionsAdmin from `"@/pages/CalcDefinitionsAdmin`";`r`n"
        $content = $content.Substring(0, $insertAt) + $newImport + $content.Substring($insertAt)
        $changed = $true
        Write-Host "  Inserted import after last @/pages import" -ForegroundColor Green
    } else {
        # Fallback: try `from "./pages/..."` (relative path).
        $pageImportsRel = [regex]::Matches(
            $content, "(?m)^import\s+\w+\s+from\s+[`"']\./pages/[^`"']+[`"'];?\s*\r?\n")
        if ($pageImportsRel.Count -gt 0) {
            $last = $pageImportsRel[$pageImportsRel.Count - 1]
            $insertAt = $last.Index + $last.Length
            $newImport = "import CalcDefinitionsAdmin from `"./pages/CalcDefinitionsAdmin`";`r`n"
            $content = $content.Substring(0, $insertAt) + $newImport + $content.Substring($insertAt)
            $changed = $true
            Write-Host "  Inserted import after last ./pages import" -ForegroundColor Green
        } else {
            Write-Host "  ERROR: could not find an existing @/pages or ./pages import" -ForegroundColor Red
            Write-Host "  Add this import line manually near the top of App.tsx:" -ForegroundColor Yellow
            Write-Host "      import CalcDefinitionsAdmin from `"@/pages/CalcDefinitionsAdmin`";" -ForegroundColor Yellow
        }
    }
}


# ----------------------------------------------------------------------------
# 2. Add Route element if missing
# ----------------------------------------------------------------------------

Write-Host ""
Write-Host "=== App.tsx route ===" -ForegroundColor Cyan

if ($content -match 'path="/global/calc-blocks"') {
    Write-Host "  Route '/global/calc-blocks' already present. Skipping." -ForegroundColor Yellow
} else {
    # Pattern: <Route path="..." element={...} />  (self-closing, single line)
    $routes = [regex]::Matches(
        $content, "(?m)^(?<indent>\s*)<Route\s+path=[`"'][^`"']+[`"'][^>]*/>\s*\r?\n")

    if ($routes.Count -gt 0) {
        $last = $routes[$routes.Count - 1]
        $indent = $last.Groups['indent'].Value
        $insertAt = $last.Index + $last.Length
        $newRoute = "${indent}<Route path=`"/global/calc-blocks`" element={<CalcDefinitionsAdmin />} />`r`n"
        $content = $content.Substring(0, $insertAt) + $newRoute + $content.Substring($insertAt)
        $changed = $true
        Write-Host "  Inserted <Route /> after the last existing Route" -ForegroundColor Green
    } else {
        # Fallback: try `<Route path="..." element={...}>` followed by `</Route>`.
        $routesBlock = [regex]::Matches(
            $content, "(?m)^(?<indent>\s*)<Route\s+path=[`"'][^`"']+[`"'][^>]*>\s*\r?\n")
        if ($routesBlock.Count -gt 0) {
            $last = $routesBlock[$routesBlock.Count - 1]
            # Insert BEFORE the matched line at same indent (sibling, not child).
            $indent = $last.Groups['indent'].Value
            $insertAt = $last.Index
            $newRoute = "${indent}<Route path=`"/global/calc-blocks`" element={<CalcDefinitionsAdmin />} />`r`n"
            $content = $content.Substring(0, $insertAt) + $newRoute + $content.Substring($insertAt)
            $changed = $true
            Write-Host "  Inserted <Route /> as sibling of an existing Route block" -ForegroundColor Green
        } else {
            Write-Host "  ERROR: could not find any <Route path=... element=... /> pattern" -ForegroundColor Red
            Write-Host "  Add this manually inside <Routes>...</Routes>:" -ForegroundColor Yellow
            Write-Host "      <Route path=`"/global/calc-blocks`" element={<CalcDefinitionsAdmin />} />" -ForegroundColor Yellow
        }
    }
}


# ----------------------------------------------------------------------------
# Write back + verify
# ----------------------------------------------------------------------------

if ($changed) {
    Set-Content -Path $appPath -Value $content -NoNewline
}

Write-Host ""
Write-Host "=== Verification ===" -ForegroundColor Cyan

$final = Get-Content $appPath -Raw

$importOk = $final -match "import\s+CalcDefinitionsAdmin"
$routeOk  = $final -match 'path="/global/calc-blocks"'

if ($importOk) {
    Write-Host "  [OK]   import CalcDefinitionsAdmin" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] import CalcDefinitionsAdmin" -ForegroundColor Red
}
if ($routeOk) {
    Write-Host "  [OK]   <Route path='/global/calc-blocks'>" -ForegroundColor Green
} else {
    Write-Host "  [FAIL] <Route path='/global/calc-blocks'>" -ForegroundColor Red
}

Write-Host ""
if ($importOk -and $routeOk) {
    Write-Host "Route wired. Vite should hot-reload automatically." -ForegroundColor Green
    Write-Host "Refresh your browser at http://localhost:5174/global/calc-blocks" -ForegroundColor Gray
} else {
    Write-Host "Some edits need manual application - see messages above." -ForegroundColor Red
    Write-Host "Paste the current contents of App.tsx and I'll write a precise patch:" -ForegroundColor Gray
    Write-Host "    Get-Content D:\INDUVISTA\frontend\src\App.tsx" -ForegroundColor Gray
    exit 1
}
