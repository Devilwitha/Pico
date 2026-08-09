using System.IO;
using System.Text.Json;

namespace PicoSteuerung.Services;

/// <summary>
/// Merkt sich den zuletzt verbundenen Host lokal (%AppData%\PicoSteuerung),
/// damit die App beim naechsten Start automatisch dorthin verbinden kann,
/// ohne jedes Mal neu suchen zu muessen.
/// </summary>
public static class Einstellungen
{
    private static readonly string Pfad = Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData),
        "PicoSteuerung", "config.json");

    public static string? LetzterHost
    {
        get
        {
            try
            {
                if (!File.Exists(Pfad)) return null;
                var daten = JsonSerializer.Deserialize<Dictionary<string, string>>(File.ReadAllText(Pfad));
                return daten is not null && daten.TryGetValue("host", out var host) && !string.IsNullOrWhiteSpace(host)
                    ? host
                    : null;
            }
            catch { return null; }
        }
        set
        {
            try
            {
                if (string.IsNullOrWhiteSpace(value)) return;
                Directory.CreateDirectory(Path.GetDirectoryName(Pfad)!);
                File.WriteAllText(Pfad, JsonSerializer.Serialize(new Dictionary<string, string> { ["host"] = value }));
            }
            catch { /* Einstellung ist rein komfortbedingt - Fehler beim Speichern sind unkritisch */ }
        }
    }
}
