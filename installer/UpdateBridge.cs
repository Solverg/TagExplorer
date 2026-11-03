using System;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography;
using System.Threading;
using System.Windows.Forms;

// Compatibility entry point for the updater shipped in TagExplorer 1.2.0.
internal static class UpdateBridge
{
    private const string SetupResource = "TagExplorer-Setup.exe";
    private const string CleanerResource = "BridgeCleaner.exe";

    [STAThread]
    private static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--healthcheck")
        {
            using (Stream setup = Assembly.GetExecutingAssembly().GetManifestResourceStream(SetupResource))
                return setup == null ? 1 : 0;
        }

        string stagedPath = Assembly.GetExecutingAssembly().Location;
        string temporaryDir = null;
        try
        {
            string installedExe = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
                                               "TagExplorer", "TagExplorer.exe");
#if TEST_MODE
            installedExe = Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_INSTALLED_EXE");
#endif
            string oldExe = null;
            byte[] oldHash = null;
            if (args.Length != 0)
            {
                if (args.Length != 2 || args[0] != "--apply-update")
                    throw new InvalidOperationException("Unsupported update arguments.");
                oldExe = Path.GetFullPath(args[1]);
                ValidateOldExecutable(oldExe, stagedPath, installedExe);
                if (!SamePath(oldExe, installedExe)) oldHash = HashFile(oldExe);
            }

