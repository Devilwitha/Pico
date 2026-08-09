using System.Net.Http;
using System.Net.Http.Headers;
using System.Text;
using System.Text.Json;
using PicoSteuerung.Models;

namespace PicoSteuerung.Services;

/// <summary>
/// Spricht dieselben HTTP-Endpunkte an, die main.py auf dem Pico bereitstellt
/// (siehe dortiger anfrage_bearbeiten()) - ein 1:1 Gegenstueck zu den fetch()-
/// Aufrufen in index.html/einstellungen.html/dateien.html.
/// </summary>
public class PicoClient
{
    private static readonly JsonSerializerOptions JsonOptionen = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _http;

    public string Host { get; }

    public PicoClient(string host, TimeSpan? timeout = null)
    {
        Host = host;
        _http = new HttpClient
        {
            BaseAddress = new Uri("http://" + host + "/"),
            Timeout = timeout ?? TimeSpan.FromSeconds(6),
        };
    }

    private async Task<T?> GetJsonAsync<T>(string pfad, CancellationToken ct = default)
    {
        using var antwort = await _http.GetAsync(pfad, ct);
        antwort.EnsureSuccessStatusCode();
        await using var stream = await antwort.Content.ReadAsStreamAsync(ct);
        return await JsonSerializer.DeserializeAsync<T>(stream, JsonOptionen, ct);
    }

    public Task<GeraeteInfo?> InfoAsync(CancellationToken ct = default) => GetJsonAsync<GeraeteInfo>("info", ct);

    public async Task<List<VerlaufEintrag>> VerlaufAsync(CancellationToken ct = default) =>
        await GetJsonAsync<List<VerlaufEintrag>>("verlauf", ct) ?? new();

    public async Task<bool> AktionAsync(string name, CancellationToken ct = default)
    {
        var antwort = await GetJsonAsync<AktionAntwort>("aktion/" + name, ct);
        return antwort?.Ok ?? false;
    }

    public async Task<bool> StartAsync(string name, CancellationToken ct = default)
    {
        var antwort = await GetJsonAsync<AktionAntwort>("start/" + name, ct);
        return antwort?.Ok ?? false;
    }

    public async Task<bool> StopAsync(string name, CancellationToken ct = default)
    {
        var antwort = await GetJsonAsync<AktionAntwort>("stop/" + name, ct);
        return antwort?.Ok ?? false;
    }

    public Task<AutomatikStatus?> AutomatikStartAsync(int sitzenMin, int stehenMin, string phase, CancellationToken ct = default) =>
        GetJsonAsync<AutomatikStatus>($"automatik/start?sitzen={sitzenMin}&stehen={stehenMin}&phase={phase}", ct);

    public Task<AutomatikStatus?> AutomatikStopAsync(CancellationToken ct = default) =>
        GetJsonAsync<AutomatikStatus>("automatik/stop", ct);

    public Task<AutomatikStatus?> AutomatikStatusAsync(CancellationToken ct = default) =>
        GetJsonAsync<AutomatikStatus>("automatik/status", ct);

    public Task<AnwesenheitStatus?> AnwesenheitStartAsync(double abfrageSek, double timeoutMin, CancellationToken ct = default) =>
        GetJsonAsync<AnwesenheitStatus>(
            $"anwesenheit/start?abfrage={abfrageSek.ToString(System.Globalization.CultureInfo.InvariantCulture)}&timeout={timeoutMin.ToString(System.Globalization.CultureInfo.InvariantCulture)}",
            ct);

    public Task<AnwesenheitStatus?> AnwesenheitStopAsync(CancellationToken ct = default) =>
        GetJsonAsync<AnwesenheitStatus>("anwesenheit/stop", ct);

    public Task<AnwesenheitStatus?> AnwesenheitStatusAsync(CancellationToken ct = default) =>
        GetJsonAsync<AnwesenheitStatus>("anwesenheit/status", ct);

