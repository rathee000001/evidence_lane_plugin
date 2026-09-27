using System;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using Microsoft.Win32;

[assembly: AssemblyTitle("Evidence Lane Studio")]
[assembly: AssemblyDescription("Windows application shell for Evidence Lane Studio")]
[assembly: AssemblyCompany("Evidence Lane")]
[assembly: AssemblyProduct("Evidence Lane Studio")]
[assembly: AssemblyCopyright("Copyright © 2026 Praveen Rathee")]
[assembly: AssemblyVersion("4.0.10.0")]
[assembly: AssemblyFileVersion("4.0.10.0")]

internal static class EvidenceLaneStudioShell
{
    private const string AppUserModelId = "EvidenceLane.Studio";
    private const string RegistryPath = @"Software\Evidence Lane\Studio";
    private const int SwMaximize = 3;
    private delegate bool EnumWindowsProc(IntPtr window, IntPtr parameter);

    [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
    private static extern int SetCurrentProcessExplicitAppUserModelID(string appId);

    [DllImport("user32.dll")]
    private static extern bool EnumWindows(EnumWindowsProc callback, IntPtr parameter);

    [DllImport("user32.dll")]
    private static extern bool IsWindowVisible(IntPtr window);

    [DllImport("user32.dll")]
    private static extern bool IsWindow(IntPtr window);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int GetWindowText(IntPtr window, StringBuilder title, int count);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool SetWindowText(IntPtr window, string title);

    [DllImport("user32.dll")]
    private static extern bool ShowWindow(IntPtr window, int command);

    [DllImport("user32.dll")]
    private static extern bool SetForegroundWindow(IntPtr window);

    [STAThread]
    private static int Main(string[] args)
    {
        if (args == null || args.Length != 1 || (args[0] != "--open" && args[0] != "--inspect"))
        {
            return 64;
        }
        SetCurrentProcessExplicitAppUserModelID(AppUserModelId);
        var root = RuntimeRoot();
        var launcher = Path.Combine(root, "app", "EvidenceLaneStudio.exe");
        var engine = Path.Combine(root, "engine");
        if (!Directory.Exists(root) || !Directory.Exists(engine) || !File.Exists(launcher))
        {
            return 65;
        }
        if (ContainsReparsePoint(launcher, root) || ContainsReparsePoint(engine, root))
        {
            return 66;
        }
        if (args[0] == "--inspect")
        {
            return 0;
        }
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = launcher,
                Arguments = "--open",
                WorkingDirectory = engine,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            });
        }
        catch (IOException) { return 67; }
        catch (UnauthorizedAccessException) { return 67; }
        catch (InvalidOperationException) { return 67; }

        var window = WaitForStudioWindow(TimeSpan.FromSeconds(25));
        if (window == IntPtr.Zero)
        {
            return 68;
        }
        Thread.Sleep(1200);
        for (var settle = 0; settle < 6; settle++)
        {
            var current = FindStudioWindow();
            if (current != IntPtr.Zero)
            {
                window = current;
                ShowWindow(window, SwMaximize);
                SetWindowText(window, "Evidence Lane Studio");
            }
            Thread.Sleep(250);
        }
        SetForegroundWindow(window);

        var missing = 0;
        while (missing < 6)
        {
            var current = FindStudioWindow();
            if (current == IntPtr.Zero || !IsWindow(current))
            {
                missing += 1;
            }
            else
            {
                missing = 0;
                window = current;
                SetWindowText(window, "Evidence Lane Studio");
            }
            Thread.Sleep(500);
        }
        return 0;
    }

    private static string RuntimeRoot()
    {
        var configured = Environment.GetEnvironmentVariable("EVIDENCE_LANE_STUDIO_ROOT");
        if (!string.IsNullOrWhiteSpace(configured))
        {
            return Path.GetFullPath(configured);
        }
        using (var key = Registry.CurrentUser.OpenSubKey(RegistryPath, false))
        {
            var registered = key == null ? null : key.GetValue("RuntimeRoot") as string;
            if (!string.IsNullOrWhiteSpace(registered))
            {
                return Path.GetFullPath(registered);
            }
        }
        return @"C:\Apps\EvidenceLaneStudio";
    }

    private static IntPtr WaitForStudioWindow(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            var window = FindStudioWindow();
            if (window != IntPtr.Zero)
            {
                return window;
            }
            Thread.Sleep(200);
        }
        return IntPtr.Zero;
    }

    private static IntPtr FindStudioWindow()
    {
        IntPtr found = IntPtr.Zero;
        EnumWindows((window, parameter) =>
        {
            if (!IsWindowVisible(window))
            {
                return true;
            }
            var title = new StringBuilder(512);
            GetWindowText(window, title, title.Capacity);
            if (title.ToString().IndexOf("Evidence Lane Studio", StringComparison.OrdinalIgnoreCase) < 0)
            {
                return true;
            }
            found = window;
            return false;
        }, IntPtr.Zero);
        return found;
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
}
