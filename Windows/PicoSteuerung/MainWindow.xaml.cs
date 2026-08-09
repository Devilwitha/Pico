using System.Globalization;
using System.Text;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Media;
using System.Windows.Threading;
using Microsoft.Win32;
using PicoSteuerung.Models;
using PicoSteuerung.Services;

namespace PicoSteuerung;

public partial class MainWindow : Window
{
    // Icon/Label/Badge-Zuordnung entspricht 1:1 VERLAUF_ICON/VERLAUF_LABEL/
    // VERLAUF_BADGE in index.html.
    private static readonly Dictionary<string, string> VerlaufIcon = new()
    {
        ["auf"] = "↑",
        ["ab"] = "↓",
        ["stehen"] = "\U0001F6B6",
        ["sitzen"] = "\U0001FA91",
        ["automatik_ein"] = "▶",
        ["automatik_aus"] = "⏸",
    };
    private static readonly Dictionary<string, string> VerlaufLabel = new()
    {
        ["auf"] = "Auf",
        ["ab"] = "Ab",
        ["stehen"] = "Stehen",
        ["sitzen"] = "Sitzen",
        ["automatik_ein"] = "Automatik gestartet",
        ["automatik_aus"] = "Automatik gestoppt",
    };
    private static readonly Dictionary<string, string> VerlaufBadge = new()
    {
        ["automatik"] = "Automatik",
        ["sensor"] = "Sensor",
    };
    private static readonly string[] DateienGeschuetzt = { "main.py", "boot.py" };

    private PicoClient? _client;
    private readonly DispatcherTimer _pollTimer;
    private readonly DispatcherTimer _sekundenTimer;
    private bool _unterdrueckeToggleEreignisse;

    // --- Lokal weiterlaufende Zeitanzeigen (wie sekundenTick() in index.html) ---
    private bool _automatikAktiv;
    private int _automatikRestSekunden;
    private bool _anwesenheitAktiv;
    private int _anwesenheitRestSekunden;
    private bool _infoVerbunden;
    private string _infoIp = "?";
    private string? _infoVersion;
    private string? _infoName;
    private double _infoUptimeBasis;
    private DateTime _infoUptimeBasisZeit;
    private List<VerlaufEintrag> _verlaufBasis = new();
    private DateTime _verlaufBasisZeit;

    // --- Update-Tab ---
    private string? _updateDateiPfad;

    // --- Dateien-Tab ---
    private List<DateiEintrag> _dateien = new();
    private string? _bearbeiteteDatei; // null = neue Datei wird angelegt
    private bool _wlanGeladen;
    private bool _dateienGeladen;

    public MainWindow()
    {
        InitializeComponent();

        _pollTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(10) };
        _pollTimer.Tick += async (_, _) => await SeiteAktualisierenAsync();