    public Task<WlanStatus?> WlanStatusAsync(CancellationToken ct = default) =>
        GetJsonAsync<WlanStatus>("wlan/status", ct);

    public async Task<AktionAntwort> NameSpeichernAsync(string name, CancellationToken ct = default)
    {
        var body = JsonSerializer.Serialize(new { name }, JsonOptionen);
        using var inhalt = new StringContent(body, Encoding.UTF8, "application/json");
        using var antwort = await _http.PostAsync("name/speichern", inhalt, ct);
        var text = await antwort.Content.ReadAsStringAsync(ct);
        return JsonSerializer.Deserialize<AktionAntwort>(text, JsonOptionen) ?? new AktionAntwort { Ok = false };
    }

    public async Task<AktionAntwort> WlanSpeichernAsync(string ssid, string passwort, CancellationToken ct = default)
    {
        var body = JsonSerializer.Serialize(new { ssid, password = passwort }, JsonOptionen);
        using var inhalt = new StringContent(body, Encoding.UTF8, "application/json");
        using var antwort = await _http.PostAsync("wlan/speichern", inhalt, ct);
        var text = await antwort.Content.ReadAsStringAsync(ct);
        return JsonSerializer.Deserialize<AktionAntwort>(text, JsonOptionen) ?? new AktionAntwort { Ok = false };
    }

    public async Task NeustartAsync(CancellationToken ct = default)
    {
        try { await _http.GetAsync("neustart", ct); } catch { /* Verbindung bricht durch den Neustart erwartungsgemaess ab */ }
    }

    public async Task<List<DateiEintrag>> DateienListeAsync(CancellationToken ct = default) =>
        await GetJsonAsync<List<DateiEintrag>>("dateien/liste", ct) ?? new();

    public async Task<string> DateiLesenAsync(string name, CancellationToken ct = default)
    {
        using var antwort = await _http.GetAsync("dateien/lesen?name=" + Uri.EscapeDataString(name), ct);
        var text = await antwort.Content.ReadAsStringAsync(ct);
        if (!antwort.IsSuccessStatusCode)
        {
            var fehler = TryParseFehler(text);
            throw new PicoFehlerException(fehler ?? ("HTTP " + (int)antwort.StatusCode));
        }
        return text;
    }

    public async Task<AktionAntwort> DateiLoeschenAsync(string name, CancellationToken ct = default)
    {
        using var antwort = await _http.GetAsync("dateien/loeschen?name=" + Uri.EscapeDataString(name), ct);
        var text = await antwort.Content.ReadAsStringAsync(ct);
        return JsonSerializer.Deserialize<AktionAntwort>(text, JsonOptionen) ?? new AktionAntwort { Ok = false };
    }

    /// <summary>
    /// Laedt eine Datei unter ihrem eigenen Namen hoch (identischer Mechanismus
    /// wie der Datei-Upload in index.html/dateien.html): existiert die Datei
    /// schon auf dem Pico, wird nur sie ersetzt (Backup bleibt als "&lt;name&gt;.bak"
    /// erhalten), .py-Dateien werden geprueft und starten den Pico danach neu.
    /// </summary>
    public async Task<UpdateAntwort> UpdateAsync(string ziel, byte[] inhalt, CancellationToken ct = default)
    {
        using var body = new ByteArrayContent(inhalt);
        body.Headers.ContentType = new MediaTypeHeaderValue("application/octet-stream");
        using var antwort = await _http.PostAsync("update?ziel=" + Uri.EscapeDataString(ziel), body, ct);
        var text = await antwort.Content.ReadAsStringAsync(ct);
        return JsonSerializer.Deserialize<UpdateAntwort>(text, JsonOptionen) ?? new UpdateAntwort { Ok = false };
    }

    private static string? TryParseFehler(string json)
    {
        try { return JsonSerializer.Deserialize<AktionAntwort>(json, JsonOptionen)?.Fehler; }
        catch { return null; }
    }
}

public class PicoFehlerException : Exception
{
    public PicoFehlerException(string message) : base(message) { }
}
