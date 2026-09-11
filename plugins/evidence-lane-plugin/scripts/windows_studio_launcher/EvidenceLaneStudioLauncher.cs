using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Text;

[assembly: AssemblyTitle("Evidence Lane Studio Launcher")]
[assembly: AssemblyDescription("Stable-root launcher for Evidence Lane Studio and its persistent engine")]
[assembly: AssemblyCompany("Evidence Lane")]
[assembly: AssemblyProduct("Evidence Lane")]
[assembly: AssemblyVersion("4.0.2.0")]
[assembly: AssemblyFileVersion("4.0.2.0")]

internal static class EvidenceLaneStudioLauncher
{
    private const int InvalidArguments = 64;
    private const int InvalidLayout = 65;
    private const int LinkedLayout = 66;
    private const int LaunchFailed = 67;

    [STAThread]
    private static int Main(string[] args)
    {
        try
        {
            return Run(args ?? new string[0]);
        }
        catch (IOException)
        {
            return InvalidLayout;
        }
        catch (UnauthorizedAccessException)
        {
            return InvalidLayout;
        }
        catch (InvalidOperationException)
        {
            return LaunchFailed;
        }
    }

    private static int Run(string[] args)
    {
        if (args.Length != 1 ||
            (args[0] != "--open" && args[0] != "--startup" && args[0] != "--inspect"))
        {
            return InvalidArguments;
        }

        var executable = new FileInfo(Assembly.GetExecutingAssembly().Location);
        var app = executable.Directory;
        var installation = app == null ? null : app.Parent;
        if (app == null || installation == null)
        {
            return InvalidLayout;
        }

        var plugin = new DirectoryInfo(Path.Combine(installation.FullName, "plugin"));
        var runtime = new DirectoryInfo(Path.Combine(installation.FullName, "engine"));
        var python = new FileInfo(Path.Combine(runtime.FullName, "venv", "Scripts", "pythonw.exe"));
        var script = new FileInfo(Path.Combine(plugin.FullName, "scripts", "launch_studio.py"));
        var pointer = new FileInfo(Path.Combine(installation.FullName, "installation.json"));
        if (!plugin.Exists || !runtime.Exists || !python.Exists || !script.Exists || !pointer.Exists)
        {
            return InvalidLayout;
        }
        foreach (var path in new[]
        {
            executable.FullName,
            app.FullName,
            installation.FullName,
            plugin.FullName,
            runtime.FullName,
            python.FullName,
            script.FullName,
            pointer.FullName,
        })
        {
            if (ContainsReparsePoint(path, installation.FullName))
            {
                return LinkedLayout;
            }
        }

        if (args[0] == "--inspect")
        {
            var diagnostics = Path.Combine(installation.FullName, "diagnostics");
            Directory.CreateDirectory(diagnostics);
            if (ContainsReparsePoint(diagnostics, installation.FullName))
            {
                return LinkedLayout;
            }
            var inspection = Path.Combine(diagnostics, "launcher-inspection.json");
            var json = "{\"status\":\"PASS\",\"installation_root\":" + Json(installation.FullName) +
                ",\"release_root\":" + Json(installation.FullName) +
                ",\"plugin_root\":" + Json(plugin.FullName) +
                ",\"runtime_root\":" + Json(runtime.FullName) +
                ",\"command_length\":" + BuildArguments(script.FullName, runtime.FullName).Length +
                ",\"project_state_changed\":false}\n";
            if (File.Exists(inspection))
            {
                return InvalidLayout;
            }
            using (var stream = new FileStream(
                inspection,
                FileMode.CreateNew,
                FileAccess.Write,
                FileShare.None
            ))
            using (var writer = new StreamWriter(stream, new UTF8Encoding(false)))
            {
                writer.Write(json);
            }
            return 0;
        }

        var start = new ProcessStartInfo
        {
            FileName = python.FullName,
            Arguments = BuildArguments(script.FullName, runtime.FullName),
            WorkingDirectory = runtime.FullName,
            UseShellExecute = false,
            CreateNoWindow = true,
            WindowStyle = ProcessWindowStyle.Hidden,
        };
        start.EnvironmentVariables["EVIDENCE_LANE_STUDIO_ROOT"] = installation.FullName;
        start.EnvironmentVariables["EVIDENCE_LANE_RUNTIME_ROOT"] = runtime.FullName;
        start.EnvironmentVariables["EVIDENCE_LANE_PLUGIN_ROOT"] = plugin.FullName;
        var nodeRoot = Path.Combine(installation.FullName, "toolchains", "node");
        var nodeCommands = Path.Combine(nodeRoot, "node_modules", ".bin");
        var gitCommands = Path.Combine(installation.FullName, "toolchains", "bin", "git", "cmd");
        if (!Directory.Exists(nodeRoot) || !Directory.Exists(nodeCommands) ||
            !Directory.Exists(gitCommands) || ContainsReparsePoint(nodeRoot, installation.FullName) ||
            ContainsReparsePoint(nodeCommands, installation.FullName) ||
            ContainsReparsePoint(gitCommands, installation.FullName))
        {
            return InvalidLayout;
        }
        var existingPath = start.EnvironmentVariables["PATH"] ?? "";
        start.EnvironmentVariables["PATH"] = string.Join(
            Path.PathSeparator.ToString(), nodeRoot, nodeCommands, gitCommands, existingPath
        );
        start.EnvironmentVariables["EVIDENCE_LANE_NODE_ROOT"] = nodeRoot;
        start.EnvironmentVariables["EVIDENCE_LANE_NATIVE_ROOT"] = Path.Combine(
            installation.FullName, "toolchains", "bin"
        );
        start.EnvironmentVariables["EVIDENCE_LANE_MODEL_ROOT"] = Path.Combine(
            installation.FullName, "toolchains", "models"
        );
        start.EnvironmentVariables["EVIDENCE_LANE_PROVIDER_ROOT"] = Path.Combine(
            installation.FullName, "toolchains", "python", "providers"
        );
        using (var child = Process.Start(start))
        {
            if (child == null)
            {
                return LaunchFailed;
            }
        }
        return 0;
    }

