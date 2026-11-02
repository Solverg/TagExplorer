using System;
using System.IO;
using System.Reflection;
[assembly: AssemblyProduct("TagExplorer")]
[assembly: AssemblyCompany("Solverg")]
[assembly: AssemblyVersion("1.3.0.0")]
[assembly: AssemblyFileVersion("1.3.0.0")]
[assembly: AssemblyInformationalVersion("1.3.0")]
internal static class FakeApp
{
    private static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "--healthcheck") return 0;
        string marker = Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_LAUNCH_MARKER");
        if (!String.IsNullOrEmpty(marker)) File.WriteAllText(marker, "launched");
        return 0;
    }
}
