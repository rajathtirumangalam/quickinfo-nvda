# QuickInfo 3.0

**Keyboard-first Windows and NVDA information for screen-reader users.**

QuickInfo is an NVDA add-on that provides spoken access to Windows, hardware, network, audio, display and NVDA information. Commands are available through keyboard shortcuts, a searchable command browser and a keyboard-accessible settings dialog.

## What's included

### Windows and hardware
- Windows edition, display version, build number and update revision.
- Computer name, system uptime, memory and processor information.
- Hardware and BIOS details, including manufacturer, model, motherboard, memory modules and processor counts where Windows exposes them.
- Battery charge information and battery health estimates such as design capacity, full-charge capacity, wear and cycle count where supported by the battery and firmware.
- System drive information and an inventory of storage volumes and physical disks.
- Display and graphics adapter information, including resolution, refresh rate and driver details where available.
- Current-user Windows display scaling.

### Network
- Wi-Fi status and network name (SSID).
- Active network connection type.
- Internet connectivity, local IP address, DNS servers and default gateway.
- Network diagnostics, including adapter link speed, ping replies, average latency and packet loss.
- An on-demand internet download/upload speed test. It transfers approximately 3 MB down and 0.5 MB up and runs only when requested.

### Audio and input
- Default audio output device.
- Default microphone endpoint.
- Output and microphone volume and mute status where Windows Core Audio exposes the information.
- Keyboard layout, Caps Lock and Num Lock status.
- Clipboard content type.

### NVDA and accessibility
- Screen Curtain status.
- NVDA cursor and review context.
- Current focused-control details.
- NVDA synthesizer, voice and available speech settings.
- Braille display status.
- Installed NVDA add-ons, with versions and enabled/disabled state where available.
- Open application windows.

### Command and settings tools
- Searchable command browser with command categories.
- Keyboard shortcut quick-help window.
- Per-command enable/disable settings.
- Additional user-defined keyboard shortcuts. A custom shortcut adds another binding; it does not replace the default shortcut.
- Language choices for QuickInfo interface text and announcements: English, Hindi, Kannada, Tamil, Telugu and Malayalam.

Windows device names, product names and other proper nouns may remain in the language supplied by Windows. Some hardware and firmware details are not exposed on every computer; QuickInfo reports unavailable information rather than inventing values.

## Opening QuickInfo

| Action | Keyboard shortcut |
|---|---|
| Open QuickInfo settings | `NVDA+Ctrl+Shift+Q` |
| Alternate settings shortcut | `NVDA+Ctrl+Shift+S` |
| Open searchable command browser | `NVDA+Ctrl+Shift+Alt+Q` |
| Open keyboard shortcut quick help | `NVDA+Ctrl+Shift+Alt+F4` |

The settings dialog lets you choose the QuickInfo language, enable or disable individual commands, and assign additional shortcuts. Save your changes before closing the dialog.

The command browser lets you search commands and browse them by category. Disabled commands are omitted from the browser and announce that they are disabled if their shortcut is pressed.

## Default command shortcuts

All shortcuts below use NVDA, Ctrl, Shift and/or Alt as shown. The order in which you press the modifier keys does not matter.

| Shortcut | Command |
|---|---|
| `NVDA+Ctrl+Shift+F9` | Screen Curtain status |
| `NVDA+Ctrl+Shift+F7` | Caps Lock and Num Lock status |
| `NVDA+Ctrl+Shift+K` | Keyboard layout |
| `NVDA+Ctrl+Shift+C` | NVDA cursor information |
| `NVDA+Ctrl+Shift+F10` | Wi-Fi status and SSID |
| `NVDA+Ctrl+Shift+N` | Network connection type |
| `NVDA+Ctrl+Shift+F6` | Default audio output device |
| `NVDA+Ctrl+Shift+V` | Clipboard content type |
| `NVDA+Ctrl+Shift+O` | Computer name |
| `NVDA+Ctrl+Shift+Alt+O` | Copy computer name |
| `NVDA+Ctrl+Shift+Alt+N` | Network name |
| `NVDA+Ctrl+Shift+Alt+V` | Copy network name |
| `NVDA+Ctrl+Shift+Alt+I` | Internet connectivity |
| `NVDA+Ctrl+Shift+Alt+P` | Local IP address |
| `NVDA+Ctrl+Shift+Alt+D` | DNS servers |
| `NVDA+Ctrl+Shift+Alt+G` | Default gateway |
| `NVDA+Ctrl+Shift+Alt+B` | Battery information |
| `NVDA+Ctrl+Shift+Alt+U` | System uptime |
| `NVDA+Ctrl+Shift+Alt+W` | Windows edition, version and build |
| `NVDA+Ctrl+Shift+Alt+F5` | Alternate shortcut for Windows version |
| `NVDA+Ctrl+Shift+Alt+M` | Memory information |
| `NVDA+Ctrl+Shift+Alt+C` | Processor information |
| `NVDA+Ctrl+Shift+Alt+R` | Microphone endpoint |
| `NVDA+Ctrl+Shift+Alt+L` | Bluetooth information |
| `NVDA+Ctrl+Shift+Alt+E` | System drive information |
| `NVDA+Ctrl+Shift+Alt+S` | Quick system summary |
| `NVDA+Ctrl+Shift+Alt+H` | Hardware and BIOS information |
| `NVDA+Ctrl+Shift+Alt+Y` | Battery health |
| `NVDA+Ctrl+Shift+Alt+F8` | All storage drives |
| `NVDA+Ctrl+Shift+Alt+F` | Display and graphics information |
| `NVDA+Ctrl+Shift+Alt+T` | Network diagnostics |
| `NVDA+Ctrl+Shift+Alt+F11` | NVDA speech and braille information |
| `NVDA+Ctrl+Shift+Alt+X` | Focused-control details |
| `NVDA+Ctrl+Shift+Alt+A` | Installed NVDA add-ons |
| `NVDA+Ctrl+Shift+Alt+J` | Open applications |
| `NVDA+Ctrl+Shift+Alt+F12` | Output and microphone volume/mute status |
| `NVDA+Ctrl+Shift+Alt+F2` | Display scaling |
| `NVDA+Ctrl+Shift+Alt+F3` | Internet download/upload speed test |

## Notes and limitations

- Some hardware, battery, disk-health and firmware information depends on the computer manufacturer, firmware, drivers and Windows support. An unavailable value is not necessarily a fault with QuickInfo.
- Battery cycle count and wear estimates are reported only when the system exposes those values.
- Display scaling is read from the current user's Windows DPI setting. It may not represent different scaling values on multiple monitors.
- Network diagnostics depend on active adapters and network responses. Ping and packet-loss results can vary with firewalls, routers, Wi-Fi quality and network congestion.
- Internet speed-test results vary with server distance, network congestion and connection quality. The test transfers data and should be run only when wanted.
- Custom shortcuts are additional bindings. Avoid assigning a shortcut already used by NVDA, Windows or another add-on. Restart NVDA if a newly assigned shortcut does not take effect immediately.
- Device names and proper nouns may be spoken in the language returned by Windows, even when another QuickInfo language is selected.

## Compatibility and validation

- Add-on version: **3.0**
- Developed for NVDA on Windows.
- The package has passed Python syntax compilation and archive-integrity checks.
- It has been exercised by the user in an NVDA 2026.2 AMD64 environment, including the Windows version command. Other commands and configurations may still require testing on the individual system.