    private static string BuildArguments(string script, string runtime)
    {
        return "-I -B " + Quote(script) + " --runtime-root " + Quote(runtime);
    }

    private static bool ContainsReparsePoint(string selectedPath, string boundaryPath)
    {
        var current = Path.GetFullPath(selectedPath).TrimEnd(Path.DirectorySeparatorChar);
        var boundary = Path.GetFullPath(boundaryPath).TrimEnd(Path.DirectorySeparatorChar);
        while (true)
        {
            if ((File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0)
            {
                return true;
            }
            if (string.Equals(current, boundary, StringComparison.OrdinalIgnoreCase))
            {
                return false;
            }
            var parent = Directory.GetParent(current);
            if (parent == null || current.Length <= boundary.Length)
            {
                return true;
            }
            current = parent.FullName.TrimEnd(Path.DirectorySeparatorChar);
        }
    }

    private static string Quote(string value)
    {
        var result = new StringBuilder("\"");
        var backslashes = 0;
        foreach (var character in value)
        {
            if (character == '\\')
            {
                backslashes += 1;
            }
            else if (character == '\"')
            {
                result.Append('\\', backslashes * 2 + 1);
                result.Append('\"');
                backslashes = 0;
            }
            else
            {
                result.Append('\\', backslashes);
                result.Append(character);
                backslashes = 0;
            }
        }
        result.Append('\\', backslashes * 2);
        result.Append('\"');
        return result.ToString();
    }

    private static string Json(string value)
    {
        return "\"" + value.Replace("\\", "\\\\").Replace("\"", "\\\"") + "\"";
    }
}
