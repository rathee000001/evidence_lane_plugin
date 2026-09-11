@echo off
setlocal DisableDelayedExpansion
set "EVIDENCE_LANE_MCP_SCRIPT=%~dp0..\mcp\server.mjs"

if defined LOCALAPPDATA for /d %%D in ("%LOCALAPPDATA%\OpenAI\Codex\runtimes\cua_node\*") do if exist "%%~fD\bin\node.exe" ("%%~fD\bin\node.exe" "%EVIDENCE_LANE_MCP_SCRIPT%" %* & exit /b)
if defined XDG_CACHE_HOME if exist "%XDG_CACHE_HOME%\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" ("%XDG_CACHE_HOME%\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" "%EVIDENCE_LANE_MCP_SCRIPT%" %* & exit /b)
if defined USERPROFILE if exist "%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" ("%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe" "%EVIDENCE_LANE_MCP_SCRIPT%" %* & exit /b)
if defined CODEX_MCP_NODE_PATH if exist "%CODEX_MCP_NODE_PATH%" ("%CODEX_MCP_NODE_PATH%" "%EVIDENCE_LANE_MCP_SCRIPT%" %* & exit /b)
if defined CODEX_BROWSER_USE_NODE_PATH if exist "%CODEX_BROWSER_USE_NODE_PATH%" ("%CODEX_BROWSER_USE_NODE_PATH%" "%EVIDENCE_LANE_MCP_SCRIPT%" %* & exit /b)
if defined CODEX_ELECTRON_RESOURCES_PATH if exist "%CODEX_ELECTRON_RESOURCES_PATH%\cua_node\bin\node.exe" ("%CODEX_ELECTRON_RESOURCES_PATH%\cua_node\bin\node.exe" "%EVIDENCE_LANE_MCP_SCRIPT%" %* & exit /b)
where node >nul 2>&1
if not errorlevel 1 (node "%EVIDENCE_LANE_MCP_SCRIPT%" %* & exit /b)
echo Evidence Lane could not find a Node runtime. Reinstall or update Codex, or set CODEX_MCP_NODE_PATH. 1>&2
exit /b 127
