using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Text;
using System.Text.Json;
using PicoSteuerung.Models;

namespace PicoSteuerung.Services;

/// <summary>
/// Sucht Picos im Netz per UDP-Broadcast auf Port 4210 mit dem Paket
/// "PICO_DISCOVER" - derselbe Discovery-Mechanismus, den main.py auf dem
/// Pico beantwortet (discovery_tick()) und den auch die Android-App nutzt.
/// </summary>
public static class PicoDiscovery
{
    private const int Port = 4210;
    private static readonly byte[] Anfrage = Encoding.ASCII.GetBytes("PICO_DISCOVER");

    /// <summary>
    /// Schickt die Discovery-Anfrage an alle lokalen Broadcast-Adressen und
    /// sammelt bis zum Timeout alle Antworten (mehrere Picos moeglich).
    /// </summary>
    public static async Task<List<DiscoveryAntwort>> SucheAsync(TimeSpan? timeout = null, CancellationToken ct = default)
    {
        var wartezeit = timeout ?? TimeSpan.FromSeconds(2);
        var gefunden = new List<DiscoveryAntwort>();
        var gefundeneIps = new HashSet<string>();

        using var udp = new UdpClient();
        udp.EnableBroadcast = true;
        udp.Client.SetSocketOption(SocketOptionLevel.Socket, SocketOptionName.ReuseAddress, true);
        udp.Client.Bind(new IPEndPoint(IPAddress.Any, 0));

        foreach (var ziel in BroadcastAdressenErmitteln())
        {
            try { await udp.SendAsync(Anfrage, Anfrage.Length, new IPEndPoint(ziel, Port)); }
            catch { /* einzelnes Interface evtl. nicht sendebereit - andere weiter versuchen */ }
        }

        using var timeoutQuelle = new CancellationTokenSource(wartezeit);
        using var verknuepft = CancellationTokenSource.CreateLinkedTokenSource(ct, timeoutQuelle.Token);
        try
        {
            while (true)
            {
                var ergebnis = await udp.ReceiveAsync(verknuepft.Token);
                try
                {
                    var antwort = JsonSerializer.Deserialize<DiscoveryAntwort>(
                        ergebnis.Buffer, new JsonSerializerOptions(JsonSerializerDefaults.Web));
                    if (antwort is not null && antwort.Typ == "pico" && gefundeneIps.Add(antwort.Ip))
                        gefunden.Add(antwort);
                }
                catch (JsonException) { /* kein gueltiges Discovery-Paket - ignorieren */ }
            }
        }
        catch (OperationCanceledException) { /* Timeout erreicht - Suche beenden */ }
        catch (ObjectDisposedException) { /* udp wurde waehrend des Wartens geschlossen */ }

        return gefunden;
    }

    private static IEnumerable<IPAddress> BroadcastAdressenErmitteln()
    {
        yield return IPAddress.Broadcast; // 255.255.255.255 - reicht in den meisten Heimnetzen

        foreach (var nic in NetworkInterface.GetAllNetworkInterfaces())
        {
            if (nic.OperationalStatus != OperationalStatus.Up) continue;
            if (nic.NetworkInterfaceType == NetworkInterfaceType.Loopback) continue;

            foreach (var unicast in nic.GetIPProperties().UnicastAddresses)
            {
                if (unicast.Address.AddressFamily != AddressFamily.InterNetwork) continue;
                var adresse = GerichteteBroadcastAdresse(unicast.Address, unicast.IPv4Mask);
                if (adresse is not null) yield return adresse;
            }
        }
    }

    private static IPAddress? GerichteteBroadcastAdresse(IPAddress adresse, IPAddress? maske)
    {
        if (maske is null) return null;
        var a = adresse.GetAddressBytes();
        var m = maske.GetAddressBytes();
        if (a.Length != 4 || m.Length != 4) return null;

        var b = new byte[4];
        for (var i = 0; i < 4; i++) b[i] = (byte)(a[i] | ~m[i]);
        return new IPAddress(b);
    }
}
