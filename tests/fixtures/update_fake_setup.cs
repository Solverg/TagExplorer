using System;
using System.IO;
internal static class FakeSetup
{
    private static int Main()
    {
        if (Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_CANCEL") == "1") return 2;
        if (Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_MISSING_INSTALL") == "1") return 0;
        string destination = Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_INSTALLED_EXE");
        Directory.CreateDirectory(Path.GetDirectoryName(destination));
        File.Copy(Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_APP_SOURCE"), destination, true);
        string old = Environment.GetEnvironmentVariable("TAGEXPLORER_TEST_MODIFY_OLD");
        if (!String.IsNullOrEmpty(old)) File.AppendAllText(old, "changed during setup");
        return 0;
    }
}
