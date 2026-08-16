using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;

[assembly: AssemblyTitle("Evidence Lane Hook Host")]
[assembly: AssemblyDescription("No-console transport for the eight governed Evidence Lane Codex hooks")]
[assembly: AssemblyCompany("Evidence Lane")]
[assembly: AssemblyProduct("Evidence Lane")]
[assembly: AssemblyVersion("2.2.0.0")]
[assembly: AssemblyFileVersion("2.2.0.0")]

internal static class EvidenceLaneHookHost
{
    private static readonly IReadOnlyDictionary<string, string> HandlerByEvent =
        new Dictionary<string, string>(StringComparer.Ordinal)
        {
            { "SessionStart", "session_start.py" },
            { "UserPromptSubmit", "prompt_submit.py" },
            { "PreToolUse", "pre_tool_use.py" },
            { "PostToolUse", "post_tool_use.py" },
            { "PreCompact", "lifecycle_boundary.py" },
            { "PostCompact", "lifecycle_boundary.py" },
            { "Stop", "stop_response.py" },
            { "SessionEnd", "lifecycle_boundary.py" },
        };

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            return Run(args);
        }
        catch
        {
            // The PowerShell launcher owns the event-specific fail-closed JSON.
            // This transport must never print internal exception details.
            return 70;
        }
    }

    private static int Run(string[] args)
    {
        if (args == null || args.Length != 2)
        {
            return 64;
        }

        string expectedHandler;
        if (!HandlerByEvent.TryGetValue(args[0], out expectedHandler) ||
            !string.Equals(args[1], expectedHandler, StringComparison.Ordinal))
        {
            return 65;
        }

        var executable = new FileInfo(Assembly.GetExecutingAssembly().Location);
        if (executable.Directory == null)
        {
            return 66;
        }

        var script = new FileInfo(Path.Combine(executable.Directory.FullName, "invoke_hook.ps1"));
        if (!script.Exists)
        {
            return 67;
        }

        var systemRoot = Environment.GetEnvironmentVariable("SystemRoot");
        if (string.IsNullOrWhiteSpace(systemRoot))
        {
            return 68;
        }

        var powershell = new FileInfo(
            Path.Combine(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        );
        if (!powershell.Exists)
        {
            return 69;
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = powershell.FullName,
            Arguments = string.Join(
                " ",
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy Bypass",
                "-WindowStyle Hidden",
                "-File " + Quote(script.FullName),
                Quote(args[0]),
                Quote(args[1])
            ),
            UseShellExecute = false,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
        };

        using (var child = new Process { StartInfo = startInfo })
        {
            if (!child.Start())
            {
                return 71;
            }

            var input = CopyInputAndCloseAsync(
                Console.OpenStandardInput(),
                child.StandardInput.BaseStream
            );
            var output = child.StandardOutput.BaseStream.CopyToAsync(Console.OpenStandardOutput());
            var error = child.StandardError.BaseStream.CopyToAsync(Console.OpenStandardError());

            child.WaitForExit();
            Task.WaitAll(input, output, error);
            return child.ExitCode;
        }
    }

    private static async Task CopyInputAndCloseAsync(Stream source, Stream destination)
    {
        try
        {
            await source.CopyToAsync(destination).ConfigureAwait(false);
        }
        finally
        {
            destination.Close();
        }
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }
}
