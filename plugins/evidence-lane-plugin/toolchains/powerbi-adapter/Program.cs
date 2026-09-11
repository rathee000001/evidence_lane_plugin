using System.Text;
using System.Text.Json.Nodes;
using CompatibilityMode = Microsoft.AnalysisServices.CompatibilityMode;
using Microsoft.AnalysisServices.Tabular;
using TomJson = Microsoft.AnalysisServices.Tabular.JsonSerializer;

// Closed protocol: no server, connection, query, refresh, script or client path.
const int MaxInput = 25165824;
const int MaxOutput = 16777216;
string? scratch = null;
var scratchRoot = Path.GetFullPath(Path.GetTempPath()).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
try
{
    if (args.SequenceEqual(new[] { "--version" }))
    {
        Console.WriteLine("evidence-lane-powerbi 4.0.0; Microsoft.AnalysisServices 19.114.12; protocol 1");
        return 0;
    }
    if (args.Length != 0) throw new InvalidDataException();
    using var input = Console.OpenStandardInput();
    using var buffer = new MemoryStream();
    var chunk = new byte[65536];
    int size;
    while ((size = input.Read(chunk)) > 0)
    {
        if (buffer.Length + size > MaxInput) throw new InvalidDataException();
        buffer.Write(chunk, 0, size);
    }
    var request = JsonNode.Parse(buffer.ToArray())!.AsObject();
    if ((string?)request["schema"] != "evidence-lane.powerbi-tom-request.v1") throw new InvalidDataException();
    Database database;
    var format = (string?)request["format"];
    if (format == "bim")
    {
        // Deserialization only constructs in-memory metadata. It never opens a server.
        database = TomJson.DeserializeDatabase((string)request["database_json"]!, new DeserializeOptions(), CompatibilityMode.PowerBI);
    }
    else if (format == "tmdl")
    {
        var files = request["files"]!.AsArray();
        if (files.Count is < 1 or > 256) throw new InvalidDataException();
        scratch = Directory.CreateTempSubdirectory("evidence-lane-powerbi-").FullName;
        if (!Path.GetFullPath(scratch).StartsWith(scratchRoot, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        long admittedBytes = 0;
        foreach (var file in files)
        {
            var name = (string)file!["name"]!;
            var parts = name.Split('/');
            if (name.Length > 1000 || name.Contains('\\') || name.Contains(':') || name.Contains('\0') ||
                parts.Any(p => p.Length == 0 || p is "." or ".." || p.EndsWith(' ') || p.EndsWith('.')) ||
                !name.EndsWith(".tmdl", StringComparison.OrdinalIgnoreCase) || !seen.Add(name)) throw new InvalidDataException();
            var raw = Convert.FromBase64String((string)file["content_base64"]!);
            admittedBytes += raw.Length;
            if (raw.Length > 8388608 || admittedBytes > 16777216) throw new InvalidDataException();
            var target = Path.Combine(scratch, Path.Combine(parts));
            if (!Path.GetFullPath(target).StartsWith(scratch + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException();
            Directory.CreateDirectory(Path.GetDirectoryName(target)!);
            File.WriteAllBytes(target, raw);
        }
        database = File.Exists(Path.Combine(scratch, "database.tmdl"))
            ? TmdlSerializer.DeserializeDatabaseFromFolder(scratch)
            : new Database { Name = "Admitted model", CompatibilityLevel = 1600, Model = TmdlSerializer.DeserializeModelFromFolder(scratch) };
    }
    else throw new InvalidDataException();
    if (database.Model is null) throw new InvalidDataException();
    var serialized = TomJson.SerializeDatabase(database, new SerializeOptions());
    var response = new JsonObject {
        ["schema"] = "evidence-lane.powerbi-tom.v1",
        ["package_version"] = "19.114.12",
        ["database"] = JsonNode.Parse(serialized),
        ["metadata_deserialized"] = true,
        ["server_connected"] = false,
        ["dax_executed"] = false,
        ["data_refreshed"] = false
    }.ToJsonString();
    if (Encoding.UTF8.GetByteCount(response) > MaxOutput) throw new InvalidDataException();
    Console.Write(response);
    return 0;
}
catch
{
    // Vendor exception messages can contain expressions, connection strings and paths.
    Console.Error.Write("POWERBI_TOM_INPUT_REJECTED");
    return 1;
}
finally
{
    if (scratch is not null && Directory.Exists(scratch))
    {
        var resolved = Path.GetFullPath(scratch);
        if (!resolved.StartsWith(scratchRoot, StringComparison.OrdinalIgnoreCase)
            || !Path.GetFileName(resolved).StartsWith("evidence-lane-powerbi-", StringComparison.Ordinal)
            || (File.GetAttributes(resolved) & FileAttributes.ReparsePoint) != 0) throw new InvalidDataException();
        Directory.Delete(resolved, recursive: true);
    }
}