            temporaryDir = Path.Combine(Path.GetTempPath(), "TagExplorer-setup-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(temporaryDir);
            string setupPath = Path.Combine(temporaryDir, "TagExplorer-Setup.exe");
            ExtractResource(SetupResource, setupPath);
            var start = new ProcessStartInfo(setupPath, "/TAGEXPLORER_BRIDGE=1") {
                UseShellExecute = true, Verb = "runas", WorkingDirectory = temporaryDir
            };
#if TEST_MODE
            start.UseShellExecute = false;
            start.Verb = "";
            if (Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_SILENT_SETUP") == "1")
                start.Arguments = "/TAGEXPLORER_BRIDGE=1 /SP- /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /NOICONS /TASKS= /DIR=\"" + Path.GetDirectoryName(installedExe) + "\"";
#endif
            using (Process setup = Process.Start(start))
            {
                if (setup == null) throw new InvalidOperationException("Could not start the installer.");
                setup.WaitForExit();
                if (setup.ExitCode != 0) return setup.ExitCode;
            }

            VerifyInstalledApplication(installedExe);
            var launch = CreateIndependentAppStart(installedExe, "");
#if TEST_MODE
            if (Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_SILENT_SETUP") == "1")
                launch.Arguments = "--healthcheck";
#endif
            Process launched = Process.Start(launch);
            if (launched == null) throw new InvalidOperationException("Could not start the installed application.");
#if TEST_MODE
            if (Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_SILENT_SETUP") == "1")
            {
                launched.WaitForExit(60000);
                if (!launched.HasExited || launched.ExitCode != 0) throw new IOException("Test launch failed.");
            }
#endif
            if (oldHash != null)
            {
                // Do not remove a different file that appeared at the old path during setup.
                if (!File.Exists(oldExe) || !EqualHash(oldHash, HashFile(oldExe)))
                    throw new IOException("The old executable changed during installation and was kept: " + oldExe);
                Exception failure = null;
                for (int attempt = 0; attempt < 30; attempt++)
                {
                    try { File.Delete(oldExe); failure = null; break; }
                    catch (IOException ex) { failure = ex; Thread.Sleep(500); }
                    catch (UnauthorizedAccessException ex) { failure = ex; Thread.Sleep(500); }
                }
                if (failure != null) throw new IOException("The application was installed, but the old file could not be removed: " + oldExe, failure);
            }
            return 0;
        }
        catch (Win32Exception ex)
        {
            if (ex.NativeErrorCode == 1223) return 2; // UAC cancelled.
            ShowError(ex.Message); return 1;
        }
        catch (Exception ex) { ShowError(ex.Message); return 1; }
        finally
        {
            if (temporaryDir != null)
            {
                try { Directory.Delete(temporaryDir, true); } catch (IOException) { } catch (UnauthorizedAccessException) { }
            }
            StartStagedCleanup(stagedPath);
        }
    }

    private static void ValidateOldExecutable(string oldExe, string stagedPath, string installedExe)
    {
        if (!File.Exists(oldExe) || !oldExe.EndsWith(".exe", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("The old TagExplorer executable was not found.");
        if ((File.GetAttributes(oldExe) & FileAttributes.ReparsePoint) != 0)
            throw new InvalidOperationException("The old executable cannot be a link.");
        if (SamePath(oldExe, stagedPath)) throw new InvalidOperationException("Invalid old executable path.");
        if (!SamePath(oldExe, installedExe) && !SamePath(Path.GetDirectoryName(oldExe), Path.GetDirectoryName(stagedPath)))
            throw new InvalidOperationException("The portable executable is not beside the staged update.");
        FileVersionInfo info = FileVersionInfo.GetVersionInfo(oldExe);
        if (!String.Equals(info.ProductName, "TagExplorer", StringComparison.OrdinalIgnoreCase) ||
            !String.Equals(info.CompanyName, "Solverg", StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException("The old executable is not a recognized TagExplorer build.");
    }

    private static void VerifyInstalledApplication(string path)
    {
        if (!File.Exists(path)) throw new IOException("The installed executable was not found.");
        string expectedVersion;
        using (var reader = new StreamReader(Assembly.GetExecutingAssembly().GetManifestResourceStream("version.txt")))
            expectedVersion = reader.ReadToEnd().Trim();
        FileVersionInfo info = FileVersionInfo.GetVersionInfo(path);
        if (!String.Equals(info.ProductName, "TagExplorer", StringComparison.OrdinalIgnoreCase) ||
            !String.Equals(info.ProductVersion, expectedVersion, StringComparison.OrdinalIgnoreCase))
            throw new IOException("The expected installed version could not be verified.");
        using (Process check = Process.Start(CreateIndependentAppStart(path, "--healthcheck")))
        {
            if (check == null) throw new IOException("The installed application did not start its health check.");
            if (!check.WaitForExit(60000)) { check.Kill(); throw new IOException("The installed application health check timed out."); }
            if (check.ExitCode != 0) throw new IOException("The installed application health check failed.");
        }
    }

    private static ProcessStartInfo CreateIndependentAppStart(string path, string arguments)
    {
        // The bridge can inherit PyInstaller state from the old one-file app.
        // When the installed path is unchanged, the new process could reuse the
        // old extraction directory, which disappears as the old process exits.
        var start = new ProcessStartInfo(path, arguments) {
            UseShellExecute = false, CreateNoWindow = true,
            WorkingDirectory = Path.GetDirectoryName(path)
        };
        start.EnvironmentVariables["PYINSTALLER_RESET_ENVIRONMENT"] = "1";
        return start;
    }

    private static void StartStagedCleanup(string stagedPath)
    {
        if (!String.Equals(Path.GetFileName(stagedPath), "TagExplorer_new.exe", StringComparison.OrdinalIgnoreCase)) return;
        try
        {
            string dir = Path.Combine(Path.GetTempPath(), "TagExplorer-cleanup-" + Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(dir);
            string helper = Path.Combine(dir, "BridgeCleaner.exe");
            ExtractResource(CleanerResource, helper);
            string hash = BitConverter.ToString(HashFile(stagedPath)).Replace("-", "");
            Process.Start(new ProcessStartInfo(helper, Process.GetCurrentProcess().Id + " \"" + stagedPath + "\" " + hash) {
                UseShellExecute = false, CreateNoWindow = true
            });
        }
        catch (Exception) { /* A temporary staged update may remain; never remove another file. */ }
    }

    private static void ExtractResource(string name, string destination)
    {
        using (Stream input = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
        {
            if (input == null) throw new InvalidOperationException("The installer resource is missing: " + name);
            using (FileStream output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                input.CopyTo(output);
        }
    }
    private static bool SamePath(string a, string b) { return String.Equals(Path.GetFullPath(a), Path.GetFullPath(b), StringComparison.OrdinalIgnoreCase); }
    private static byte[] HashFile(string path) { using (var hash = SHA256.Create()) using (var input = File.OpenRead(path)) return hash.ComputeHash(input); }
    private static bool EqualHash(byte[] a, byte[] b) { if (a.Length != b.Length) return false; for (int i = 0; i < a.Length; i++) if (a[i] != b[i]) return false; return true; }
    private static void ShowError(string message)
    {
#if TEST_MODE
        string errorFile = Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_ERROR_FILE");
        if (!String.IsNullOrEmpty(errorFile)) File.WriteAllText(errorFile, message);
#else
        MessageBox.Show(message, "TagExplorer update", MessageBoxButtons.OK, MessageBoxIcon.Error);
#endif
    }
}
