using System.Text.Json.Serialization;

namespace PicoSteuerung.Models;

// Alle Felder entsprechen 1:1 dem JSON, das main.py auf dem Pico liefert
// (siehe dortige geraeteinfo()/automatik_status()/anwesenheit_status()/...).

public class GeraeteInfo
{
    [JsonPropertyName("ip")] public string Ip { get; set; } = "";
    [JsonPropertyName("hostname")] public string Hostname { get; set; } = "";
    [JsonPropertyName("name")] public string? Name { get; set; }
    [JsonPropertyName("uptime_sek")] public long UptimeSek { get; set; }
    [JsonPropertyName("version")] public string Version { get; set; } = "";
}

public class VerlaufEintrag
{
    [JsonPropertyName("aktion")] public string Aktion { get; set; } = "";
    [JsonPropertyName("quelle")] public string Quelle { get; set; } = "";
    [JsonPropertyName("vor_sek")] public int VorSek { get; set; }
}

public class AutomatikStatus
{
    [JsonPropertyName("aktiv")] public bool Aktiv { get; set; }
    [JsonPropertyName("phase")] public string? Phase { get; set; }
    [JsonPropertyName("rest_sek")] public int RestSek { get; set; }
    [JsonPropertyName("sitzen_min")] public int SitzenMin { get; set; }
    [JsonPropertyName("stehen_min")] public int StehenMin { get; set; }
}

public class AnwesenheitStatus
{
    [JsonPropertyName("aktiv")] public bool Aktiv { get; set; }
    [JsonPropertyName("abfrage_sek")] public double AbfrageSek { get; set; }
    [JsonPropertyName("keine_aenderung_min")] public double KeineAenderungMin { get; set; }
    [JsonPropertyName("distanz_cm")] public int? DistanzCm { get; set; }
    [JsonPropertyName("rest_sek")] public int RestSek { get; set; }
}

public class WlanStatus
{
    [JsonPropertyName("modus")] public string Modus { get; set; } = "normal";
    [JsonPropertyName("ip")] public string Ip { get; set; } = "";
    [JsonPropertyName("hostname")] public string Hostname { get; set; } = "";
    [JsonPropertyName("ssid_konfiguriert")] public string? SsidKonfiguriert { get; set; }
    [JsonPropertyName("hotspot_ssid")] public string? HotspotSsid { get; set; }
    [JsonPropertyName("hotspot_rest_sek")] public int? HotspotRestSek { get; set; }
}

public class DateiEintrag
{
    [JsonPropertyName("name")] public string Name { get; set; } = "";
    [JsonPropertyName("groesse")] public long Groesse { get; set; }
}

public class AktionAntwort
{
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("aktion")] public string? Aktion { get; set; }
    [JsonPropertyName("fehler")] public string? Fehler { get; set; }
}

public class UpdateAntwort
{
    [JsonPropertyName("ok")] public bool Ok { get; set; }
    [JsonPropertyName("neustart")] public bool Neustart { get; set; }
    [JsonPropertyName("fehler")] public string? Fehler { get; set; }
}

public class DiscoveryAntwort
{
    [JsonPropertyName("typ")] public string Typ { get; set; } = "";
    [JsonPropertyName("hostname")] public string Hostname { get; set; } = "";
    [JsonPropertyName("name")] public string? Name { get; set; }
    [JsonPropertyName("ip")] public string Ip { get; set; } = "";
    [JsonPropertyName("modus")] public string Modus { get; set; } = "";
    [JsonPropertyName("version")] public string Version { get; set; } = "";

    /// <summary>Anzeigename, faellt auf den technischen Hostnamen zurueck, falls (noch) kein
    /// eigener Name vergeben wurde.</summary>
    public string Anzeigename => string.IsNullOrWhiteSpace(Name) ? Hostname : Name!;
}