        _sekundenTimer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(1) };
        _sekundenTimer.Tick += (_, _) => SekundenTick();
    }

    private async void MainWindow_Loaded(object sender, RoutedEventArgs e)
    {
        _sekundenTimer.Start();

        var letzterHost = Einstellungen.LetzterHost;
        if (!string.IsNullOrWhiteSpace(letzterHost))
        {
            HostEingabe.Text = letzterHost;
            Verbinden(letzterHost);
        }
        else
        {
            await AutomatischSuchenUndVerbindenAsync();
        }
    }

    private void MainWindow_Closing(object? sender, System.ComponentModel.CancelEventArgs e)
    {
        _pollTimer.Stop();
        _sekundenTimer.Stop();
    }

    // ==================================================================
    // Verbindung (Host-Eingabe, automatische Suche per UDP-Discovery)
    // ==================================================================

    private void Verbinden(string host)
    {
        _client = new PicoClient(host);
        Einstellungen.LetzterHost = host;
        _wlanGeladen = false;
        _dateienGeladen = false;
        _pollTimer.Stop();
        _pollTimer.Start();
        _ = SeiteAktualisierenAsync();
    }

    private void VerbindenButton_Click(object sender, RoutedEventArgs e)
    {
        var host = HostEingabe.Text.Trim();
        if (string.IsNullOrWhiteSpace(host)) return;
        Verbinden(host);
    }

    private async void SuchenButton_Click(object sender, RoutedEventArgs e)
    {
        await AutomatischSuchenUndVerbindenAsync();
    }

    private async Task AutomatischSuchenUndVerbindenAsync()
    {
        SuchenButton.IsEnabled = false;
        SucheStatus.Text = "Suche Pico im Netz...";
        try
        {
            var gefunden = await PicoDiscovery.SucheAsync(TimeSpan.FromSeconds(2.5));
            if (gefunden.Count == 0)
            {
                SucheStatus.Text = "Kein Pico gefunden - laeuft er im Recovery-Hotspot, mit diesem WLAN verbinden und erneut suchen.";
                return;
            }
            var ziel = gefunden[0];
            HostEingabe.Text = ziel.Ip;
            SucheStatus.Text = gefunden.Count == 1
                ? $"Gefunden: {ziel.Anzeigename} ({ziel.Ip})"
                : $"{gefunden.Count} Picos gefunden - verbinde mit {ziel.Anzeigename} ({ziel.Ip})";
            Verbinden(ziel.Ip);
        }
        catch (Exception ex)
        {
            SucheStatus.Text = "Suche fehlgeschlagen: " + ex.Message;
        }
        finally
        {
            SuchenButton.IsEnabled = true;
        }
    }

    // ==================================================================
    // Steuerung: Auf/Ab (Halten) und Stehen/Sitzen (Impuls)
    // ==================================================================

    private void ZeigeAktionStatus(string text) => AktionStatus.Text = text;

    private async void AufAbHalten_Down(object sender, MouseButtonEventArgs e)
    {
        if (_client is null || sender is not Button btn) return;
        var aktion = (string)btn.Tag;
        ZeigeAktionStatus($"\"{aktion}\" haelt...");
        try
        {
            var ok = await _client.StartAsync(aktion);
            ZeigeAktionStatus(ok ? $"\"{aktion}\" aktiv" : $"Fehler bei \"{aktion}\"");
            if (ok) await VerlaufAbrufenAsync();
        }
        catch { ZeigeAktionStatus("Verbindungsfehler"); }
    }

    private async void AufAbHalten_Up(object sender, MouseEventArgs e)
    {
        if (_client is null || sender is not Button btn) return;
        var aktion = (string)btn.Tag;
        try
        {
            var ok = await _client.StopAsync(aktion);
            ZeigeAktionStatus(ok ? $"\"{aktion}\" gestoppt" : $"Fehler bei \"{aktion}\"");
            if (ok) await VerlaufAbrufenAsync();
        }
        catch { ZeigeAktionStatus("Verbindungsfehler"); }
    }

    private async void StehenSitzen_Click(object sender, RoutedEventArgs e)
    {
        if (_client is null || sender is not Button btn) return;
        var aktion = (string)btn.Tag;
        ZeigeAktionStatus($"Sende \"{aktion}\" ...");
        try
        {
            var ok = await _client.AktionAsync(aktion);
            ZeigeAktionStatus(ok ? $"\"{aktion}\" ausgefuehrt" : $"Fehler bei \"{aktion}\"");
            if (ok) await VerlaufAbrufenAsync();
        }
        catch { ZeigeAktionStatus("Verbindungsfehler"); }
    }

    // ==================================================================
    // Automatik
    // ==================================================================

    private static string FormatZeit(int sek)
    {
        sek = Math.Max(0, sek);
        return $"{sek / 60:D2}:{sek % 60:D2}";
    }

    private void AutomatikAnzeigen(AutomatikStatus data)
    {
        _unterdrueckeToggleEreignisse = true;
        AutomatikToggle.IsChecked = data.Aktiv;
        _unterdrueckeToggleEreignisse = false;

        SitzenMinEingabe.IsEnabled = !data.Aktiv;
        StehenMinEingabe.IsEnabled = !data.Aktiv;
        StartPhaseAuswahl.IsEnabled = !data.Aktiv;
        _automatikAktiv = data.Aktiv;

        if (data.Aktiv)
        {
            if (data.SitzenMin > 0) SitzenMinEingabe.Text = data.SitzenMin.ToString();
            if (data.StehenMin > 0) StehenMinEingabe.Text = data.StehenMin.ToString();

            var aktuellePhase = data.Phase == "sitzen" ? "Sitzen" : "Stehen";
            var naechstePhase = data.Phase == "sitzen" ? "Stehen" : "Sitzen";
            AutomatikPhaseText.Text = "Aktiv - " + aktuellePhase;
            AutomatikSubText.Text = $"bis Wechsel zu \"{naechstePhase}\"";
            _automatikRestSekunden = data.RestSek;
            AutomatikTimerText.Text = FormatZeit(_automatikRestSekunden);
        }
        else
        {
            AutomatikPhaseText.Text = "Automatik";
            AutomatikTimerText.Text = "--:--";
            AutomatikSubText.Text = "ausgeschaltet";
        }
    }

    private async Task AutomatikStatusAbrufenAsync()
    {
        if (_client is null) return;
        try { AutomatikAnzeigen(await _client.AutomatikStatusAsync() ?? new AutomatikStatus()); }
        catch { /* Status wird beim naechsten Intervall erneut versucht */ }
    }

    private async void AutomatikToggle_Changed(object sender, RoutedEventArgs e)
    {
        if (_unterdrueckeToggleEreignisse || _client is null) return;
        try
        {
            if (AutomatikToggle.IsChecked == true)
            {
                var sitzen = int.TryParse(SitzenMinEingabe.Text, out var s) ? s : 90;
                var stehen = int.TryParse(StehenMinEingabe.Text, out var st) ? st : 30;
                var phase = (string)((ComboBoxItem)StartPhaseAuswahl.SelectedItem).Tag;
                AutomatikAnzeigen(await _client.AutomatikStartAsync(sitzen, stehen, phase) ?? new AutomatikStatus());
            }
            else
            {
                AutomatikAnzeigen(await _client.AutomatikStopAsync() ?? new AutomatikStatus());
            }
        }
        catch { AutomatikSubText.Text = "Verbindungsfehler"; }
    }

    // ==================================================================
    // Bewegungserkennung (Anwesenheit)
    // ==================================================================

    private void AnwesenheitAnzeigen(AnwesenheitStatus data)
    {
        _unterdrueckeToggleEreignisse = true;
        AnwesenheitToggle.IsChecked = data.Aktiv;
        _unterdrueckeToggleEreignisse = false;

        AbfrageEingabe.IsEnabled = !data.Aktiv;
        TimeoutEingabe.IsEnabled = !data.Aktiv;
        _anwesenheitAktiv = data.Aktiv;

        if (data.Aktiv)
        {
            if (data.AbfrageSek > 0) AbfrageEingabe.Text = data.AbfrageSek.ToString(CultureInfo.InvariantCulture);
            if (data.KeineAenderungMin > 0) TimeoutEingabe.Text = data.KeineAenderungMin.ToString(CultureInfo.InvariantCulture);
            AnwesenheitDistanzText.Text = (data.DistanzCm?.ToString() ?? "--") + " cm";
            _anwesenheitRestSekunden = data.RestSek;
            AnwesenheitTimerText.Text = FormatZeit(_anwesenheitRestSekunden);
            AnwesenheitSubText.Text = "bis Automatik-Stopp ohne Bewegung";
        }
        else
        {
            AnwesenheitDistanzText.Text = "Sensor";
            AnwesenheitTimerText.Text = "--:--";
            AnwesenheitSubText.Text = "ausgeschaltet";
        }
    }

    private async Task AnwesenheitStatusAbrufenAsync()
    {
        if (_client is null) return;
        try { AnwesenheitAnzeigen(await _client.AnwesenheitStatusAsync() ?? new AnwesenheitStatus()); }
        catch { /* Status wird beim naechsten Intervall erneut versucht */ }
    }

    private async void AnwesenheitToggle_Changed(object sender, RoutedEventArgs e)
    {
        if (_unterdrueckeToggleEreignisse || _client is null) return;
        try
        {
            if (AnwesenheitToggle.IsChecked == true)
            {
                var abfrage = double.TryParse(AbfrageEingabe.Text, NumberStyles.Any, CultureInfo.InvariantCulture, out var a) ? a : 3;
                var timeout = double.TryParse(TimeoutEingabe.Text, NumberStyles.Any, CultureInfo.InvariantCulture, out var t) ? t : 10;
                AnwesenheitAnzeigen(await _client.AnwesenheitStartAsync(abfrage, timeout) ?? new AnwesenheitStatus());
            }
            else
            {
                AnwesenheitAnzeigen(await _client.AnwesenheitStopAsync() ?? new AnwesenheitStatus());
            }
        }
        catch { AnwesenheitSubText.Text = "Verbindungsfehler"; }
    }

    // ==================================================================
    // Info-Zeile (IP/Laufzeit/Version) + Online-Anzeige
    // ==================================================================

    private static string FormatDauer(double sek)
    {
        sek = Math.Max(0, sek);
        var h = (int)(sek / 3600);
        var m = (int)(sek % 3600 / 60);
        if (h > 0) return $"{h}h {m}m";
        if (m > 0) return $"{m}m";
        return $"{(int)sek}s";
    }

    private void InfoZeileRendern()
    {
        if (!_infoVerbunden) return;
        var vergangen = (DateTime.UtcNow - _infoUptimeBasisZeit).TotalSeconds;
        var text = $"{_infoIp} - Laufzeit {FormatDauer(_infoUptimeBasis + vergangen)}";
        if (_infoVersion is not null) text += $" - v{_infoVersion}";
        InfoZeile.Text = text;
    }

    private async Task InfoAbrufenAsync()
    {
        if (_client is null) return;
        try
        {
            var data = await _client.InfoAsync() ?? throw new PicoFehlerException("keine Daten");
            _infoVerbunden = true;
            _infoIp = data.Ip;
            _infoVersion = data.Version;
            _infoUptimeBasis = data.UptimeSek;
            _infoUptimeBasisZeit = DateTime.UtcNow;
            InfoZeileRendern();
            LivePunkt.Fill = (Brush)FindResource("GruenBrush");

            if (!string.IsNullOrWhiteSpace(data.Name) && data.Name != _infoName)
            {
                _infoName = data.Name;
                GeraeteNameText.Text = _infoName;
                Title = _infoName + " – Pico Steuerung";
                if (!GeraeteNameEingabe.IsFocused) GeraeteNameEingabe.Text = _infoName;
            }
        }
        catch
        {
            _infoVerbunden = false;
            InfoZeile.Text = "Keine Verbindung zum Pico";
            LivePunkt.Fill = (Brush)FindResource("RotBrush");
        }
    }

    // ==================================================================
    // Verlauf der letzten Aktionen
    // ==================================================================

    private static string FormatVor(int sek)
    {
        if (sek < 60) return $"vor {sek}s";
        var m = sek / 60;
        if (m < 60) return $"vor {m}m";
        return $"vor {m / 60}h";
    }

    private void VerlaufRendern()
    {
        VerlaufListe.Children.Clear();
        if (_verlaufBasis.Count == 0)
        {
            VerlaufListe.Children.Add(new TextBlock
            {
                Text = "Noch keine Aktionen",
                Foreground = (Brush)FindResource("TextDimBrush"),
                FontSize = 12.5,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 6, 0, 6),
            });
            return;
        }

        var vergangenSek = (int)(DateTime.UtcNow - _verlaufBasisZeit).TotalSeconds;
        foreach (var eintrag in _verlaufBasis)
        {
            var icon = VerlaufIcon.GetValueOrDefault(eintrag.Aktion, "•");
            var label = VerlaufLabel.GetValueOrDefault(eintrag.Aktion, eintrag.Aktion);
            var badge = VerlaufBadge.GetValueOrDefault(eintrag.Quelle);

            var zeile = new Grid { Margin = new Thickness(0, 6, 0, 6) };
            zeile.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            zeile.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            zeile.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var iconText = new TextBlock { Text = icon, FontSize = 15, Width = 30, TextAlignment = TextAlignment.Center, VerticalAlignment = VerticalAlignment.Center };
            Grid.SetColumn(iconText, 0);

            var mittePanel = new StackPanel { Orientation = Orientation.Horizontal, Margin = new Thickness(10, 0, 0, 0), VerticalAlignment = VerticalAlignment.Center };
            mittePanel.Children.Add(new TextBlock { Text = label, FontSize = 13 });
            if (badge is not null)
            {
                mittePanel.Children.Add(new Border
                {
                    Background = new SolidColorBrush(Color.FromArgb(0x24, 0xA8, 0x55, 0xF7)),
                    CornerRadius = new CornerRadius(999),
                    Padding = new Thickness(7, 2, 7, 2),
                    Margin = new Thickness(6, 0, 0, 0),
                    Child = new TextBlock { Text = badge, FontSize = 9.5, FontWeight = FontWeights.Bold, Foreground = (Brush)FindResource("Akzent2Brush") },
                });
            }
            Grid.SetColumn(mittePanel, 1);

            var zeitText = new TextBlock { Text = FormatVor(eintrag.VorSek + vergangenSek), FontSize = 11.5, Foreground = (Brush)FindResource("TextDimBrush"), VerticalAlignment = VerticalAlignment.Center };
            Grid.SetColumn(zeitText, 2);

            zeile.Children.Add(iconText);
            zeile.Children.Add(mittePanel);
            zeile.Children.Add(zeitText);
            VerlaufListe.Children.Add(zeile);
        }
    }

    private async Task VerlaufAbrufenAsync()
    {
        if (_client is null) return;
        try
        {
            _verlaufBasis = await _client.VerlaufAsync();
            _verlaufBasisZeit = DateTime.UtcNow;
            VerlaufRendern();
        }
        catch { /* Verlauf wird beim naechsten Poll erneut versucht */ }
    }

    // ==================================================================
    // Update: neue Datei hochladen (Geraet startet bei .py danach neu)
    // ==================================================================

    private void UpdateInfoAnzeigen(string text) => UpdateStatus.Text = text;

    private void UpdateDateiWaehlen_Click(object sender, RoutedEventArgs e)
    {
        var dialog = new OpenFileDialog { Filter = "Update-Dateien (*.py;*.html)|*.py;*.html|Alle Dateien (*.*)|*.*" };
        if (dialog.ShowDialog() != true) return;
        _updateDateiPfad = dialog.FileName;
        UpdateDateiName.Text = System.IO.Path.GetFileName(dialog.FileName);
        UpdateHochladenButton.IsEnabled = true;
    }

    private async void UpdateHochladen_Click(object sender, RoutedEventArgs e)
    {
        if (_client is null || _updateDateiPfad is null) return;
        var ziel = System.IO.Path.GetFileName(_updateDateiPfad);
        var istPython = ziel.EndsWith(".py", StringComparison.OrdinalIgnoreCase);
        var frage = $"\"{ziel}\" auf dem Pico speichern" + (istPython ? " und danach neu starten?" : "?");
        if (MessageBox.Show(frage, "Update hochladen", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;

        UpdateHochladenButton.IsEnabled = false;
        UpdateInfoAnzeigen("Lade hoch...");
        try
        {
            var inhalt = await System.IO.File.ReadAllBytesAsync(_updateDateiPfad);
            var antwort = await _client.UpdateAsync(ziel, inhalt);
            if (antwort.Ok && antwort.Neustart)
            {
                UpdateInfoAnzeigen("Update erfolgreich - Pico startet neu.");
            }
            else if (antwort.Ok)
            {
                UpdateInfoAnzeigen(ziel + " aktualisiert - kein Neustart noetig.");
                await SeiteAktualisierenAsync();
            }
            else
            {
                UpdateInfoAnzeigen("Fehler: " + (antwort.Fehler ?? "unbekannt"));
                UpdateHochladenButton.IsEnabled = true;
            }
        }
        catch
        {
            UpdateInfoAnzeigen("Verbindung getrennt - falls das Update angenommen wurde, startet der Pico gerade neu.");
            UpdateHochladenButton.IsEnabled = true;
        }
    }

    private async void Neustart_Click(object sender, RoutedEventArgs e)
    {
        if (_client is null) return;
        if (MessageBox.Show("Pico jetzt neu starten?", "Neustart", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        UpdateInfoAnzeigen("Neustart...");
        await _client.NeustartAsync();
    }

    // ==================================================================
    // Einstellungen (Geraetename, WLAN)
    // ==================================================================

    private async void NameSpeichern_Click(object sender, RoutedEventArgs e)
    {
        if (_client is null) return;
        var name = GeraeteNameEingabe.Text.Trim();
        if (string.IsNullOrEmpty(name)) return;

        NameSpeichernButton.IsEnabled = false;
        NameStatusText.Text = "Speichere...";
        try
        {
            var antwort = await _client.NameSpeichernAsync(name);
            if (antwort.Ok)
            {
                NameStatusText.Text = "Gespeichert.";
                _infoName = name;
                GeraeteNameText.Text = name;
                Title = name + " – Pico Steuerung";
            }
            else
            {
                NameStatusText.Text = "Fehler: " + (antwort.Fehler ?? "unbekannt");
            }
        }
        catch { NameStatusText.Text = "Verbindungsfehler"; }
        finally { NameSpeichernButton.IsEnabled = true; }
    }

    private async Task WlanStatusLadenAsync()
    {
        if (_client is null) return;
        try
        {
            var data = await _client.WlanStatusAsync() ?? throw new PicoFehlerException("keine Daten");
            if (!string.IsNullOrEmpty(data.SsidKonfiguriert)) WlanSsidEingabe.Text = data.SsidKonfiguriert;

            if (data.Modus == "hotspot")
            {
                var restMin = Math.Ceiling((data.HotspotRestSek ?? 0) / 60.0);
                WlanHinweisText.Text = $"Verbindung zu \"{data.SsidKonfiguriert ?? "?"}\" fehlgeschlagen. Der Pico ist als Hotspot \"{data.HotspotSsid}\" erreichbar. Bitte WLAN-Zugangsdaten eingeben - der Pico startet danach neu und versucht es erneut (automatischer Neuversuch in ca. {restMin} Min.).";
            }
            else
            {
                WlanHinweisText.Text = $"Aktuell verbunden mit \"{data.SsidKonfiguriert ?? "?"}\" ({data.Ip}). Neue Zugangsdaten hier speichern, um das WLAN zu wechseln.";
            }
        }
        catch
        {
            WlanHinweisText.Text = "Status konnte nicht geladen werden - trotzdem koennen hier neue Zugangsdaten gespeichert werden.";
        }
    }

    private async void WlanSpeichern_Click(object sender, RoutedEventArgs e)
    {
        if (_client is null) return;
        var ssid = WlanSsidEingabe.Text.Trim();
        if (string.IsNullOrEmpty(ssid)) return;

        WlanSpeichernButton.IsEnabled = false;
        WlanStatusText.Text = "Speichere...";
        try
        {
            var antwort = await _client.WlanSpeichernAsync(ssid, WlanPasswortEingabe.Password);
            if (antwort.Ok)
            {
                WlanStatusText.Text = $"Gespeichert - Pico startet neu und verbindet sich mit \"{ssid}\".";
            }
            else
            {
                WlanStatusText.Text = "Fehler: " + (antwort.Fehler ?? "unbekannt");
                WlanSpeichernButton.IsEnabled = true;
            }
        }
        catch
        {
            WlanStatusText.Text = "Gespeichert - Verbindung getrennt, der Pico startet vermutlich gerade neu.";
        }
    }

    // ==================================================================
    // Dateien: auflisten, bearbeiten, anlegen, loeschen
    // ==================================================================

    private static string FormatGroesse(long bytes) => bytes < 1024 ? $"{bytes} B" : $"{bytes / 1024.0:F1} KB";

    private void DateiListeRendern()
    {
        var filter = DateiSucheEingabe.Text.Trim().ToLowerInvariant();
        var gefiltert = _dateien.Where(d => d.Name.ToLowerInvariant().Contains(filter)).ToList();

        DateiListe.Children.Clear();
        if (gefiltert.Count == 0)
        {
            DateiListe.Children.Add(new TextBlock
            {
                Text = _dateien.Count == 0 ? "Keine Dateien gefunden" : "Keine Treffer",
                Foreground = (Brush)FindResource("TextDimBrush"),
                FontSize = 12.5,
                HorizontalAlignment = HorizontalAlignment.Center,
                Margin = new Thickness(0, 10, 0, 10),
            });
            return;
        }

        foreach (var datei in gefiltert)
        {
            var geschuetzt = DateienGeschuetzt.Contains(datei.Name);

            var zeile = new Grid { Margin = new Thickness(0, 6, 0, 6) };
            zeile.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
            zeile.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
            zeile.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

            var infoPanel = new StackPanel { VerticalAlignment = VerticalAlignment.Center };
            infoPanel.Children.Add(new TextBlock { Text = datei.Name, FontSize = 13.5, TextTrimming = TextTrimming.CharacterEllipsis });
            infoPanel.Children.Add(new TextBlock { Text = FormatGroesse(datei.Groesse), FontSize = 11, Foreground = (Brush)FindResource("TextDimBrush") });
            Grid.SetColumn(infoPanel, 0);

            var bearbeitenBtn = new Button { Content = "✎", Width = 32, Margin = new Thickness(6, 0, 0, 0), ToolTip = "Bearbeiten" };
            bearbeitenBtn.Click += async (_, _) => await DateiBearbeitenAsync(datei.Name);
            Grid.SetColumn(bearbeitenBtn, 1);

            var loeschenBtn = new Button { Content = "\U0001F5D1", Width = 32, Margin = new Thickness(6, 0, 0, 0), ToolTip = "Loeschen", IsEnabled = !geschuetzt };
            loeschenBtn.Click += async (_, _) => await DateiLoeschenAsync(datei.Name);
            Grid.SetColumn(loeschenBtn, 2);

            zeile.Children.Add(infoPanel);
            zeile.Children.Add(bearbeitenBtn);
            zeile.Children.Add(loeschenBtn);
            DateiListe.Children.Add(zeile);
        }
    }

    private async Task DateienLadenAsync()
    {
        if (_client is null) return;
        DateiListeStatus.Text = "Lade...";
        try
        {
            _dateien = await _client.DateienListeAsync();
            DateiListeRendern();
            DateiListeStatus.Text = "";
            _dateienGeladen = true;
        }
        catch
        {
            DateiListeStatus.Text = "Verbindungsfehler";
        }
    }

    private void DateiSuche_Changed(object sender, TextChangedEventArgs e) => DateiListeRendern();

    private void DateiEditorAnzeigen(string? name, string inhalt)
    {
        _bearbeiteteDatei = name;
        DateiNameEingabe.Text = name ?? "";
        DateiNameEingabe.IsEnabled = name is null;
        DateiInhaltEingabe.Text = inhalt;
        DateiEditorStatus.Text = "";
        DateiSpeichernButton.IsEnabled = true;
        DateiAbbrechenButton.IsEnabled = true;
        DateienListenAnsicht.Visibility = Visibility.Collapsed;
        DateiEditorAnsicht.Visibility = Visibility.Visible;
    }

    private void DateiEditorSchliessen()
    {
        DateiEditorAnsicht.Visibility = Visibility.Collapsed;
        DateienListenAnsicht.Visibility = Visibility.Visible;
        _bearbeiteteDatei = null;
        DateiNameEingabe.IsEnabled = true;
    }

    private void DateiNeu_Click(object sender, RoutedEventArgs e) => DateiEditorAnzeigen(null, "");

    private void DateiAbbrechen_Click(object sender, RoutedEventArgs e) => DateiEditorSchliessen();

    private async Task DateiBearbeitenAsync(string name)
    {
        if (_client is null) return;
        DateiListeStatus.Text = $"Lade \"{name}\"...";
        try
        {
            var inhalt = await _client.DateiLesenAsync(name);
            DateiListeStatus.Text = "";
            DateiEditorAnzeigen(name, inhalt);
        }
        catch (Exception ex)
        {
            DateiListeStatus.Text = "Fehler: " + ex.Message;
        }
    }

    private async Task DateiLoeschenAsync(string name)
    {
        if (_client is null) return;
        if (MessageBox.Show($"\"{name}\" wirklich loeschen?", "Datei loeschen", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;
        DateiListeStatus.Text = "Loesche...";
        try
        {
            var antwort = await _client.DateiLoeschenAsync(name);
            if (antwort.Ok)
            {
                DateiListeStatus.Text = $"\"{name}\" geloescht";
                await DateienLadenAsync();
            }
            else
            {
                DateiListeStatus.Text = "Fehler: " + (antwort.Fehler ?? "unbekannt");
            }
        }
        catch { DateiListeStatus.Text = "Verbindungsfehler"; }
    }

    private async void DateiSpeichern_Click(object sender, RoutedEventArgs e)
    {
        if (_client is null) return;
        var name = _bearbeiteteDatei ?? DateiNameEingabe.Text.Trim();
        if (string.IsNullOrEmpty(name))
        {
            DateiEditorStatus.Text = "Bitte einen Dateinamen eingeben";
            return;
        }
        if (string.IsNullOrWhiteSpace(DateiInhaltEingabe.Text))
        {
            DateiEditorStatus.Text = "Datei darf nicht leer sein";
            return;
        }

        var istPython = name.EndsWith(".py", StringComparison.OrdinalIgnoreCase);
        var frage = $"\"{name}\" speichern" + (istPython ? " und Pico danach neu starten?" : "?");
        if (MessageBox.Show(frage, "Datei speichern", MessageBoxButton.YesNo) != MessageBoxResult.Yes) return;

        DateiSpeichernButton.IsEnabled = false;
        DateiAbbrechenButton.IsEnabled = false;
        DateiEditorStatus.Text = "Speichere...";
        try
        {
            var antwort = await _client.UpdateAsync(name, Encoding.UTF8.GetBytes(DateiInhaltEingabe.Text));
            if (antwort.Ok)
            {
                DateiEditorStatus.Text = antwort.Neustart ? "Gespeichert - Pico startet neu." : "Gespeichert.";
                await Task.Delay(600);
                DateiEditorSchliessen();
                await DateienLadenAsync();
            }
            else
            {
                DateiEditorStatus.Text = "Fehler: " + (antwort.Fehler ?? "unbekannt");
                DateiSpeichernButton.IsEnabled = true;
                DateiAbbrechenButton.IsEnabled = true;
            }
        }
        catch
        {
            DateiEditorStatus.Text = "Verbindung getrennt - falls gespeichert wurde, startet der Pico ggf. gerade neu.";
            DateiSpeichernButton.IsEnabled = true;
            DateiAbbrechenButton.IsEnabled = true;
        }
    }

    // ==================================================================
    // Tabs: Einstellungen/Dateien beim ersten Anzeigen laden
    // ==================================================================

    private async void HauptTabs_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_client is null || HauptTabs.SelectedItem is not TabItem tab) return;
        switch (tab.Header)
        {
            case "Einstellungen" when !_wlanGeladen:
                _wlanGeladen = true;
                await WlanStatusLadenAsync();
                break;
            case "Dateien" when !_dateienGeladen:
                await DateienLadenAsync();
                break;
        }
    }

    // ==================================================================
    // Polling (alle 10s echte Daten vom Pico) + Sekunden-Tick (lokale Anzeige)
    // ==================================================================

    private async Task SeiteAktualisierenAsync()
    {
        if (_client is null) return;
        await InfoAbrufenAsync();
        await VerlaufAbrufenAsync();
        await AutomatikStatusAbrufenAsync();
        await AnwesenheitStatusAbrufenAsync();
    }

    private void SekundenTick()
    {
        if (_automatikAktiv)
        {
            _automatikRestSekunden = Math.Max(0, _automatikRestSekunden - 1);
            AutomatikTimerText.Text = FormatZeit(_automatikRestSekunden);
        }
        if (_anwesenheitAktiv)
        {
            _anwesenheitRestSekunden = Math.Max(0, _anwesenheitRestSekunden - 1);
            AnwesenheitTimerText.Text = FormatZeit(_anwesenheitRestSekunden);
        }
        InfoZeileRendern();
        VerlaufRendern();
    }
}
