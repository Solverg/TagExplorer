using System;
using System.Diagnostics;
using System.IO;
using System.Security.Cryptography;
using System.Threading;

internal static class BridgeCleaner
{
    private static int Main(string[] args)
    {
        try
        {
            int pid;
            if (args.Length != 3 || !Int32.TryParse(args[0], out pid) ||
                !String.Equals(Path.GetFileName(args[1]), "TagExplorer_new.exe", StringComparison.OrdinalIgnoreCase)) return 1;
            try { using (Process parent = Process.GetProcessById(pid)) parent.WaitForExit(60000); }
            catch (ArgumentException) { }
            string path = Path.GetFullPath(args[1]);
            for (int attempt = 0; attempt < 30; attempt++)
            {
                if (!File.Exists(path)) return 0;
                if ((File.GetAttributes(path) & FileAttributes.ReparsePoint) != 0) return 1;
                string hash;
                using (var algorithm = SHA256.Create()) using (var input = File.OpenRead(path))
                    hash = BitConverter.ToString(algorithm.ComputeHash(input)).Replace("-", "");
                if (!String.Equals(hash, args[2], StringComparison.OrdinalIgnoreCase)) return 1;
                try { File.Delete(path); return 0; }
                catch (IOException) { Thread.Sleep(500); }
                catch (UnauthorizedAccessException) { Thread.Sleep(500); }
            }
        }
        catch (Exception) { }
        return 1;
    }
}
