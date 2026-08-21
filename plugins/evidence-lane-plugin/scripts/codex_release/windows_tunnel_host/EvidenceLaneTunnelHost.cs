using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Threading.Tasks;

[assembly: AssemblyTitle("Evidence Lane Tunnel Host")]
[assembly: AssemblyDescription("No-visible-console host for the Evidence Lane Windows tunnel")]
[assembly: AssemblyCompany("Evidence Lane")]
[assembly: AssemblyProduct("Evidence Lane")]
[assembly: AssemblyVersion("3.0.0.0")]
[assembly: AssemblyFileVersion("3.0.0.0")]

internal static class EvidenceLaneTunnelHost
{
    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            return Run(args);
        }
        catch
        {
            return 70;
        }
    }

    private static int Run(string[] args)
    {
        if (args == null || args.Length != 4)
        {
            return 64;
        }

        var executable = new FileInfo(Assembly.GetExecutingAssembly().Location);
        if (executable.Directory == null)
        {
            return 65;
        }

        var boot = new FileInfo(
            Path.Combine(executable.Directory.FullName, "EvidenceLaneTunnel.Boot.ps1")
        );
        if (!boot.Exists)
        {
            return 66;
        }

        var systemRoot = Environment.GetEnvironmentVariable("SystemRoot");
        if (string.IsNullOrWhiteSpace(systemRoot))
        {
            return 67;
        }

        var powershell = new FileInfo(
            Path.Combine(systemRoot, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
        );
        if (!powershell.Exists)
        {
            return 68;
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
                "-File " + Quote(boot.FullName),
                "-RuntimeRoot " + Quote(args[0]),
                "-ProfileName " + Quote(args[1]),
                "-ProfileDir " + Quote(args[2]),
                "-ReleaseToken " + Quote(args[3])
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
                return 69;
            }

            child.StandardInput.Close();
            var output = child.StandardOutput.BaseStream.CopyToAsync(Stream.Null);
            var error = child.StandardError.BaseStream.CopyToAsync(Stream.Null);
            child.WaitForExit();
            Task.WaitAll(output, error);
            return child.ExitCode;
        }
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }
}
