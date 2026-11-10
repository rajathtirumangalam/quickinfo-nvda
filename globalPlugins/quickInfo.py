# -*- coding: UTF-8 -*-
# QuickInfo - accessible system information for NVDA
import globalPluginHandler
import ui
import api
import ctypes
import subprocess
import socket
import os
import threading
import json
import re
import nvwave
import config
import gui
import wx
from scriptHandler import script as _quickInfoScript

config.conf.spec["quickInfo"] = {
    "copyComputerName": "boolean(default=True)",
    "copyNetworkName": "boolean(default=True)",
    "language": "string(default='English')",
    "enabledCommands": "string(default='{}')",
    "customGestures": "string(default='{}')",
}

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
PS = os.path.join(os.environ.get("WINDIR", r"C:\Windows"),
                  "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def run_ps(script):
    return subprocess.check_output(
        [PS, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", script],
        stderr=subprocess.DEVNULL, universal_newlines=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).strip()


def computer_name():
    buf = ctypes.create_unicode_buffer(256)
    size = ctypes.c_ulong(len(buf))
    if kernel32.GetComputerNameW(buf, ctypes.byref(size)):
        return buf.value
    return socket.gethostname()


def network_details():
    # Windows interface type 71 = Wi-Fi; 6 = Ethernet.
    raw = run_ps(
        "Get-NetAdapter -Physical -ErrorAction Stop | "
        "Where-Object {$_.Status -eq 'Up'} | "
        "Select-Object Name,InterfaceDescription,ifIndex,NdisPhysicalMedium | ConvertTo-Json -Compress")
    if not raw:
        return None, tr("No active network connection"), None
    data = json.loads(raw)
    adapters = data if isinstance(data, list) else [data]
    def adapter_kind(adapter):
        label = (str(adapter.get("Name") or "") + " " +
                 str(adapter.get("InterfaceDescription") or "")).lower()
        medium = int(adapter.get("NdisPhysicalMedium") or 0)
        if medium in (1, 9) or any(token in label for token in ("wi-fi", "wifi", "wireless", "802.11")):
            return "wifi"
        if medium == 14 or any(token in label for token in ("ethernet", "gigabit", "gbe", "802.3")):
            return "ethernet"
        return "other"

    wifi = [a for a in adapters if adapter_kind(a) == "wifi"]
    eth = [a for a in adapters if adapter_kind(a) == "ethernet"]
    profile = ""
    try:
        profile = run_ps(
            "Get-NetConnectionProfile | Where-Object {$_.IPv4Connectivity -ne 'Disconnected' "
            "-or $_.IPv6Connectivity -ne 'Disconnected'} | Select-Object -First 1 -ExpandProperty Name")
    except Exception:
        pass
    if wifi:
        ssid = ""
        try:
            out = subprocess.check_output(["netsh", "wlan", "show", "interfaces"],
                stderr=subprocess.DEVNULL, universal_newlines=True, timeout=8,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            for line in out.splitlines():
                s = line.strip()
                if s.lower().startswith("ssid") and not s.lower().startswith("bssid"):
                    parts = s.split(":", 1)
                    if len(parts) == 2 and parts[1].strip():
                        ssid = parts[1].strip()
                        break
        except Exception:
            pass
        return ssid or profile or wifi[0].get("Name"), tr("Connected via Wi-Fi"), ssid
    if eth:
        return profile or eth[0].get("Name"), tr("Connected via Ethernet"), None
    return profile or adapters[0].get("Name"), tr("Connected through another network interface"), None



def wifi_details():
    """Return (SSID, signal percent, signal quality) from the active WLAN interface."""
    command = ["netsh", "wlan", "show", "interfaces"]
    output = subprocess.check_output(
        command,
        stderr=subprocess.DEVNULL,
        universal_newlines=True,
        timeout=8,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
    )
    blocks = re.split(r"(?im)^\s*Name\s*:", output)
    for block in blocks[1:]:
        state_match = re.search(r"(?im)^\s*State\s*:\s*(.+?)\s*$", block)
        state = state_match.group(1).strip().lower() if state_match else ""
        if state not in ("connected", "connected "):
            continue
        ssid_match = re.search(r"(?im)^\s*SSID\s*:\s*(.*?)\s*$", block)
        signal_match = re.search(r"(?im)^\s*Signal\s*:\s*(\d+)\s*%", block)
        ssid = ssid_match.group(1).strip() if ssid_match else ""
        signal = int(signal_match.group(1)) if signal_match else None
        if signal is None:
            quality = tr("signal strength unavailable")
        elif signal >= 80:
            quality = tr("excellent")
        elif signal >= 60:
            quality = tr("good")
        elif signal >= 40:
            quality = tr("fair")
        else:
            quality = tr("weak")
        return ssid or None, signal, quality
    return None, None, tr("not connected")

def default_audio_device():
    """Get the actual Windows default playback endpoint through Core Audio."""
    script = r"""
$ErrorActionPreference = 'Stop'
$source = @'
using System;
using System.Runtime.InteropServices;

[StructLayout(LayoutKind.Sequential)]
public struct PROPERTYKEY { public Guid fmtid; public int pid; }
[StructLayout(LayoutKind.Explicit, Size = 16)]
public struct PROPVARIANT {
    [FieldOffset(0)] public ushort vt;
    [FieldOffset(8)] public IntPtr pointerValue;
}
[ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDeviceEnumerator {
    [PreserveSig] int EnumAudioEndpoints(int dataFlow, int stateMask, out IntPtr devices);
    [PreserveSig] int GetDefaultAudioEndpoint(int dataFlow, int role, out IMMDevice device);
    [PreserveSig] int GetDevice([MarshalAs(UnmanagedType.LPWStr)] string id, out IMMDevice device);
    [PreserveSig] int RegisterEndpointNotificationCallback(IntPtr client);
    [PreserveSig] int UnregisterEndpointNotificationCallback(IntPtr client);
}
[ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IMMDevice {
    [PreserveSig] int Activate(ref Guid iid, int clsCtx, IntPtr activationParams, out IntPtr instance);
    [PreserveSig] int OpenPropertyStore(int access, out IPropertyStore properties);
    [PreserveSig] int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    [PreserveSig] int GetState(out int state);
}
[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IPropertyStore {
    [PreserveSig] int GetCount(out uint count);
    [PreserveSig] int GetAt(uint index, out PROPERTYKEY key);
    [PreserveSig] int GetValue(ref PROPERTYKEY key, out PROPVARIANT value);
    [PreserveSig] int SetValue(ref PROPERTYKEY key, ref PROPVARIANT value);
    [PreserveSig] int Commit();
}
public static class QuickInfoAudio {
    [DllImport("ole32.dll")] private static extern int PropVariantClear(ref PROPVARIANT value);
    public static string GetDefaultPlaybackName() {
        IMMDeviceEnumerator enumerator = null; IMMDevice device = null; IPropertyStore store = null;
        PROPVARIANT value = new PROPVARIANT();
        try {
            Type t = Type.GetTypeFromCLSID(new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E"));
            enumerator = (IMMDeviceEnumerator)Activator.CreateInstance(t);
            int hr = enumerator.GetDefaultAudioEndpoint(0, 0, out device); // render, console
            if (hr != 0 || device == null) {
                hr = enumerator.GetDefaultAudioEndpoint(0, 1, out device); // render, multimedia
            }
            if (hr != 0 || device == null) {
                hr = enumerator.GetDefaultAudioEndpoint(0, 2, out device); // render, communications
            }
            if (hr != 0 || device == null)
                Marshal.ThrowExceptionForHR(hr != 0 ? hr : unchecked((int)0x80070490));
            hr = device.OpenPropertyStore(0, out store);
            if (hr != 0 || store == null) Marshal.ThrowExceptionForHR(hr != 0 ? hr : unchecked((int)0x80004005));
            PROPERTYKEY key = new PROPERTYKEY();
            key.fmtid = new Guid("A45C254E-DF1C-4EFD-8020-67D146A850E0"); key.pid = 14;
            hr = store.GetValue(ref key, out value);
            if (hr != 0) Marshal.ThrowExceptionForHR(hr);
            if ((value.vt == 31 || value.vt == 8) && value.pointerValue != IntPtr.Zero)
                return Marshal.PtrToStringUni(value.pointerValue);
            return "";
        } finally {
            try { PropVariantClear(ref value); } catch {}
            if (store != null && Marshal.IsComObject(store)) Marshal.ReleaseComObject(store);
            if (device != null && Marshal.IsComObject(device)) Marshal.ReleaseComObject(device);
            if (enumerator != null && Marshal.IsComObject(enumerator)) Marshal.ReleaseComObject(enumerator);
        }
    }
}
'@
Add-Type -TypeDefinition $source -ErrorAction Stop
$name = [QuickInfoAudio]::GetDefaultPlaybackName()
if ([string]::IsNullOrWhiteSpace($name)) { throw 'The default playback endpoint returned no friendly name.' }
[Console]::Write($name)
"""
    result = run_ps(script)
    return result.strip() if result else None


def system_data():
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$os = Get-CimInstance Win32_OperatingSystem
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cs = Get-CimInstance Win32_ComputerSystem
$disk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$env:SystemDrive'"
$bat = Get-CimInstance Win32_Battery | Select-Object -First 1
$ipcfg = Get-NetIPConfiguration | Where-Object {$_.NetAdapter.Status -eq 'Up'} | Select-Object -First 1
$profile = Get-NetConnectionProfile | Where-Object {$_.IPv4Connectivity -ne 'Disconnected' -or $_.IPv6Connectivity -ne 'Disconnected'} | Select-Object -First 1
$ip4=@(); $ip6=@(); $dns=@(); $gateway=@()
if ($ipcfg) {
 $ip4=@($ipcfg.IPv4Address | ForEach-Object {$_.IPAddress})
 $ip6=@($ipcfg.IPv6Address | ForEach-Object {$_.IPAddress})
 $dns=@($ipcfg.DNSServer.ServerAddresses)
 $gateway=@($ipcfg.IPv4DefaultGateway | ForEach-Object {$_.NextHop})
}
$adapter=Get-NetAdapter -Physical | Where-Object {$_.Status -eq 'Up'} | Select-Object -First 1
$kind='Other'
if ($adapter) {
 $desc=($adapter.Name+' '+$adapter.InterfaceDescription).ToLowerInvariant()
 if ($desc -match 'wi.?fi|wireless|802.11' -or $adapter.NdisPhysicalMedium -in @(1,9)) {$kind='Wi-Fi'}
 elseif ($desc -match 'ethernet|gigabit|gbe|802.3' -or $adapter.NdisPhysicalMedium -eq 14) {$kind='Ethernet'}
}
$internet=$false
try {
 $client=New-Object System.Net.Sockets.TcpClient
 $ar=$client.BeginConnect('1.1.1.1',443,$null,$null)
 $internet=$ar.AsyncWaitHandle.WaitOne(2200,$false) -and $client.Connected
 $client.Close()
} catch {$internet=$false}
$battery=$null
if ($bat) {$battery=[ordered]@{percent=$bat.EstimatedChargeRemaining;status=$bat.BatteryStatus;minutes=$bat.EstimatedRunTime}}
$up=(Get-Date)-$os.LastBootUpTime
[ordered]@{
 windows=$os.Caption;version=$os.Version;build=$os.BuildNumber
 cpu=$cpu.Name;cpuLoad=$cpu.LoadPercentage
 ramTotalGB=[math]::Round($cs.TotalPhysicalMemory/1GB,1)
 ramFreeGB=[math]::Round($os.FreePhysicalMemory/1MB,1)
 diskFreeGB=if($disk){[math]::Round($disk.FreeSpace/1GB,1)}else{$null}
 diskSizeGB=if($disk){[math]::Round($disk.Size/1GB,1)}else{$null}
 uptimeDays=$up.Days;uptimeHours=$up.Hours;uptimeMinutes=$up.Minutes
 battery=$battery;connection=$kind;profile=if($profile){$profile.Name}else{$null}
 internet=$internet;ipv4=$ip4;ipv6=$ip6;dns=$dns;gateway=$gateway
} | ConvertTo-Json -Depth 5 -Compress
"""
    return json.loads(run_ps(script))


def microphone_device():
    try:
        raw = run_ps(
            "Get-PnpDevice -Class AudioEndpoint -ErrorAction Stop | "
            "Where-Object {$_.Status -eq 'OK' -and $_.FriendlyName -match 'Microphone|Mic'} | "
            "Select-Object -First 1 -ExpandProperty FriendlyName")
        return raw or None
    except Exception:
        return None


def bluetooth_devices():
    try:
        raw = run_ps(
            "Get-PnpDevice -Class Bluetooth -ErrorAction Stop | "
            "Where-Object {$_.Status -eq 'OK'} | Select-Object -ExpandProperty FriendlyName | "
            "ConvertTo-Json -Compress")
        if not raw:
            return []
        value = json.loads(raw)
        return value if isinstance(value, list) else [value]
    except Exception:
        return []


def hardware_details():
    script = r"""
$ErrorActionPreference='SilentlyContinue'
$cs=Get-CimInstance Win32_ComputerSystem;$bios=Get-CimInstance Win32_BIOS
$board=Get-CimInstance Win32_BaseBoard | Select-Object -First 1
$ram=@(Get-CimInstance Win32_PhysicalMemory);$slots=@(Get-CimInstance Win32_PhysicalMemoryArray);$cpu=@(Get-CimInstance Win32_Processor)
[ordered]@{manufacturer=$cs.Manufacturer;model=$cs.Model;systemType=$cs.SystemType;bios=$bios.SMBIOSBIOSVersion;biosDate=if($bios.ReleaseDate){$bios.ReleaseDate.ToString('yyyy-MM-dd')}else{$null};board=if($board){($board.Manufacturer+' '+$board.Product).Trim()}else{$null};ramModules=$ram.Count;ramSlots=if($slots){($slots|Measure-Object -Property MemoryDevices -Sum).Sum}else{$null};ramSpeed=if($ram){($ram|Select-Object -ExpandProperty Speed -Unique)-join ', '}else{$null};cpuCores=($cpu|Measure-Object -Property NumberOfCores -Sum).Sum;cpuLogical=($cpu|Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum} | ConvertTo-Json -Compress
"""
    return json.loads(run_ps(script))


def battery_health_details():
    script = r"""
$ErrorActionPreference='SilentlyContinue'
$s=Get-CimInstance -Namespace root/wmi -ClassName BatteryStaticData|Select-Object -First 1
$f=Get-CimInstance -Namespace root/wmi -ClassName BatteryFullChargedCapacity|Select-Object -First 1
$c=Get-CimInstance -Namespace root/wmi -ClassName BatteryCycleCount|Select-Object -First 1
$d=if($s){[double]$s.DesignedCapacity}else{0};$v=if($f){[double]$f.FullChargedCapacity}else{0};$w=$null
if($d -gt 0 -and $v -gt 0){$w=[math]::Round((1-($v/$d))*100,1)}
[ordered]@{designCapacity=$d;fullCapacity=$v;wearPercent=$w;cycles=if($c){$c.CycleCount}else{$null}}|ConvertTo-Json -Compress
"""
    return json.loads(run_ps(script))


def all_drive_details():
    script = r"""
$ErrorActionPreference='SilentlyContinue'
$v=@(Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3'|ForEach-Object {[ordered]@{name=$_.DeviceID;label=$_.VolumeName;sizeGB=[math]::Round($_.Size/1GB,1);freeGB=[math]::Round($_.FreeSpace/1GB,1)}})
$p=@();if(Get-Command Get-PhysicalDisk -ErrorAction SilentlyContinue){$p=@(Get-PhysicalDisk|ForEach-Object {[ordered]@{name=$_.FriendlyName;media=$_.MediaType;health=$_.HealthStatus;sizeGB=[math]::Round($_.Size/1GB,1)}})}
[ordered]@{volumes=$v;physical=$p}|ConvertTo-Json -Depth 4 -Compress
"""
    return json.loads(run_ps(script))


def display_details():
    script = r"""
$ErrorActionPreference='SilentlyContinue'
$g=@(Get-CimInstance Win32_VideoController|ForEach-Object {[ordered]@{name=$_.Name;width=$_.CurrentHorizontalResolution;height=$_.CurrentVerticalResolution;refresh=$_.CurrentRefreshRate;driver=$_.DriverVersion}})
[ordered]@{graphics=$g}|ConvertTo-Json -Depth 4 -Compress
"""
    return json.loads(run_ps(script))


def network_diagnostics():
    script = r"""
$ErrorActionPreference='SilentlyContinue'
$a=@(Get-NetAdapter -Physical|Where-Object {$_.Status -eq 'Up'}|ForEach-Object {[ordered]@{name=$_.Name;linkSpeed=[string]$_.LinkSpeed}})
$p=Test-Connection -ComputerName 1.1.1.1 -Count 4 -ErrorAction SilentlyContinue;$n=@($p).Count;$avg=$null
if($n){$avg=[math]::Round((($p|Measure-Object -Property ResponseTime -Average).Average),1)}
[ordered]@{adapters=$a;sent=4;received=$n;lost=(4-$n);averageMs=$avg}|ConvertTo-Json -Depth 4 -Compress
"""
    return json.loads(run_ps(script))


def display_scaling_details():
    script = r"""
$ErrorActionPreference='SilentlyContinue'
$d=Get-ItemProperty 'HKCU:\Control Panel\Desktop'
$p=$null
if($d.LogPixels -and [int]$d.LogPixels -gt 0){$p=[math]::Round(([double]$d.LogPixels/96)*100)}
[ordered]@{scalingPercent=$p;logPixels=$d.LogPixels}|ConvertTo-Json -Compress
"""
    return json.loads(run_ps(script))


def audio_status_details():
    script = r"""
$ErrorActionPreference='Stop'
$source=@'
using System;
using System.Runtime.InteropServices;
[ComImport,Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IQIEnum { [PreserveSig]int EnumAudioEndpoints(int f,int s,out IntPtr d); [PreserveSig]int GetDefaultAudioEndpoint(int f,int r,out IQIDevice d); [PreserveSig]int GetDevice([MarshalAs(UnmanagedType.LPWStr)]string id,out IQIDevice d); [PreserveSig]int RegisterEndpointNotificationCallback(IntPtr c); [PreserveSig]int UnregisterEndpointNotificationCallback(IntPtr c); }
[ComImport,Guid("D666063F-1587-4E43-81F1-B948E807363F"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IQIDevice { [PreserveSig]int Activate(ref Guid iid,int ctx,IntPtr p,out IntPtr o); [PreserveSig]int OpenPropertyStore(int a,out IntPtr p); [PreserveSig]int GetId([MarshalAs(UnmanagedType.LPWStr)]out string id); [PreserveSig]int GetState(out int s); }
[ComImport,Guid("5CDF2C82-841E-4546-9722-0CF74078229A"),InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IQIVolume {
 [PreserveSig]int RegisterControlChangeNotify(IntPtr n); [PreserveSig]int UnregisterControlChangeNotify(IntPtr n); [PreserveSig]int GetChannelCount(out uint c);
 [PreserveSig]int SetMasterVolumeLevel(float v,Guid c); [PreserveSig]int SetMasterVolumeLevelScalar(float v,Guid c); [PreserveSig]int GetMasterVolumeLevel(out float v); [PreserveSig]int GetMasterVolumeLevelScalar(out float v);
 [PreserveSig]int SetChannelVolumeLevel(uint c,float v,Guid x); [PreserveSig]int SetChannelVolumeLevelScalar(uint c,float v,Guid x); [PreserveSig]int GetChannelVolumeLevel(uint c,out float v); [PreserveSig]int GetChannelVolumeLevelScalar(uint c,out float v);
 [PreserveSig]int SetMute(bool m,Guid c); [PreserveSig]int GetMute(out bool m); [PreserveSig]int GetVolumeStepInfo(out uint s,out uint n); [PreserveSig]int VolumeStepUp(Guid c); [PreserveSig]int VolumeStepDown(Guid c); [PreserveSig]int QueryHardwareSupport(out uint m); [PreserveSig]int GetVolumeRange(out float min,out float max,out float inc);
}
public class QIAudio {
 public static string Get(int flow){ object en=null; IQIDevice dev=null; object volObj=null;
  try { var t=Type.GetTypeFromCLSID(new Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")); en=Activator.CreateInstance(t); var e=(IQIEnum)en; int hr=e.GetDefaultAudioEndpoint(flow,0,out dev); if(hr!=0||dev==null)return "";
   Guid iid=new Guid("5CDF2C82-841E-4546-9722-0CF74078229A"); IntPtr ptr; hr=dev.Activate(ref iid,23,IntPtr.Zero,out ptr); if(hr!=0)return "";
   volObj=Marshal.GetObjectForIUnknown(ptr); Marshal.Release(ptr); var v=(IQIVolume)volObj; float level=0; bool mute=false; v.GetMasterVolumeLevelScalar(out level); v.GetMute(out mute);
   return ((int)Math.Round(level*100)).ToString()+"|"+(mute?"true":"false");
  } catch{return "";} finally {if(volObj!=null&&Marshal.IsComObject(volObj))Marshal.ReleaseComObject(volObj); if(dev!=null&&Marshal.IsComObject(dev))Marshal.ReleaseComObject(dev); if(en!=null&&Marshal.IsComObject(en))Marshal.ReleaseComObject(en);}
 }
}
'@
Add-Type -TypeDefinition $source -ErrorAction Stop
$o=[QIAudio]::Get(0);$m=[QIAudio]::Get(1)
[ordered]@{output=$o;microphone=$m}|ConvertTo-Json -Compress
"""
    return json.loads(run_ps(script))


def internet_speed_test():
    """Small user-initiated Cloudflare test: about 3 MB down and 0.5 MB up."""
    import time
    import urllib.request
    results = {}
    start = time.monotonic()
    request = urllib.request.Request(
        "https://speed.cloudflare.com/__down?bytes=3000000",
        headers={"User-Agent": "QuickInfo-NVDA/3.3"})
    with urllib.request.urlopen(request, timeout=20) as response:
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
    elapsed = max(time.monotonic() - start, 0.001)
    results["downloadMbps"] = round((total * 8 / elapsed) / 1_000_000, 2)
    payload = os.urandom(500000)
    start = time.monotonic()
    request = urllib.request.Request(
        "https://speed.cloudflare.com/__up", data=payload, method="POST",
        headers={"Content-Type": "application/octet-stream", "User-Agent": "QuickInfo-NVDA/3.3"})
    with urllib.request.urlopen(request, timeout=20) as response:
        response.read(1024)
    elapsed = max(time.monotonic() - start, 0.001)
    results["uploadMbps"] = round((len(payload) * 8 / elapsed) / 1_000_000, 2)
    return results


def _read_json_setting(key):
    try:
        value = config.conf.get("quickInfo", {}).get(key, "{}")
        result = json.loads(value) if isinstance(value, str) else value
        return result if isinstance(result, dict) else {}
    except Exception:
        return {}


def _normalize_gesture(value):
    value = (value or "").strip()
    if not value:
        return ""
    parts = [p.strip().lower() for p in value.replace(" ", "").split("+") if p.strip()]
    aliases = {"ctrl": "control", "win": "windows", "cmd": "windows"}
    parts = [aliases.get(p, p) for p in parts]
    allowed = {"nvda", "control", "shift", "alt", "windows"}
    keys = [p for p in parts if p not in allowed]
    if not keys or len(keys) != 1 or len(set(parts)) != len(parts):
        raise ValueError("Enter a shortcut such as NVDA+Ctrl+Shift+F12.")
    if not any(p in allowed for p in parts):
        raise ValueError("A custom shortcut must include NVDA, Ctrl, Alt or Shift.")
    return "+".join(parts)


def command_is_enabled(method):
    return _read_json_setting("enabledCommands").get(method, True) is not False


def nvda_speech_details():
    result=[]
    try:
        import synthDriverHandler
        synth=synthDriverHandler.getSynth()
        if synth:
            result.append("Synthesizer "+str(getattr(synth,"name","unknown")))
            voice=getattr(synth,"voice",None)
            if voice: result.append("Voice "+str(voice))
            for attr, label in (("rate", "Speech rate "), ("pitch", "Speech pitch "), ("volume", "Speech volume ")):
                value = getattr(synth, attr, None)
                if value is not None:
                    result.append(label + str(value))
    except Exception: pass
    try:
        speech_config = config.conf.get("speech", {})
        for key, label in (("rate", "Configured speech rate "), ("pitch", "Configured speech pitch "), ("volume", "Configured speech volume ")):
            value = speech_config.get(key)
            if value is not None and not any(x.startswith(label) for x in result):
                result.append(label + str(value))
    except Exception: pass
    return ", ".join(result) if result else None


def focus_details():
    obj=api.getFocusObject()
    if not obj: return None
    fields=[]
    for attr,label in (("name","Name"),("role","Role"),("value","Value"),("description","Description")):
        try:
            value=getattr(obj,attr,None)
            if value: fields.append(label+" "+str(value))
        except Exception: pass
    try:
        states=getattr(obj,"states",None)
        if states: fields.append("States "+", ".join(str(x) for x in states))
    except Exception: pass
    try:
        app=getattr(obj,"appModule",None)
        if app: fields.append("Application "+str(getattr(app,"appName","unknown")))
    except Exception: pass
    return ". ".join(fields) if fields else None


def addon_inventory():
    """Return installed NVDA add-ons, including disabled add-ons when exposed by NVDA."""
    import addonHandler

    def manifest_value(manifest, key):
        try:
            if hasattr(manifest, "get"):
                return manifest.get(key)
            return None
        except Exception:
            return None

    result = []
    addons = addonHandler.getAvailableAddons()
    for addon in addons:
        manifest = getattr(addon, "manifest", None)
        name = (getattr(addon, "name", None)
                or manifest_value(manifest, "summary")
                or manifest_value(manifest, "name"))
        version = (getattr(addon, "version", None)
                   or manifest_value(manifest, "version"))
        disabled = getattr(addon, "isDisabled", False)
        if callable(disabled):
            try:
                disabled = disabled()
            except Exception:
                disabled = False
        if not name:
            continue
        line = str(name)
        if version:
            line += " version " + str(version)
        line += ", " + ("disabled" if disabled else "enabled")
        result.append(line)

    return sorted(result, key=lambda item: item.casefold())


def running_applications():
    """Enumerate visible top-level Windows windows without relying on PowerShell."""
    titles = []
    seen = set()
    EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_ssize_t)
    GW_OWNER = 4
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
    user32.IsWindowVisible.restype = ctypes.c_bool
    user32.GetWindow.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    user32.GetWindow.restype = ctypes.c_void_p
    user32.GetWindowTextLengthW.argtypes = [ctypes.c_void_p]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    user32.GetWindowThreadProcessId.restype = ctypes.c_ulong
    user32.EnumWindows.argtypes = [EnumWindowsProc, ctypes.c_ssize_t]
    user32.EnumWindows.restype = ctypes.c_bool
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_bool, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.QueryFullProcessImageNameW.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_ulong)
    ]
    kernel32.QueryFullProcessImageNameW.restype = ctypes.c_bool
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_bool

    def process_name(hwnd):
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return ""
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            return ""
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return os.path.basename(buffer.value)
        except Exception:
            pass
        finally:
            try:
                kernel32.CloseHandle(handle)
            except Exception:
                pass
        return ""

    def callback(hwnd, lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetWindow(hwnd, GW_OWNER):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length <= 0:
                return True
            buffer = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buffer, length + 1)
            title = buffer.value.strip()
            if not title:
                return True
            exe = process_name(hwnd)
            item = title + (" (" + exe + ")" if exe else "")
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                titles.append(item)
        except Exception:
            pass
        return True

    callback_ref = EnumWindowsProc(callback)
    if not user32.EnumWindows(callback_ref, 0):
        return []
    return sorted(titles, key=lambda item: item.casefold())


def windows_version_details():
    """Read Windows edition and build directly from the documented CurrentVersion registry key."""
    import winreg

    key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
    access = winreg.KEY_READ
    if hasattr(winreg, "KEY_WOW64_64KEY"):
        access |= winreg.KEY_WOW64_64KEY
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path, 0, access) as key:
        def read_value(name):
            try:
                return winreg.QueryValueEx(key, name)[0]
            except OSError:
                return None

        product = read_value("ProductName") or "Windows"
        edition = read_value("EditionID")
        display_version = read_value("DisplayVersion") or read_value("ReleaseId")
        build = read_value("CurrentBuildNumber") or read_value("CurrentBuild")
        revision = read_value("UBR")

    # Some Windows 11 installations retain a legacy ProductName value.
    # The build number is the reliable discriminator.
    try:
        if int(build) >= 22000 and "Windows 11" not in product:
            product = product.replace("Windows 10", "Windows 11")
    except (TypeError, ValueError):
        pass

    parts = [str(product)]
    if edition and str(edition).casefold() not in str(product).casefold():
        parts[0] += " " + str(edition)
    if display_version:
        parts.append("version " + str(display_version))
    if build:
        build_text = str(build)
        if revision is not None:
            build_text += "." + str(revision)
        parts.append("build " + build_text)
    return ", ".join(parts)


def copy_text(value, label):
    if not value:
        announce("Unable to determine " + label)
        return
    try:
        if not wx.TheClipboard.Open():
            announce("Clipboard unavailable")
            return
        try:
            wx.TheClipboard.SetData(wx.TextDataObject(str(value)))
        finally:
            wx.TheClipboard.Close()
        announce(label.capitalize() + " copied to clipboard")
    except Exception:
        announce("Unable to copy " + label + " to clipboard")


LANGUAGES = ["English", "Hindi", "Kannada", "Tamil", "Telugu", "Malayalam"]
UI_TEXT = {
    "English": {},
    "Hindi": {
        "QuickInfo": "क्विक इन्फो", "Available commands": "उपलब्ध कमांड",
        "Open settings": "सेटिंग खोलें", "Close": "बंद करें",
        "QuickInfo Settings": "क्विक इन्फो सेटिंग", "Language for QuickInfo": "क्विक इन्फो की भाषा",
        "Copy computer name command": "कंप्यूटर नाम कॉपी करने की कमांड",
        "Copy network name command": "नेटवर्क नाम कॉपी करने की कमांड",
        "Enabled": "चालू", "Disabled": "बंद", "Save": "सहेजें", "Cancel": "रद्द करें",
        "Use Up and Down arrows to choose a command, then press Enter.": "कमांड चुनने के लिए ऊपर और नीचे तीर दबाएँ, फिर एंटर दबाएँ।",
        "Use Tab and arrow keys to change options.": "विकल्प बदलने के लिए टैब और तीर कुंजियों का उपयोग करें।",
        "Settings saved": "सेटिंग सहेजी गई",
    },
    "Kannada": {
        "QuickInfo": "ಕ್ವಿಕ್ ಇನ್ಫೋ", "Available commands": "ಲಭ್ಯವಿರುವ ಕಮಾಂಡ್‌ಗಳು",
        "Open settings": "ಸೆಟ್ಟಿಂಗ್‌ಗಳನ್ನು ತೆರೆಯಿರಿ", "Close": "ಮುಚ್ಚಿ",
        "QuickInfo Settings": "ಕ್ವಿಕ್ ಇನ್ಫೋ ಸೆಟ್ಟಿಂಗ್‌ಗಳು", "Language for QuickInfo": "ಕ್ವಿಕ್ ಇನ್ಫೋ ಭಾಷೆ",
        "Copy computer name command": "ಕಂಪ್ಯೂಟರ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್",
        "Copy network name command": "ನೆಟ್‌ವರ್ಕ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್",
        "Enabled": "ಸಕ್ರಿಯ", "Disabled": "ನಿಷ್ಕ್ರಿಯ", "Save": "ಉಳಿಸಿ", "Cancel": "ರದ್ದುಮಾಡಿ",
        "Use Up and Down arrows to choose a command, then press Enter.": "ಕಮಾಂಡ್ ಆಯ್ಕೆ ಮಾಡಲು ಮೇಲಿನ ಮತ್ತು ಕೆಳಗಿನ ಬಾಣದ ಕೀಲಿಗಳನ್ನು ಒತ್ತಿ, ನಂತರ Enter ಒತ್ತಿ.",
        "Use Tab and arrow keys to change options.": "ಆಯ್ಕೆಗಳನ್ನು ಬದಲಾಯಿಸಲು Tab ಮತ್ತು ಬಾಣದ ಕೀಲಿಗಳನ್ನು ಬಳಸಿ.",
        "Settings saved": "ಸೆಟ್ಟಿಂಗ್‌ಗಳನ್ನು ಉಳಿಸಲಾಗಿದೆ",
    },
    "Tamil": {
        "QuickInfo": "விரைவு தகவல்", "Available commands": "கிடைக்கும் கட்டளைகள்",
        "Open settings": "அமைப்புகளைத் திற", "Close": "மூடு",
        "QuickInfo Settings": "விரைவு தகவல் அமைப்புகள்", "Language for QuickInfo": "விரைவு தகவலின் மொழி",
        "Copy computer name command": "கணினி பெயரை நகலெடுக்கும் கட்டளை",
        "Copy network name command": "பிணையப் பெயரை நகலெடுக்கும் கட்டளை",
        "Enabled": "இயக்கு", "Disabled": "முடக்கு", "Save": "சேமி", "Cancel": "ரத்து",
        "Use Up and Down arrows to choose a command, then press Enter.": "கட்டளையைத் தேர்ந்தெடுக்க மேல் மற்றும் கீழ் அம்புக்குறிகளை அழுத்தி, பின்னர் Enter அழுத்தவும்.",
        "Use Tab and arrow keys to change options.": "விருப்பங்களை மாற்ற Tab மற்றும் அம்புக்குறிகளைப் பயன்படுத்தவும்.",
        "Settings saved": "அமைப்புகள் சேமிக்கப்பட்டன",
    },
    "Telugu": {
        "QuickInfo": "త్వరిత సమాచారం", "Available commands": "అందుబాటులో ఉన్న ఆదేశాలు",
        "Open settings": "సెట్టింగ్‌లను తెరవండి", "Close": "మూసివేయండి",
        "QuickInfo Settings": "త్వరిత సమాచారం సెట్టింగ్‌లు", "Language for QuickInfo": "త్వరిత సమాచారం భాష",
        "Copy computer name command": "కంప్యూటర్ పేరును కాపీ చేసే ఆదేశం",
        "Copy network name command": "నెట్‌వర్క్ పేరును కాపీ చేసే ఆదేశం",
        "Enabled": "ప్రారంభించబడింది", "Disabled": "నిలిపివేయబడింది", "Save": "సేవ్ చేయండి", "Cancel": "రద్దు చేయండి",
        "Use Up and Down arrows to choose a command, then press Enter.": "ఆదేశాన్ని ఎంచుకోవడానికి పైకి, కిందికి బాణం కీలను నొక్కి, తర్వాత Enter నొక్కండి.",
        "Use Tab and arrow keys to change options.": "ఎంపికలను మార్చడానికి Tab మరియు బాణం కీలను ఉపయోగించండి.",
        "Settings saved": "సెట్టింగ్‌లు సేవ్ అయ్యాయి",
    },
    "Malayalam": {
        "QuickInfo": "ക്വിക് ഇൻഫോ", "Available commands": "ലഭ്യമായ കമാൻഡുകൾ",
        "Open settings": "ക്രമീകരണങ്ങൾ തുറക്കുക", "Close": "അടയ്ക്കുക",
        "QuickInfo Settings": "ക്വിക് ഇൻഫോ ക്രമീകരണങ്ങൾ", "Language for QuickInfo": "ക്വിക് ഇൻഫോ ഭാഷ",
        "Copy computer name command": "കമ്പ്യൂട്ടർ പേര് പകർത്താനുള്ള കമാൻഡ്",
        "Copy network name command": "നെറ്റ്‌വർക്ക് പേര് പകർത്താനുള്ള കമാൻഡ്",
        "Enabled": "പ്രവർത്തനക്ഷമം", "Disabled": "പ്രവർത്തനരഹിതം", "Save": "സംരക്ഷിക്കുക", "Cancel": "റദ്ദാക്കുക",
        "Use Up and Down arrows to choose a command, then press Enter.": "കമാൻഡ് തിരഞ്ഞെടുക്കാൻ മുകളിലേക്കും താഴേക്കുമുള്ള അമ്പുകീകൾ അമർത്തി Enter അമർത്തുക.",
        "Use Tab and arrow keys to change options.": "ഓപ്ഷനുകൾ മാറ്റാൻ Tab, അമ്പുകീകൾ ഉപയോഗിക്കുക.",
        "Settings saved": "ക്രമീകരണങ്ങൾ സംരക്ഷിച്ചു",
    },
}

# QuickInfo 4.0 command labels
UI_TEXT["Hindi"] .update({'Hardware and BIOS information': 'हार्डवेयर और BIOS जानकारी', 'Battery health information': 'बैटरी स्वास्थ्य जानकारी', 'All storage drives': 'सभी स्टोरेज ड्राइव', 'Display and graphics information': 'डिस्प्ले और ग्राफिक्स जानकारी', 'Network diagnostics': 'नेटवर्क निदान', 'NVDA speech information': 'NVDA वाक् जानकारी', 'Current focus details': 'वर्तमान फ़ोकस विवरण', 'Installed NVDA add-ons': 'स्थापित NVDA ऐड-ऑन', 'Open applications': 'खुले एप्लिकेशन', 'Search commands': 'कमांड खोजें'})
UI_TEXT["Kannada"] .update({'Hardware and BIOS information': 'ಹಾರ್ಡ್\u200cವೇರ್ ಮತ್ತು BIOS ಮಾಹಿತಿ', 'Battery health information': 'ಬ್ಯಾಟರಿ ಆರೋಗ್ಯ ಮಾಹಿತಿ', 'All storage drives': 'ಎಲ್ಲಾ ಸಂಗ್ರಹ ಡ್ರೈವ್\u200cಗಳು', 'Display and graphics information': 'ಪ್ರದರ್ಶನ ಮತ್ತು ಗ್ರಾಫಿಕ್ಸ್ ಮಾಹಿತಿ', 'Network diagnostics': 'ನೆಟ್\u200cವರ್ಕ್ ಪರಿಶೀಲನೆ', 'NVDA speech information': 'NVDA ಮಾತಿನ ಮಾಹಿತಿ', 'Current focus details': 'ಪ್ರಸ್ತುತ ಫೋಕಸ್ ವಿವರಗಳು', 'Installed NVDA add-ons': 'ಸ್ಥಾಪಿತ NVDA ಆಡ್-ಆನ್\u200cಗಳು', 'Open applications': 'ತೆರೆದ ಅಪ್ಲಿಕೇಶನ್\u200cಗಳು', 'Search commands': 'ಕಮಾಂಡ್\u200cಗಳನ್ನು ಹುಡುಕಿ'})
UI_TEXT["Tamil"] .update({'Hardware and BIOS information': 'வன்பொருள் மற்றும் BIOS தகவல்', 'Battery health information': 'மின்கலன் நிலை தகவல்', 'All storage drives': 'அனைத்து சேமிப்பக இயக்கிகள்', 'Display and graphics information': 'காட்சி மற்றும் கிராபிக்ஸ் தகவல்', 'Network diagnostics': 'பிணைய கண்டறிதல்', 'NVDA speech information': 'NVDA பேச்சு தகவல்', 'Current focus details': 'தற்போதைய கவனக் கூறு விவரங்கள்', 'Installed NVDA add-ons': 'நிறுவப்பட்ட NVDA துணை நிரல்கள்', 'Open applications': 'திறந்த பயன்பாடுகள்', 'Search commands': 'கட்டளைகளைத் தேடு'})
UI_TEXT["Telugu"] .update({'Hardware and BIOS information': 'హార్డ్\u200cవేర్ మరియు BIOS సమాచారం', 'Battery health information': 'బ్యాటరీ ఆరోగ్య సమాచారం', 'All storage drives': 'అన్ని నిల్వ డ్రైవ్\u200cలు', 'Display and graphics information': 'డిస్\u200cప్లే మరియు గ్రాఫిక్స్ సమాచారం', 'Network diagnostics': 'నెట్\u200cవర్క్ నిర్ధారణ', 'NVDA speech information': 'NVDA ప్రసంగ సమాచారం', 'Current focus details': 'ప్రస్తుత ఫోకస్ వివరాలు', 'Installed NVDA add-ons': 'ఇన్\u200cస్టాల్ చేసిన NVDA యాడ్-ఆన్\u200cలు', 'Open applications': 'తెరిచిన అప్లికేషన్\u200cలు', 'Search commands': 'ఆదేశాలను వెతకండి'})
UI_TEXT["Malayalam"] .update({'Hardware and BIOS information': 'ഹാർഡ്\u200cവെയർ, BIOS വിവരങ്ങൾ', 'Battery health information': 'ബാറ്ററി ആരോഗ്യവിവരം', 'All storage drives': 'എല്ലാ സ്റ്റോറേജ് ഡ്രൈവുകളും', 'Display and graphics information': 'ഡിസ്\u200cപ്ലേ, ഗ്രാഫിക്സ് വിവരങ്ങൾ', 'Network diagnostics': 'നെറ്റ്\u200cവർക്ക് പരിശോധന', 'NVDA speech information': 'NVDA സംഭാഷണ വിവരം', 'Current focus details': 'നിലവിലെ ഫോക്കസ് വിവരങ്ങൾ', 'Installed NVDA add-ons': 'ഇൻസ്റ്റാൾ ചെയ്ത NVDA ആഡ്-ഓണുകൾ', 'Open applications': 'തുറന്ന ആപ്ലിക്കേഷനുകൾ', 'Search commands': 'കമാൻഡുകൾ തിരയുക'})


for _lang, _values in {
    "Hindi": {"Command to configure":"कॉन्फ़िगर करने के लिए कमांड", "Command status":"कमांड की स्थिति", "Additional keyboard shortcut":"अतिरिक्त कीबोर्ड शॉर्टकट", "Command category":"कमांड श्रेणी", "All":"सभी", "System":"सिस्टम", "Network":"नेटवर्क", "Audio":"ऑडियो", "Accessibility":"सुलभता", "NVDA":"NVDA", "Leave the shortcut blank to keep the default shortcut only. Custom shortcuts are additional bindings.":"केवल डिफ़ॉल्ट शॉर्टकट रखने के लिए इसे खाली छोड़ें। कस्टम शॉर्टकट अतिरिक्त रूप से काम करेंगे."},
    "Kannada": {"Command to configure":"ಸಂರಚಿಸಬೇಕಾದ ಕಮಾಂಡ್", "Command status":"ಕಮಾಂಡ್ ಸ್ಥಿತಿ", "Additional keyboard shortcut":"ಹೆಚ್ಚುವರಿ ಕೀಬೋರ್ಡ್ ಶಾರ್ಟ್‌ಕಟ್", "Command category":"ಕಮಾಂಡ್ ವರ್ಗ", "All":"ಎಲ್ಲಾ", "System":"ಸಿಸ್ಟಮ್", "Network":"ನೆಟ್‌ವರ್ಕ್", "Audio":"ಆಡಿಯೋ", "Accessibility":"ಪ್ರವೇಶಸಾಧ್ಯತೆ", "NVDA":"NVDA", "Leave the shortcut blank to keep the default shortcut only. Custom shortcuts are additional bindings.":"ಡೀಫಾಲ್ಟ್ ಶಾರ್ಟ್‌ಕಟ್ ಮಾತ್ರ ಇರಿಸಲು ಖಾಲಿ ಬಿಡಿ. ಕಸ್ಟಮ್ ಶಾರ್ಟ್‌ಕಟ್ ಹೆಚ್ಚುವರಿಯಾಗಿ ಕಾರ್ಯನಿರ್ವಹಿಸುತ್ತದೆ."},
    "Tamil": {"Command to configure":"கட்டமைக்க வேண்டிய கட்டளை", "Command status":"கட்டளை நிலை", "Additional keyboard shortcut":"கூடுதல் விசைப்பலகை குறுக்குவழி", "Command category":"கட்டளை வகை", "All":"அனைத்தும்", "System":"கணினி", "Network":"பிணையம்", "Audio":"ஒலி", "Accessibility":"அணுகல்தன்மை", "NVDA":"NVDA", "Leave the shortcut blank to keep the default shortcut only. Custom shortcuts are additional bindings.":"இயல்புநிலை குறுக்குவழியை மட்டும் வைத்திருக்க காலியாக விடவும். தனிப்பயன் குறுக்குவழி கூடுதலாக செயல்படும்."},
    "Telugu": {"Command to configure":"కాన్ఫిగర్ చేయాల్సిన ఆదేశం", "Command status":"ఆదేశ స్థితి", "Additional keyboard shortcut":"అదనపు కీబోర్డ్ షార్ట్‌కట్", "Command category":"ఆదేశ వర్గం", "All":"అన్నీ", "System":"సిస్టమ్", "Network":"నెట్‌వర్క్", "Audio":"ఆడియో", "Accessibility":"ప్రాప్యత", "NVDA":"NVDA", "Leave the shortcut blank to keep the default shortcut only. Custom shortcuts are additional bindings.":"డిఫాల్ట్ షార్ట్‌కట్ మాత్రమే ఉంచడానికి ఖాళీగా వదలండి. కస్టమ్ షార్ట్‌కట్ అదనపు షార్ట్‌కట్‌గా పనిచేస్తుంది."},
    "Malayalam": {"Command to configure":"ക്രമീകരിക്കേണ്ട കമാൻഡ്", "Command status":"കമാൻഡ് നില", "Additional keyboard shortcut":"അധിക കീബോർഡ് കുറുക്കുവഴി", "Command category":"കമാൻഡ് വിഭാഗം", "All":"എല്ലാം", "System":"സിസ്റ്റം", "Network":"നെറ്റ്‌വർക്ക്", "Audio":"ഓഡിയോ", "Accessibility":"ആക്സസിബിലിറ്റി", "NVDA":"NVDA", "Leave the shortcut blank to keep the default shortcut only. Custom shortcuts are additional bindings.":"ഡിഫോൾട്ട് കുറുക്കുവഴി മാത്രം നിലനിർത്താൻ ശൂന്യമായി വിടുക. ഇഷ്ടാനുസൃത കുറുക്കുവഴി അധികമായി പ്രവർത്തിക്കും."}
}.items():
    UI_TEXT[_lang].update(_values)

def tr(text):
    language = config.conf.get("quickInfo", {}).get("language", "English")
    return UI_TEXT.get(language, {}).get(text, SPOKEN_TEXT.get(language, {}).get(text, text))


# Spoken announcement translations. Dynamic values such as device names, SSIDs,
# IP addresses, Windows edition names and numbers are intentionally preserved.
SPOKEN_TEXT = {
"Hindi": {
"Unable to determine ": "निर्धारित नहीं किया जा सका: ", "Clipboard unavailable": "क्लिपबोर्ड उपलब्ध नहीं है",
" copied to clipboard": " क्लिपबोर्ड पर कॉपी किया गया", "Unable to copy ": "कॉपी नहीं किया जा सका: ",
"Screen Curtain is on": "स्क्रीन कर्टेन चालू है", "Screen Curtain is off": "स्क्रीन कर्टेन बंद है",
"Screen Curtain status is unavailable in this NVDA version": "इस NVDA संस्करण में स्क्रीन कर्टेन की स्थिति उपलब्ध नहीं है",
"Caps Lock on": "कैप्स लॉक चालू", "Caps Lock off": "कैप्स लॉक बंद", "Num Lock on": "न्यूम लॉक चालू", "Num Lock off": "न्यूम लॉक बंद",
"Keyboard layout ": "कीबोर्ड लेआउट ", "Keyboard layout detected": "कीबोर्ड लेआउट का पता चला",
"Keyboard layout unavailable": "कीबोर्ड लेआउट उपलब्ध नहीं है", "Focus cursor on ": "फोकस कर्सर यहां है: ",
"Unable to determine NVDA cursor context": "NVDA कर्सर का संदर्भ निर्धारित नहीं किया जा सका",
"NVDA cursor information unavailable": "NVDA कर्सर की जानकारी उपलब्ध नहीं है",
"Checking Wi-Fi connection and signal strength": "वाई-फाई कनेक्शन और सिग्नल की जांच की जा रही है",
"Connected via Wi-Fi": "वाई-फाई से जुड़ा है", "Connected via Ethernet": "ईथरनेट से जुड़ा है",
"No active network connection": "कोई सक्रिय नेटवर्क कनेक्शन नहीं है", "Network information unavailable": "नेटवर्क की जानकारी उपलब्ध नहीं है",
"Checking default audio output device": "डिफ़ॉल्ट ऑडियो आउटपुट डिवाइस की जांच की जा रही है",
"Default audio output device": "डिफ़ॉल्ट ऑडियो आउटपुट डिवाइस", "Windows did not return a default playback device": "Windows ने डिफ़ॉल्ट प्लेबैक डिवाइस की जानकारी नहीं दी",
"Audio device detection timed out while querying Windows Core Audio": "Windows Core Audio से जानकारी लेते समय ऑडियो डिवाइस की जांच का समय समाप्त हो गया",
"Audio device detection failed: ": "ऑडियो डिवाइस की जांच विफल हुई: ", "Computer name": "कंप्यूटर का नाम",
"Computer name unavailable": "कंप्यूटर का नाम उपलब्ध नहीं है", "Network name": "नेटवर्क का नाम",
"Network name unavailable": "नेटवर्क का नाम उपलब्ध नहीं है", "Copy computer name command is disabled in QuickInfo options": "QuickInfo सेटिंग में कंप्यूटर नाम कॉपी करने की कमांड बंद है",
"Copy network name command is disabled in QuickInfo options": "QuickInfo सेटिंग में नेटवर्क नाम कॉपी करने की कमांड बंद है",
"Unable to copy computer name": "कंप्यूटर का नाम कॉपी नहीं किया जा सका", "Unable to copy network name": "नेटवर्क का नाम कॉपी नहीं किया जा सका",
"Unable to retrieve system information": "सिस्टम की जानकारी प्राप्त नहीं की जा सकी", "Internet access available": "इंटरनेट उपलब्ध है",
"Network adapter may be connected, but internet access could not be confirmed": "नेटवर्क अडैप्टर जुड़ा हो सकता है, लेकिन इंटरनेट कनेक्शन की पुष्टि नहीं हुई",
"IP address ": "आईपी पता ", "IP address unavailable": "आईपी पता उपलब्ध नहीं है", "DNS servers ": "डीएनएस सर्वर ",
"DNS server information unavailable": "डीएनएस सर्वर की जानकारी उपलब्ध नहीं है", "Default gateway ": "डिफ़ॉल्ट गेटवे ",
"Default gateway unavailable": "डिफ़ॉल्ट गेटवे उपलब्ध नहीं है", "Battery not detected": "बैटरी का पता नहीं चला",
"Battery ": "बैटरी ", "discharging": "डिस्चार्ज हो रही है", "charging": "चार्ज हो रही है", "fully charged": "पूरी तरह चार्ज है",
"low": "कम है", "critical": "बहुत कम है", "status unavailable": "स्थिति उपलब्ध नहीं है", "estimated ": "अनुमानित ",
" hours ": " घंटे ", " minutes remaining": " मिनट शेष", "System uptime ": "सिस्टम चालू रहने का समय ",
" days, ": " दिन, ", " hours, ": " घंटे, ", " minutes": " मिनट", "Windows edition unavailable": "Windows संस्करण उपलब्ध नहीं है",
"unknown": "अज्ञात", "version ": "संस्करण ", "build ": "बिल्ड ", "Memory ": "मेमोरी ",
" gigabytes total, ": " गीगाबाइट कुल, ", " gigabytes available": " गीगाबाइट उपलब्ध", "Memory information unavailable": "मेमोरी की जानकारी उपलब्ध नहीं है",
"Processor name unavailable": "प्रोसेसर का नाम उपलब्ध नहीं है", ", current load ": ", वर्तमान उपयोग ", " percent": " प्रतिशत",
"Microphone endpoint ": "माइक्रोफ़ोन डिवाइस ", "Microphone information unavailable": "माइक्रोफ़ोन की जानकारी उपलब्ध नहीं है",
"Bluetooth devices detected: ": "ब्लूटूथ डिवाइस मिले: ", "No active Bluetooth devices found, or Bluetooth information is unavailable": "कोई सक्रिय ब्लूटूथ डिवाइस नहीं मिला या जानकारी उपलब्ध नहीं है",
"System drive has ": "सिस्टम ड्राइव में ", " gigabytes free of ": " में से गीगाबाइट खाली हैं, कुल ", "System drive information unavailable": "सिस्टम ड्राइव की जानकारी उपलब्ध नहीं है",
"Quick system summary. ": "सिस्टम का संक्षिप्त विवरण। ", "Clipboard contains files": "क्लिपबोर्ड में फ़ाइलें हैं",
"Clipboard contains text": "क्लिपबोर्ड में टेक्स्ट है", "Clipboard empty or unsupported": "क्लिपबोर्ड खाली है या फ़ॉर्मैट समर्थित नहीं है",
},
"Kannada": {
"Unable to determine ": "ನಿರ್ಧರಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ: ", "Clipboard unavailable": "ಕ್ಲಿಪ್‌ಬೋರ್ಡ್ ಲಭ್ಯವಿಲ್ಲ",
" copied to clipboard": " ಕ್ಲಿಪ್‌ಬೋರ್ಡ್‌ಗೆ ನಕಲಿಸಲಾಗಿದೆ", "Unable to copy ": "ನಕಲಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ: ",
"Screen Curtain is on": "ಸ್ಕ್ರೀನ್ ಕರ್ಟನ್ ಆನ್ ಆಗಿದೆ", "Screen Curtain is off": "ಸ್ಕ್ರೀನ್ ಕರ್ಟನ್ ಆಫ್ ಆಗಿದೆ",
"Screen Curtain status is unavailable in this NVDA version": "ಈ NVDA ಆವೃತ್ತಿಯಲ್ಲಿ ಸ್ಕ್ರೀನ್ ಕರ್ಟನ್ ಸ್ಥಿತಿ ಲಭ್ಯವಿಲ್ಲ",
"Caps Lock on": "ಕ್ಯಾಪ್ಸ್ ಲಾಕ್ ಆನ್", "Caps Lock off": "ಕ್ಯಾಪ್ಸ್ ಲಾಕ್ ಆಫ್", "Num Lock on": "ನಮ್ ಲಾಕ್ ಆನ್", "Num Lock off": "ನಮ್ ಲಾಕ್ ಆಫ್",
"Keyboard layout ": "ಕೀಬೋರ್ಡ್ ವಿನ್ಯಾಸ ", "Keyboard layout detected": "ಕೀಬೋರ್ಡ್ ವಿನ್ಯಾಸ ಪತ್ತೆಯಾಗಿದೆ",
"Keyboard layout unavailable": "ಕೀಬೋರ್ಡ್ ವಿನ್ಯಾಸ ಲಭ್ಯವಿಲ್ಲ", "Focus cursor on ": "ಫೋಕಸ್ ಕರ್ಸರ್ ಇಲ್ಲಿ ಇದೆ: ",
"Unable to determine NVDA cursor context": "NVDA ಕರ್ಸರ್ ಸಂದರ್ಭವನ್ನು ನಿರ್ಧರಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ", "NVDA cursor information unavailable": "NVDA ಕರ್ಸರ್ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ",
"Checking Wi-Fi connection and signal strength": "ವೈ-ಫೈ ಸಂಪರ್ಕ ಮತ್ತು ಸಿಗ್ನಲ್ ಶಕ್ತಿಯನ್ನು ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ",
"Connected via Wi-Fi": "ವೈ-ಫೈ ಮೂಲಕ ಸಂಪರ್ಕಗೊಂಡಿದೆ", "Connected via Ethernet": "ಈಥರ್ನೆಟ್ ಮೂಲಕ ಸಂಪರ್ಕಗೊಂಡಿದೆ",
"No active network connection": "ಸಕ್ರಿಯ ನೆಟ್‌ವರ್ಕ್ ಸಂಪರ್ಕವಿಲ್ಲ", "Network information unavailable": "ನೆಟ್‌ವರ್ಕ್ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ",
"Checking default audio output device": "ಡೀಫಾಲ್ಟ್ ಆಡಿಯೊ ಔಟ್‌ಪುಟ್ ಸಾಧನವನ್ನು ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ", "Default audio output device": "ಡೀಫಾಲ್ಟ್ ಆಡಿಯೊ ಔಟ್‌ಪುಟ್ ಸಾಧನ",
"Windows did not return a default playback device": "Windows ಡೀಫಾಲ್ಟ್ ಪ್ಲೇಬ್ಯಾಕ್ ಸಾಧನವನ್ನು ನೀಡಲಿಲ್ಲ", "Audio device detection timed out while querying Windows Core Audio": "Windows Core Audio ಪರಿಶೀಲಿಸುವಾಗ ಸಮಯ ಮೀರಿದೆ",
"Audio device detection failed: ": "ಆಡಿಯೊ ಸಾಧನ ಪತ್ತೆ ವಿಫಲವಾಗಿದೆ: ", "Computer name": "ಕಂಪ್ಯೂಟರ್ ಹೆಸರು", "Computer name unavailable": "ಕಂಪ್ಯೂಟರ್ ಹೆಸರು ಲಭ್ಯವಿಲ್ಲ",
"Network name": "ನೆಟ್‌ವರ್ಕ್ ಹೆಸರು", "Network name unavailable": "ನೆಟ್‌ವರ್ಕ್ ಹೆಸರು ಲಭ್ಯವಿಲ್ಲ", "Copy computer name command is disabled in QuickInfo options": "QuickInfo ಆಯ್ಕೆಗಳಲ್ಲಿ ಕಂಪ್ಯೂಟರ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್ ನಿಷ್ಕ್ರಿಯವಾಗಿದೆ",
"Copy network name command is disabled in QuickInfo options": "QuickInfo ಆಯ್ಕೆಗಳಲ್ಲಿ ನೆಟ್‌ವರ್ಕ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್ ನಿಷ್ಕ್ರಿಯವಾಗಿದೆ", "Unable to copy computer name": "ಕಂಪ್ಯೂಟರ್ ಹೆಸರನ್ನು ನಕಲಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ", "Unable to copy network name": "ನೆಟ್‌ವರ್ಕ್ ಹೆಸರನ್ನು ನಕಲಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ",
"Unable to retrieve system information": "ಸಿಸ್ಟಮ್ ಮಾಹಿತಿ ಪಡೆಯಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ", "Internet access available": "ಇಂಟರ್ನೆಟ್ ಸಂಪರ್ಕ ಲಭ್ಯವಿದೆ", "Network adapter may be connected, but internet access could not be confirmed": "ನೆಟ್‌ವರ್ಕ್ ಅಡಾಪ್ಟರ್ ಸಂಪರ್ಕಗೊಂಡಿರಬಹುದು, ಆದರೆ ಇಂಟರ್ನೆಟ್ ಸಂಪರ್ಕವನ್ನು ಖಚಿತಪಡಿಸಲಾಗಲಿಲ್ಲ",
"IP address ": "ಐಪಿ ವಿಳಾಸ ", "IP address unavailable": "ಐಪಿ ವಿಳಾಸ ಲಭ್ಯವಿಲ್ಲ", "DNS servers ": "ಡಿಎನ್ಎಸ್ ಸರ್ವರ್‌ಗಳು ", "DNS server information unavailable": "ಡಿಎನ್ಎಸ್ ಸರ್ವರ್ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ",
"Default gateway ": "ಡೀಫಾಲ್ಟ್ ಗೇಟ್‌ವೇ ", "Default gateway unavailable": "ಡೀಫಾಲ್ಟ್ ಗೇಟ್‌ವೇ ಲಭ್ಯವಿಲ್ಲ", "Battery not detected": "ಬ್ಯಾಟರಿ ಪತ್ತೆಯಾಗಿಲ್ಲ", "Battery ": "ಬ್ಯಾಟರಿ ",
"discharging": "ಚಾರ್ಜ್ ಕಡಿಮೆಯಾಗುತ್ತಿದೆ", "charging": "ಚಾರ್ಜ್ ಆಗುತ್ತಿದೆ", "fully charged": "ಪೂರ್ಣ ಚಾರ್ಜ್ ಆಗಿದೆ", "low": "ಕಡಿಮೆ", "critical": "ಅತ್ಯಂತ ಕಡಿಮೆ", "status unavailable": "ಸ್ಥಿತಿ ಲಭ್ಯವಿಲ್ಲ", "estimated ": "ಅಂದಾಜು ",
" hours ": " ಗಂಟೆ ", " minutes remaining": " ನಿಮಿಷ ಉಳಿದಿದೆ", "System uptime ": "ಸಿಸ್ಟಮ್ ಚಾಲನೆಯಲ್ಲಿರುವ ಸಮಯ ", " days, ": " ದಿನ, ", " hours, ": " ಗಂಟೆ, ", " minutes": " ನಿಮಿಷ",
"Windows edition unavailable": "Windows ಆವೃತ್ತಿ ಲಭ್ಯವಿಲ್ಲ", "unknown": "ಗೊತ್ತಿಲ್ಲ", "version ": "ಆವೃತ್ತಿ ", "build ": "ಬಿಲ್ಡ್ ", "Memory ": "ಮೆಮೊರಿ ", " gigabytes total, ": " ಗಿಗಾಬೈಟ್ ಒಟ್ಟು, ", " gigabytes available": " ಗಿಗಾಬೈಟ್ ಲಭ್ಯವಿದೆ", "Memory information unavailable": "ಮೆಮೊರಿ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ",
"Processor name unavailable": "ಪ್ರೊಸೆಸರ್ ಹೆಸರು ಲಭ್ಯವಿಲ್ಲ", ", current load ": ", ಪ್ರಸ್ತುತ ಬಳಕೆ ", " percent": " ಶೇಕಡಾ", "Microphone endpoint ": "ಮೈಕ್ರೊಫೋನ್ ಸಾಧನ ", "Microphone information unavailable": "ಮೈಕ್ರೊಫೋನ್ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ",
"Bluetooth devices detected: ": "ಬ್ಲೂಟೂತ್ ಸಾಧನಗಳು ಪತ್ತೆಯಾಗಿವೆ: ", "No active Bluetooth devices found, or Bluetooth information is unavailable": "ಸಕ್ರಿಯ ಬ್ಲೂಟೂತ್ ಸಾಧನಗಳಿಲ್ಲ ಅಥವಾ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ",
"System drive has ": "ಸಿಸ್ಟಮ್ ಡ್ರೈವ್‌ನಲ್ಲಿ ", " gigabytes free of ": " ಗಿಗಾಬೈಟ್‌ನಲ್ಲಿ ಖಾಲಿ, ಒಟ್ಟು ", "System drive information unavailable": "ಸಿಸ್ಟಮ್ ಡ್ರೈವ್ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ", "Quick system summary. ": "ಸಿಸ್ಟಮ್ ಸಂಕ್ಷಿಪ್ತ ಮಾಹಿತಿ. ",
"Clipboard contains files": "ಕ್ಲಿಪ್‌ಬೋರ್ಡ್‌ನಲ್ಲಿ ಫೈಲ್‌ಗಳಿವೆ", "Clipboard contains text": "ಕ್ಲಿಪ್‌ಬೋರ್ಡ್‌ನಲ್ಲಿ ಪಠ್ಯವಿದೆ", "Clipboard empty or unsupported": "ಕ್ಲಿಪ್‌ಬೋರ್ಡ್ ಖಾಲಿಯಾಗಿದೆ ಅಥವಾ ಬೆಂಬಲಿತವಲ್ಲ",
},
"Tamil": {
"Unable to determine ": "கண்டறிய முடியவில்லை: ", "Clipboard unavailable": "கிளிப்போர்டு கிடைக்கவில்லை", " copied to clipboard": " கிளிப்போர்டுக்கு நகலெடுக்கப்பட்டது", "Unable to copy ": "நகலெடுக்க முடியவில்லை: ",
"Screen Curtain is on": "ஸ்கிரீன் கர்டன் இயக்கத்தில் உள்ளது", "Screen Curtain is off": "ஸ்கிரீன் கர்டன் முடக்கப்பட்டுள்ளது", "Screen Curtain status is unavailable in this NVDA version": "இந்த NVDA பதிப்பில் ஸ்கிரீன் கர்டன் நிலை கிடைக்கவில்லை",
"Caps Lock on": "கேப்ஸ் லாக் இயக்கத்தில்", "Caps Lock off": "கேப்ஸ் லாக் முடக்கப்பட்டுள்ளது", "Num Lock on": "நம் லாக் இயக்கத்தில்", "Num Lock off": "நம் லாக் முடக்கப்பட்டுள்ளது", "Keyboard layout ": "விசைப்பலகை அமைப்பு ", "Keyboard layout detected": "விசைப்பலகை அமைப்பு கண்டறியப்பட்டது", "Keyboard layout unavailable": "விசைப்பலகை அமைப்பு கிடைக்கவில்லை",
"Focus cursor on ": "ஃபோகஸ் கர்சர் இங்கே உள்ளது: ", "Unable to determine NVDA cursor context": "NVDA கர்சர் சூழலைக் கண்டறிய முடியவில்லை", "NVDA cursor information unavailable": "NVDA கர்சர் தகவல் கிடைக்கவில்லை",
"Checking Wi-Fi connection and signal strength": "வை-ஃபை இணைப்பு மற்றும் சிக்னல் வலிமை சரிபார்க்கப்படுகிறது", "Connected via Wi-Fi": "வை-ஃபை மூலம் இணைக்கப்பட்டுள்ளது", "Connected via Ethernet": "ஈதர்நெட் மூலம் இணைக்கப்பட்டுள்ளது", "No active network connection": "செயலில் உள்ள நெட்வொர்க் இணைப்பு இல்லை", "Network information unavailable": "நெட்வொர்க் தகவல் கிடைக்கவில்லை",
"Checking default audio output device": "இயல்புநிலை ஆடியோ வெளியீட்டு சாதனம் சரிபார்க்கப்படுகிறது", "Default audio output device": "இயல்புநிலை ஆடியோ வெளியீட்டு சாதனம்", "Windows did not return a default playback device": "Windows இயல்புநிலை ஒலிபரப்பு சாதனத்தை வழங்கவில்லை", "Audio device detection timed out while querying Windows Core Audio": "Windows Core Audio-ஐச் சரிபாரிக்கும் நேரம் முடிந்தது", "Audio device detection failed: ": "ஆடியோ சாதனத்தைக் கண்டறிய முடியவில்லை: ",
"Computer name": "கணினி பெயர்", "Computer name unavailable": "கணினி பெயர் கிடைக்கவில்லை", "Network name": "நெட்வொர்க் பெயர்", "Network name unavailable": "நெட்வொர்க் பெயர் கிடைக்கவில்லை", "Copy computer name command is disabled in QuickInfo options": "QuickInfo அமைப்புகளில் கணினி பெயரை நகலெடுக்கும் கட்டளை முடக்கப்பட்டுள்ளது", "Copy network name command is disabled in QuickInfo options": "QuickInfo அமைப்புகளில் நெட்வொர்க் பெயரை நகலெடுக்கும் கட்டளை முடக்கப்பட்டுள்ளது", "Unable to copy computer name": "கணினி பெயரை நகலெடுக்க முடியவில்லை", "Unable to copy network name": "நெட்வொர்க் பெயரை நகலெடுக்க முடியவில்லை",
"Unable to retrieve system information": "கணினி தகவலைப் பெற முடியவில்லை", "Internet access available": "இணைய அணுகல் உள்ளது", "Network adapter may be connected, but internet access could not be confirmed": "நெட்வொர்க் இணைக்கப்பட்டிருக்கலாம்; இணைய அணுகலை உறுதிப்படுத்த முடியவில்லை", "IP address ": "ஐபி முகவரி ", "IP address unavailable": "ஐபி முகவரி கிடைக்கவில்லை", "DNS servers ": "டிஎன்எஸ் சேவையகங்கள் ", "DNS server information unavailable": "டிஎன்எஸ் சேவையகத் தகவல் கிடைக்கவில்லை", "Default gateway ": "இயல்புநிலை கேட்வே ", "Default gateway unavailable": "இயல்புநிலை கேட்வே கிடைக்கவில்லை",
"Battery not detected": "பேட்டரி கண்டறியப்படவில்லை", "Battery ": "பேட்டரி ", "discharging": "மின்சாரம் குறைகிறது", "charging": "சார்ஜ் ஆகிறது", "fully charged": "முழுமையாக சார்ஜ் செய்யப்பட்டது", "low": "குறைவு", "critical": "மிகக் குறைவு", "status unavailable": "நிலை கிடைக்கவில்லை", "estimated ": "மதிப்பிடப்பட்ட ", " hours ": " மணி ", " minutes remaining": " நிமிடங்கள் மீதமுள்ளன",
"System uptime ": "கணினி இயங்கிய நேரம் ", " days, ": " நாட்கள், ", " hours, ": " மணி, ", " minutes": " நிமிடங்கள்", "Windows edition unavailable": "Windows பதிப்பு கிடைக்கவில்லை", "unknown": "தெரியவில்லை", "version ": "பதிப்பு ", "build ": "பில்டு ", "Memory ": "நினைவகம் ", " gigabytes total, ": " ஜிகாபைட் மொத்தம், ", " gigabytes available": " ஜிகாபைட் கிடைக்கிறது", "Memory information unavailable": "நினைவகத் தகவல் கிடைக்கவில்லை", "Processor name unavailable": "செயலி பெயர் கிடைக்கவில்லை", ", current load ": ", தற்போதைய பயன்பாடு ", " percent": " சதவீதம்", "Microphone endpoint ": "மைக்ரோஃபோன் சாதனம் ", "Microphone information unavailable": "மைக்ரோஃபோன் தகவல் கிடைக்கவில்லை", "Bluetooth devices detected: ": "புளூடூத் சாதனங்கள் கண்டறியப்பட்டன: ", "No active Bluetooth devices found, or Bluetooth information is unavailable": "செயலில் உள்ள புளூடூத் சாதனங்கள் இல்லை அல்லது தகவல் கிடைக்கவில்லை", "System drive has ": "கணினி இயக்ககத்தில் ", " gigabytes free of ": " ஜிகாபைட்டில் காலி, மொத்தம் ", "System drive information unavailable": "கணினி இயக்ககத் தகவல் கிடைக்கவில்லை", "Quick system summary. ": "கணினி சுருக்கம். ", "Clipboard contains files": "கிளிப்போர்டில் கோப்புகள் உள்ளன", "Clipboard contains text": "கிளிப்போர்டில் உரை உள்ளது", "Clipboard empty or unsupported": "கிளிப்போர்டு காலியாக உள்ளது அல்லது ஆதரிக்கப்படவில்லை",
},
"Telugu": {
"Unable to determine ": "గుర్తించలేకపోయాము: ", "Clipboard unavailable": "క్లిప్‌బోర్డ్ అందుబాటులో లేదు", " copied to clipboard": " క్లిప్‌బోర్డ్‌కు కాపీ చేయబడింది", "Unable to copy ": "కాపీ చేయలేకపోయాము: ",
"Screen Curtain is on": "స్క్రీన్ కర్టెన్ ఆన్‌లో ఉంది", "Screen Curtain is off": "స్క్రీన్ కర్టెన్ ఆఫ్‌లో ఉంది", "Screen Curtain status is unavailable in this NVDA version": "ఈ NVDA వెర్షన్‌లో స్క్రీన్ కర్టెన్ స్థితి అందుబాటులో లేదు", "Caps Lock on": "క్యాప్స్ లాక్ ఆన్", "Caps Lock off": "క్యాప్స్ లాక్ ఆఫ్", "Num Lock on": "నమ్ లాక్ ఆన్", "Num Lock off": "నమ్ లాక్ ఆఫ్", "Keyboard layout ": "కీబోర్డ్ లేఅవుట్ ", "Keyboard layout detected": "కీబోర్డ్ లేఅవుట్ గుర్తించబడింది", "Keyboard layout unavailable": "కీబోర్డ్ లేఅవుట్ అందుబాటులో లేదు", "Focus cursor on ": "ఫోకస్ కర్సర్ ఇక్కడ ఉంది: ", "Unable to determine NVDA cursor context": "NVDA కర్సర్ సందర్భాన్ని గుర్తించలేకపోయాము", "NVDA cursor information unavailable": "NVDA కర్సర్ సమాచారం అందుబాటులో లేదు",
"Checking Wi-Fi connection and signal strength": "వై-ఫై కనెక్షన్ మరియు సిగ్నల్ బలాన్ని తనిఖీ చేస్తున్నాము", "Connected via Wi-Fi": "వై-ఫై ద్వారా కనెక్ట్ అయింది", "Connected via Ethernet": "ఈథర్నెట్ ద్వారా కనెక్ట్ అయింది", "No active network connection": "క్రియాశీల నెట్‌వర్క్ కనెక్షన్ లేదు", "Network information unavailable": "నెట్‌వర్క్ సమాచారం అందుబాటులో లేదు", "Checking default audio output device": "డిఫాల్ట్ ఆడియో అవుట్‌పుట్ పరికరాన్ని తనిఖీ చేస్తున్నాము", "Default audio output device": "డిఫాల్ట్ ఆడియో అవుట్‌పుట్ పరికరం", "Windows did not return a default playback device": "Windows డిఫాల్ట్ ప్లేబ్యాక్ పరికరాన్ని అందించలేదు", "Audio device detection timed out while querying Windows Core Audio": "Windows Core Audio తనిఖీ సమయం ముగిసింది", "Audio device detection failed: ": "ఆడియో పరికరాన్ని గుర్తించడంలో విఫలమైంది: ",
"Computer name": "కంప్యూటర్ పేరు", "Computer name unavailable": "కంప్యూటర్ పేరు అందుబాటులో లేదు", "Network name": "నెట్‌వర్క్ పేరు", "Network name unavailable": "నెట్‌వర్క్ పేరు అందుబాటులో లేదు", "Copy computer name command is disabled in QuickInfo options": "QuickInfo ఎంపికల్లో కంప్యూటర్ పేరును కాపీ చేసే కమాండ్ నిలిపివేయబడింది", "Copy network name command is disabled in QuickInfo options": "QuickInfo ఎంపికల్లో నెట్‌వర్క్ పేరును కాపీ చేసే కమాండ్ నిలిపివేయబడింది", "Unable to copy computer name": "కంప్యూటర్ పేరును కాపీ చేయలేకపోయాము", "Unable to copy network name": "నెట్‌వర్క్ పేరును కాపీ చేయలేకపోయాము", "Unable to retrieve system information": "సిస్టమ్ సమాచారాన్ని పొందలేకపోయాము", "Internet access available": "ఇంటర్నెట్ అందుబాటులో ఉంది", "Network adapter may be connected, but internet access could not be confirmed": "నెట్‌వర్క్ అడాప్టర్ కనెక్ట్ అయి ఉండవచ్చు, కానీ ఇంటర్నెట్ కనెక్షన్‌ను నిర్ధారించలేకపోయాము", "IP address ": "ఐపీ చిరునామా ", "IP address unavailable": "ఐపీ చిరునామా అందుబాటులో లేదు", "DNS servers ": "DNS సర్వర్లు ", "DNS server information unavailable": "DNS సర్వర్ సమాచారం అందుబాటులో లేదు", "Default gateway ": "డిఫాల్ట్ గేట్‌వే ", "Default gateway unavailable": "డిఫాల్ట్ గేట్‌వే అందుబాటులో లేదు", "Battery not detected": "బ్యాటరీ గుర్తించబడలేదు", "Battery ": "బ్యాటరీ ", "discharging": "చార్జ్ తగ్గుతోంది", "charging": "చార్జ్ అవుతోంది", "fully charged": "పూర్తిగా చార్జ్ అయింది", "low": "తక్కువ", "critical": "చాలా తక్కువ", "status unavailable": "స్థితి అందుబాటులో లేదు", "estimated ": "అంచనా ", " hours ": " గంటలు ", " minutes remaining": " నిమిషాలు మిగిలి ఉన్నాయి", "System uptime ": "సిస్టమ్ పనిచేసిన సమయం ", " days, ": " రోజులు, ", " hours, ": " గంటలు, ", " minutes": " నిమిషాలు", "Windows edition unavailable": "Windows ఎడిషన్ అందుబాటులో లేదు", "unknown": "తెలియదు", "version ": "వెర్షన్ ", "build ": "బిల్డ్ ", "Memory ": "మెమరీ ", " gigabytes total, ": " గిగాబైట్లు మొత్తం, ", " gigabytes available": " గిగాబైట్లు అందుబాటులో ఉన్నాయి", "Memory information unavailable": "మెమరీ సమాచారం అందుబాటులో లేదు", "Processor name unavailable": "ప్రాసెసర్ పేరు అందుబాటులో లేదు", ", current load ": ", ప్రస్తుత వినియోగం ", " percent": " శాతం", "Microphone endpoint ": "మైక్రోఫోన్ పరికరం ", "Microphone information unavailable": "మైక్రోఫోన్ సమాచారం అందుబాటులో లేదు", "Bluetooth devices detected: ": "బ్లూటూత్ పరికరాలు గుర్తించబడ్డాయి: ", "No active Bluetooth devices found, or Bluetooth information is unavailable": "క్రియాశీల బ్లూటూత్ పరికరాలు లేవు లేదా సమాచారం అందుబాటులో లేదు", "System drive has ": "సిస్టమ్ డ్రైవ్‌లో ", " gigabytes free of ": " గిగాబైట్లలో ఖాళీ, మొత్తం ", "System drive information unavailable": "సిస్టమ్ డ్రైవ్ సమాచారం అందుబాటులో లేదు", "Quick system summary. ": "సిస్టమ్ సంక్షిప్త సమాచారం. ", "Clipboard contains files": "క్లిప్‌బోర్డ్‌లో ఫైళ్లు ఉన్నాయి", "Clipboard contains text": "క్లిప్‌బోర్డ్‌లో టెక్స్ట్ ఉంది", "Clipboard empty or unsupported": "క్లిప్‌బోర్డ్ ఖాళీగా ఉంది లేదా మద్దతు లేదు",
},
"Malayalam": {
"Unable to determine ": "കണ്ടെത്താനായില്ല: ", "Clipboard unavailable": "ക്ലിപ്പ്ബോർഡ് ലഭ്യമല്ല", " copied to clipboard": " ക്ലിപ്പ്ബോർഡിലേക്ക് പകർത്തി", "Unable to copy ": "പകർത്താനായില്ല: ",
"Screen Curtain is on": "സ്ക്രീൻ കർട്ടൻ ഓണാണ്", "Screen Curtain is off": "സ്ക്രീൻ കർട്ടൻ ഓഫാണ്", "Screen Curtain status is unavailable in this NVDA version": "ഈ NVDA പതിപ്പിൽ സ്ക്രീൻ കർട്ടൻ നില ലഭ്യമല്ല", "Caps Lock on": "ക്യാപ്സ് ലോക്ക് ഓൺ", "Caps Lock off": "ക്യാപ്സ് ലോക്ക് ഓഫ്", "Num Lock on": "നം ലോക്ക് ഓൺ", "Num Lock off": "നം ലോക്ക് ഓഫ്", "Keyboard layout ": "കീബോർഡ് ലേയൗട്ട് ", "Keyboard layout detected": "കീബോർഡ് ലേയൗട്ട് കണ്ടെത്തി", "Keyboard layout unavailable": "കീബോർഡ് ലേയൗട്ട് ലഭ്യമല്ല", "Focus cursor on ": "ഫോക്കസ് കർസർ ഇവിടെ: ", "Unable to determine NVDA cursor context": "NVDA കർസർ സന്ദർഭം കണ്ടെത്താനായില്ല", "NVDA cursor information unavailable": "NVDA കർസർ വിവരം ലഭ്യമല്ല",
"Checking Wi-Fi connection and signal strength": "വൈ-ഫൈ കണക്ഷനും സിഗ്നൽ ശക്തിയും പരിശോധിക്കുന്നു", "Connected via Wi-Fi": "വൈ-ഫൈ വഴി കണക്റ്റ് ചെയ്തിരിക്കുന്നു", "Connected via Ethernet": "ഇഥർനെറ്റ് വഴി കണക്റ്റ് ചെയ്തിരിക്കുന്നു", "No active network connection": "സജീവ നെറ്റ്‌വർക്ക് കണക്ഷൻ ഇല്ല", "Network information unavailable": "നെറ്റ്‌വർക്ക് വിവരം ലഭ്യമല്ല", "Checking default audio output device": "ഡിഫോൾട്ട് ഓഡിയോ ഔട്ട്പുട്ട് ഉപകരണം പരിശോധിക്കുന്നു", "Default audio output device": "ഡിഫോൾട്ട് ഓഡിയോ ഔട്ട്പുട്ട് ഉപകരണം", "Windows did not return a default playback device": "Windows ഡിഫോൾട്ട് പ്ലേബാക്ക് ഉപകരണം നൽകിയില്ല", "Audio device detection timed out while querying Windows Core Audio": "Windows Core Audio പരിശോധിക്കുന്നതിനുള്ള സമയം കഴിഞ്ഞു", "Audio device detection failed: ": "ഓഡിയോ ഉപകരണം കണ്ടെത്താനായില്ല: ",
"Computer name": "കമ്പ്യൂട്ടറിന്റെ പേര്", "Computer name unavailable": "കമ്പ്യൂട്ടറിന്റെ പേര് ലഭ്യമല്ല", "Network name": "നെറ്റ്‌വർക്ക് പേര്", "Network name unavailable": "നെറ്റ്‌വർക്ക് പേര് ലഭ്യമല്ല", "Copy computer name command is disabled in QuickInfo options": "QuickInfo ക്രമീകരണങ്ങളിൽ കമ്പ്യൂട്ടർ പേര് പകർത്താനുള്ള കമാൻഡ് പ്രവർത്തനരഹിതമാണ്", "Copy network name command is disabled in QuickInfo options": "QuickInfo ക്രമീകരണങ്ങളിൽ നെറ്റ്‌വർക്ക് പേര് പകർത്താനുള്ള കമാൻഡ് പ്രവർത്തനരഹിതമാണ്", "Unable to copy computer name": "കമ്പ്യൂട്ടറിന്റെ പേര് പകർത്താനായില്ല", "Unable to copy network name": "നെറ്റ്‌വർക്ക് പേര് പകർത്താനായില്ല", "Unable to retrieve system information": "സിസ്റ്റം വിവരം ലഭ്യമാക്കാനായില്ല", "Internet access available": "ഇന്റർനെറ്റ് ലഭ്യമാണ്", "Network adapter may be connected, but internet access could not be confirmed": "നെറ്റ്‌വർക്ക് അഡാപ്റ്റർ കണക്റ്റ് ചെയ്തിരിക്കാം, എന്നാൽ ഇന്റർനെറ്റ് ഉറപ്പാക്കാനായില്ല", "IP address ": "ഐപി വിലാസം ", "IP address unavailable": "ഐപി വിലാസം ലഭ്യമല്ല", "DNS servers ": "ഡിഎൻഎസ് സെർവറുകൾ ", "DNS server information unavailable": "ഡിഎൻഎസ് സെർവർ വിവരം ലഭ്യമല്ല", "Default gateway ": "ഡിഫോൾട്ട് ഗേറ്റ്‌വേ ", "Default gateway unavailable": "ഡിഫോൾട്ട് ഗേറ്റ്‌വേ ലഭ്യമല്ല", "Battery not detected": "ബാറ്ററി കണ്ടെത്തിയില്ല", "Battery ": "ബാറ്ററി ", "discharging": "ചാർജ് കുറയുന്നു", "charging": "ചാർജ് ചെയ്യുന്നു", "fully charged": "പൂർണ്ണമായി ചാർജ് ചെയ്തു", "low": "കുറവ്", "critical": "വളരെ കുറവ്", "status unavailable": "നില ലഭ്യമല്ല", "estimated ": "ഏകദേശം ", " hours ": " മണിക്കൂർ ", " minutes remaining": " മിനിറ്റ് ശേഷിക്കുന്നു", "System uptime ": "സിസ്റ്റം പ്രവർത്തിച്ച സമയം ", " days, ": " ദിവസം, ", " hours, ": " മണിക്കൂർ, ", " minutes": " മിനിറ്റ്", "Windows edition unavailable": "Windows പതിപ്പ് ലഭ്യമല്ല", "unknown": "അറിയില്ല", "version ": "പതിപ്പ് ", "build ": "ബിൽഡ് ", "Memory ": "മെമ്മറി ", " gigabytes total, ": " ജിഗാബൈറ്റ് ആകെ, ", " gigabytes available": " ജിഗാബൈറ്റ് ലഭ്യമാണ്", "Memory information unavailable": "മെമ്മറി വിവരം ലഭ്യമല്ല", "Processor name unavailable": "പ്രോസസറിന്റെ പേര് ലഭ്യമല്ല", ", current load ": ", നിലവിലെ ഉപയോഗം ", " percent": " ശതമാനം", "Microphone endpoint ": "മൈക്രോഫോൺ ഉപകരണം ", "Microphone information unavailable": "മൈക്രോഫോൺ വിവരം ലഭ്യമല്ല", "Bluetooth devices detected: ": "ബ്ലൂടൂത്ത് ഉപകരണങ്ങൾ കണ്ടെത്തി: ", "No active Bluetooth devices found, or Bluetooth information is unavailable": "സജീവ ബ്ലൂടൂത്ത് ഉപകരണങ്ങളില്ല അല്ലെങ്കിൽ വിവരം ലഭ്യമല്ല", "System drive has ": "സിസ്റ്റം ഡ്രൈവിൽ ", " gigabytes free of ": " ജിഗാബൈറ്റിൽ ഒഴിവുണ്ട്, ആകെ ", "System drive information unavailable": "സിസ്റ്റം ഡ്രൈവ് വിവരം ലഭ്യമല്ല", "Quick system summary. ": "സിസ്റ്റം സംഗ്രഹം. ", "Clipboard contains files": "ക്ലിപ്പ്ബോർഡിൽ ഫയലുകളുണ്ട്", "Clipboard contains text": "ക്ലിപ്പ്ബോർഡിൽ ടെക്സ്റ്റുണ്ട്", "Clipboard empty or unsupported": "ക്ലിപ്പ്ബോർഡ് ശൂന്യമാണ് അല്ലെങ്കിൽ പിന്തുണയില്ല",
}
}


# Labels used in the command browser and the remaining settings help text.
SPOKEN_TEXT["Hindi"].update({
"Screen Curtain status":"स्क्रीन कर्टेन की स्थिति", "Wi-Fi status and SSID":"वाई-फाई स्थिति और SSID", "Network connection type":"नेटवर्क कनेक्शन का प्रकार", "Default audio output device":"डिफ़ॉल्ट ऑडियो आउटपुट डिवाइस", "Copy computer name command":"कंप्यूटर नाम कॉपी करने की कमांड", "Copy network name command":"नेटवर्क नाम कॉपी करने की कमांड", "Internet connectivity":"इंटरनेट कनेक्टिविटी", "IP address":"आईपी पता", "DNS servers":"डीएनएस सर्वर", "Default gateway":"डिफ़ॉल्ट गेटवे", "Battery information":"बैटरी की जानकारी", "System uptime":"सिस्टम चालू रहने का समय", "Windows version":"Windows संस्करण", "Memory information":"मेमोरी की जानकारी", "Processor information":"प्रोसेसर की जानकारी", "Microphone":"माइक्रोफ़ोन", "Bluetooth devices":"ब्लूटूथ डिवाइस", "Disk information":"डिस्क की जानकारी", "Quick system summary":"सिस्टम का संक्षिप्त विवरण", "Clipboard content type":"क्लिपबोर्ड सामग्री का प्रकार", "Keyboard layout":"कीबोर्ड लेआउट", "Lock key status":"लॉक कुंजियों की स्थिति", "NVDA cursor information":"NVDA कर्सर की जानकारी", "Allow the Copy Computer Name command":"कंप्यूटर नाम कॉपी करने की अनुमति दें", "Allow the Copy Network Name command":"नेटवर्क नाम कॉपी करने की अनुमति दें", "Choose the language used for QuickInfo announcements and labels.":"QuickInfo घोषणाओं और लेबल के लिए भाषा चुनें।", "Choose Enabled or Disabled for copying the computer name.":"कंप्यूटर नाम कॉपी करने के लिए चालू या बंद चुनें।", "Choose Enabled or Disabled for copying the network name.":"नेटवर्क नाम कॉपी करने के लिए चालू या बंद चुनें।", "Enabled lets the copy-computer-name command place the computer name on the clipboard.":"चालू होने पर कंप्यूटर नाम कॉपी करने की कमांड नाम को क्लिपबोर्ड पर रखती है।", "Enabled lets the copy-network-name command place the Wi-Fi or network name on the clipboard.":"चालू होने पर नेटवर्क नाम कॉपी करने की कमांड वाई-फाई या नेटवर्क नाम को क्लिपबोर्ड पर रखती है।", "QuickInfo settings saved":"QuickInfo सेटिंग सहेजी गईं"})
SPOKEN_TEXT["Kannada"].update({
"Screen Curtain status":"ಸ್ಕ್ರೀನ್ ಕರ್ಟನ್ ಸ್ಥಿತಿ", "Wi-Fi status and SSID":"ವೈ-ಫೈ ಸ್ಥಿತಿ ಮತ್ತು SSID", "Network connection type":"ನೆಟ್‌ವರ್ಕ್ ಸಂಪರ್ಕದ ವಿಧ", "Copy computer name command":"ಕಂಪ್ಯೂಟರ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್", "Copy network name command":"ನೆಟ್‌ವರ್ಕ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್", "Internet connectivity":"ಇಂಟರ್ನೆಟ್ ಸಂಪರ್ಕ", "IP address":"ಐಪಿ ವಿಳಾಸ", "DNS servers":"ಡಿಎನ್ಎಸ್ ಸರ್ವರ್‌ಗಳು", "Default gateway":"ಡೀಫಾಲ್ಟ್ ಗೇಟ್‌ವೇ", "Battery information":"ಬ್ಯಾಟರಿ ಮಾಹಿತಿ", "System uptime":"ಸಿಸ್ಟಮ್ ಚಾಲನೆಯ ಸಮಯ", "Windows version":"Windows ಆವೃತ್ತಿ", "Memory information":"ಮೆಮೊರಿ ಮಾಹಿತಿ", "Processor information":"ಪ್ರೊಸೆಸರ್ ಮಾಹಿತಿ", "Microphone":"ಮೈಕ್ರೊಫೋನ್", "Bluetooth devices":"ಬ್ಲೂಟೂತ್ ಸಾಧನಗಳು", "Disk information":"ಡಿಸ್ಕ್ ಮಾಹಿತಿ", "Quick system summary":"ಸಿಸ್ಟಮ್ ಸಂಕ್ಷಿಪ್ತ ಮಾಹಿತಿ", "Clipboard content type":"ಕ್ಲಿಪ್‌ಬೋರ್ಡ್ ವಿಷಯದ ವಿಧ", "Keyboard layout":"ಕೀಬೋರ್ಡ್ ವಿನ್ಯಾಸ", "Lock key status":"ಲಾಕ್ ಕೀ ಸ್ಥಿತಿ", "NVDA cursor information":"NVDA ಕರ್ಸರ್ ಮಾಹಿತಿ", "Allow the Copy Computer Name command":"ಕಂಪ್ಯೂಟರ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್‌ಗೆ ಅನುಮತಿ ನೀಡಿ", "Allow the Copy Network Name command":"ನೆಟ್‌ವರ್ಕ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್‌ಗೆ ಅನುಮತಿ ನೀಡಿ", "Choose the language used for QuickInfo announcements and labels.":"QuickInfo ಪ್ರಕಟಣೆಗಳು ಮತ್ತು ಲೇಬಲ್‌ಗಳಿಗೆ ಭಾಷೆಯನ್ನು ಆಯ್ಕೆಮಾಡಿ.", "Choose Enabled or Disabled for copying the computer name.":"ಕಂಪ್ಯೂಟರ್ ಹೆಸರು ನಕಲಿಸಲು ಸಕ್ರಿಯ ಅಥವಾ ನಿಷ್ಕ್ರಿಯ ಆಯ್ಕೆಮಾಡಿ.", "Choose Enabled or Disabled for copying the network name.":"ನೆಟ್‌ವರ್ಕ್ ಹೆಸರು ನಕಲಿಸಲು ಸಕ್ರಿಯ ಅಥವಾ ನಿಷ್ಕ್ರಿಯ ಆಯ್ಕೆಮಾಡಿ.", "Enabled lets the copy-computer-name command place the computer name on the clipboard.":"ಸಕ್ರಿಯವಾಗಿದ್ದರೆ ಕಂಪ್ಯೂಟರ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್ ಹೆಸರನ್ನು ಕ್ಲಿಪ್‌ಬೋರ್ಡ್‌ಗೆ ಸೇರಿಸುತ್ತದೆ.", "Enabled lets the copy-network-name command place the Wi-Fi or network name on the clipboard.":"ಸಕ್ರಿಯವಾಗಿದ್ದರೆ ನೆಟ್‌ವರ್ಕ್ ಹೆಸರು ನಕಲಿಸುವ ಕಮಾಂಡ್ ಹೆಸರನ್ನು ಕ್ಲಿಪ್‌ಬೋರ್ಡ್‌ಗೆ ಸೇರಿಸುತ್ತದೆ.", "QuickInfo settings saved":"QuickInfo ಸೆಟ್ಟಿಂಗ್‌ಗಳನ್ನು ಉಳಿಸಲಾಗಿದೆ"})
SPOKEN_TEXT["Tamil"].update({
"Screen Curtain status":"ஸ்கிரீன் கர்டன் நிலை", "Wi-Fi status and SSID":"வை-ஃபை நிலை மற்றும் SSID", "Network connection type":"நெட்வொர்க் இணைப்பு வகை", "Copy computer name command":"கணினி பெயரை நகலெடுக்கும் கட்டளை", "Copy network name command":"நெட்வொர்க் பெயரை நகலெடுக்கும் கட்டளை", "Internet connectivity":"இணைய இணைப்பு", "IP address":"ஐபி முகவரி", "DNS servers":"டிஎன்எஸ் சேவையகங்கள்", "Default gateway":"இயல்புநிலை கேட்வே", "Battery information":"பேட்டரி தகவல்", "System uptime":"கணினி இயங்கிய நேரம்", "Windows version":"Windows பதிப்பு", "Memory information":"நினைவகத் தகவல்", "Processor information":"செயலி தகவல்", "Microphone":"மைக்ரோஃபோன்", "Bluetooth devices":"புளூடூத் சாதனங்கள்", "Disk information":"வட்டு தகவல்", "Quick system summary":"கணினி சுருக்கம்", "Clipboard content type":"கிளிப்போர்டு உள்ளடக்க வகை", "Keyboard layout":"விசைப்பலகை அமைப்பு", "Lock key status":"லாக் விசை நிலை", "NVDA cursor information":"NVDA கர்சர் தகவல்", "Allow the Copy Computer Name command":"கணினி பெயரை நகலெடுக்கும் கட்டளையை அனுமதி", "Allow the Copy Network Name command":"நெட்வொர்க் பெயரை நகலெடுக்கும் கட்டளையை அனுமதி", "Choose the language used for QuickInfo announcements and labels.":"QuickInfo அறிவிப்புகள் மற்றும் பெயரிடலுக்கான மொழியைத் தேர்ந்தெடுக்கவும்.", "Choose Enabled or Disabled for copying the computer name.":"கணினி பெயரை நகலெடுக்க இயக்கம் அல்லது முடக்கம் தேர்ந்தெடுக்கவும்.", "Choose Enabled or Disabled for copying the network name.":"நெட்வொர்க் பெயரை நகலெடுக்க இயக்கம் அல்லது முடக்கம் தேர்ந்தெடுக்கவும்.", "Enabled lets the copy-computer-name command place the computer name on the clipboard.":"இயக்கத்தில் இருந்தால் கணினி பெயரை கிளிப்போர்டில் நகலெடுக்கும்.", "Enabled lets the copy-network-name command place the Wi-Fi or network name on the clipboard.":"இயக்கத்தில் இருந்தால் வை-ஃபை அல்லது நெட்வொர்க் பெயரை கிளிப்போர்டில் நகலெடுக்கும்.", "QuickInfo settings saved":"QuickInfo அமைப்புகள் சேமிக்கப்பட்டன"})
SPOKEN_TEXT["Telugu"].update({
"Screen Curtain status":"స్క్రీన్ కర్టెన్ స్థితి", "Wi-Fi status and SSID":"వై-ఫై స్థితి మరియు SSID", "Network connection type":"నెట్‌వర్క్ కనెక్షన్ రకం", "Copy computer name command":"కంప్యూటర్ పేరును కాపీ చేసే కమాండ్", "Copy network name command":"నెట్‌వర్క్ పేరును కాపీ చేసే కమాండ్", "Internet connectivity":"ఇంటర్నెట్ కనెక్టివిటీ", "IP address":"ఐపీ చిరునామా", "DNS servers":"DNS సర్వర్లు", "Default gateway":"డిఫాల్ట్ గేట్‌వే", "Battery information":"బ్యాటరీ సమాచారం", "System uptime":"సిస్టమ్ పనిచేసిన సమయం", "Windows version":"Windows వెర్షన్", "Memory information":"మెమరీ సమాచారం", "Processor information":"ప్రాసెసర్ సమాచారం", "Microphone":"మైక్రోఫోన్", "Bluetooth devices":"బ్లూటూత్ పరికరాలు", "Disk information":"డిస్క్ సమాచారం", "Quick system summary":"సిస్టమ్ సంక్షిప్త సమాచారం", "Clipboard content type":"క్లిప్‌బోర్డ్ కంటెంట్ రకం", "Keyboard layout":"కీబోర్డ్ లేఅవుట్", "Lock key status":"లాక్ కీ స్థితి", "NVDA cursor information":"NVDA కర్సర్ సమాచారం", "Allow the Copy Computer Name command":"కంప్యూటర్ పేరును కాపీ చేసే కమాండ్‌ను అనుమతించండి", "Allow the Copy Network Name command":"నెట్‌వర్క్ పేరును కాపీ చేసే కమాండ్‌ను అనుమతించండి", "Choose the language used for QuickInfo announcements and labels.":"QuickInfo ప్రకటనలు మరియు లేబుళ్ల కోసం భాషను ఎంచుకోండి.", "Choose Enabled or Disabled for copying the computer name.":"కంప్యూటర్ పేరును కాపీ చేయడానికి ప్రారంభించబడింది లేదా నిలిపివేయబడింది ఎంచుకోండి.", "Choose Enabled or Disabled for copying the network name.":"నెట్‌వర్క్ పేరును కాపీ చేయడానికి ప్రారంభించబడింది లేదా నిలిపివేయబడింది ఎంచుకోండి.", "Enabled lets the copy-computer-name command place the computer name on the clipboard.":"ప్రారంభిస్తే కంప్యూటర్ పేరు క్లిప్‌బోర్డ్‌కు కాపీ అవుతుంది.", "Enabled lets the copy-network-name command place the Wi-Fi or network name on the clipboard.":"ప్రారంభిస్తే వై-ఫై లేదా నెట్‌వర్క్ పేరు క్లిప్‌బోర్డ్‌కు కాపీ అవుతుంది.", "QuickInfo settings saved":"QuickInfo సెట్టింగ్‌లు సేవ్ అయ్యాయి"})
SPOKEN_TEXT["Malayalam"].update({
"Screen Curtain status":"സ്ക്രീൻ കർട്ടൻ നില", "Wi-Fi status and SSID":"വൈ-ഫൈ നിലയും SSIDയും", "Network connection type":"നെറ്റ്‌വർക്ക് കണക്ഷൻ തരം", "Copy computer name command":"കമ്പ്യൂട്ടർ പേര് പകർത്താനുള്ള കമാൻഡ്", "Copy network name command":"നെറ്റ്‌വർക്ക് പേര് പകർത്താനുള്ള കമാൻഡ്", "Internet connectivity":"ഇന്റർനെറ്റ് കണക്ഷൻ", "IP address":"ഐപി വിലാസം", "DNS servers":"ഡിഎൻഎസ് സെർവറുകൾ", "Default gateway":"ഡിഫോൾട്ട് ഗേറ്റ്‌വേ", "Battery information":"ബാറ്ററി വിവരം", "System uptime":"സിസ്റ്റം പ്രവർത്തിച്ച സമയം", "Windows version":"Windows പതിപ്പ്", "Memory information":"മെമ്മറി വിവരം", "Processor information":"പ്രോസസർ വിവരം", "Microphone":"മൈക്രോഫോൺ", "Bluetooth devices":"ബ്ലൂടൂത്ത് ഉപകരണങ്ങൾ", "Disk information":"ഡിസ്ക് വിവരം", "Quick system summary":"സിസ്റ്റം സംഗ്രഹം", "Clipboard content type":"ക്ലിപ്പ്ബോർഡ് ഉള്ളടക്ക തരം", "Keyboard layout":"കീബോർഡ് ലേയൗട്ട്", "Lock key status":"ലോക്ക് കീ നില", "NVDA cursor information":"NVDA കർസർ വിവരം", "Allow the Copy Computer Name command":"കമ്പ്യൂട്ടർ പേര് പകർത്താനുള്ള കമാൻഡ് അനുവദിക്കുക", "Allow the Copy Network Name command":"നെറ്റ്‌വർക്ക് പേര് പകർത്താനുള്ള കമാൻഡ് അനുവദിക്കുക", "Choose the language used for QuickInfo announcements and labels.":"QuickInfo അറിയിപ്പുകൾക്കും ലേബലുകൾക്കും ഉപയോഗിക്കുന്ന ഭാഷ തിരഞ്ഞെടുക്കുക.", "Choose Enabled or Disabled for copying the computer name.":"കമ്പ്യൂട്ടർ പേര് പകർത്താൻ പ്രവർത്തനക്ഷമം അല്ലെങ്കിൽ പ്രവർത്തനരഹിതം തിരഞ്ഞെടുക്കുക.", "Choose Enabled or Disabled for copying the network name.":"നെറ്റ്‌വർക്ക് പേര് പകർത്താൻ പ്രവർത്തനക്ഷമം അല്ലെങ്കിൽ പ്രവർത്തനരഹിതം തിരഞ്ഞെടുക്കുക.", "Enabled lets the copy-computer-name command place the computer name on the clipboard.":"പ്രവർത്തനക്ഷമമാക്കിയാൽ കമ്പ്യൂട്ടർ പേര് ക്ലിപ്പ്ബോർഡിലേക്ക് പകർത്തും.", "Enabled lets the copy-network-name command place the Wi-Fi or network name on the clipboard.":"പ്രവർത്തനക്ഷമമാക്കിയാൽ വൈ-ഫൈ അല്ലെങ്കിൽ നെറ്റ്‌വർക്ക് പേര് ക്ലിപ്പ്ബോർഡിലേക്ക് പകർത്തും.", "QuickInfo settings saved":"QuickInfo ക്രമീകരണങ്ങൾ സംരക്ഷിച്ചു"})

SPOKEN_TEXT['Hindi'].update({'Wi-Fi is not connected': 'वाई-फाई कनेक्ट नहीं है', 'Wi-Fi connected to ': 'वाई-फाई इस नेटवर्क से जुड़ा है: ', 'Signal strength is unavailable': 'सिग्नल की शक्ति उपलब्ध नहीं है', 'Signal strength ': 'सिग्नल की शक्ति ', 'Wi-Fi status check timed out': 'वाई-फाई स्थिति जांच का समय समाप्त हो गया', 'Unable to determine Wi-Fi information: ': 'वाई-फाई की जानकारी निर्धारित नहीं की जा सकी: ', '. Review cursor available.': '. रिव्यू कर्सर उपलब्ध है।', 'Connected through another network interface': 'अन्य नेटवर्क इंटरफ़ेस से जुड़ा है'})
SPOKEN_TEXT['Kannada'].update({'Wi-Fi is not connected': 'ವೈ-ಫೈ ಸಂಪರ್ಕಗೊಂಡಿಲ್ಲ', 'Wi-Fi connected to ': 'ವೈ-ಫೈ ಈ ನೆಟ್\u200cವರ್ಕ್\u200cಗೆ ಸಂಪರ್ಕಗೊಂಡಿದೆ: ', 'Signal strength is unavailable': 'ಸಿಗ್ನಲ್ ಶಕ್ತಿ ಲಭ್ಯವಿಲ್ಲ', 'Signal strength ': 'ಸಿಗ್ನಲ್ ಶಕ್ತಿ ', 'Wi-Fi status check timed out': 'ವೈ-ಫೈ ಸ್ಥಿತಿ ಪರಿಶೀಲನೆಯ ಸಮಯ ಮೀರಿದೆ', 'Unable to determine Wi-Fi information: ': 'ವೈ-ಫೈ ಮಾಹಿತಿ ನಿರ್ಧರಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ: ', '. Review cursor available.': '. ರಿವ್ಯೂ ಕರ್ಸರ್ ಲಭ್ಯವಿದೆ.', 'Connected through another network interface': 'ಇನ್ನೊಂದು ನೆಟ್\u200cವರ್ಕ್ ಇಂಟರ್\u200cಫೇಸ್ ಮೂಲಕ ಸಂಪರ್ಕಗೊಂಡಿದೆ'})
SPOKEN_TEXT['Tamil'].update({'Wi-Fi is not connected': 'வை-ஃபை இணைக்கப்படவில்லை', 'Wi-Fi connected to ': 'வை-ஃபை இந்த நெட்வொர்க்குடன் இணைக்கப்பட்டுள்ளது: ', 'Signal strength is unavailable': 'சிக்னல் வலிமை கிடைக்கவில்லை', 'Signal strength ': 'சிக்னல் வலிமை ', 'Wi-Fi status check timed out': 'வை-ஃபை நிலையைச் சரிபாரிக்கும் நேரம் முடிந்தது', 'Unable to determine Wi-Fi information: ': 'வை-ஃபை தகவலைக் கண்டறிய முடியவில்லை: ', '. Review cursor available.': '. ரிவ்யூ கர்சர் கிடைக்கிறது.', 'Connected through another network interface': 'மற்றொரு நெட்வொர்க் இடைமுகம் மூலம் இணைக்கப்பட்டுள்ளது'})
SPOKEN_TEXT['Telugu'].update({'Wi-Fi is not connected': 'వై-ఫై కనెక్ట్ కాలేదు', 'Wi-Fi connected to ': 'వై-ఫై ఈ నెట్\u200cవర్క్\u200cకు కనెక్ట్ అయింది: ', 'Signal strength is unavailable': 'సిగ్నల్ బలం అందుబాటులో లేదు', 'Signal strength ': 'సిగ్నల్ బలం ', 'Wi-Fi status check timed out': 'వై-ఫై స్థితి తనిఖీ సమయం ముగిసింది', 'Unable to determine Wi-Fi information: ': 'వై-ఫై సమాచారాన్ని గుర్తించలేకపోయాము: ', '. Review cursor available.': '. రివ్యూ కర్సర్ అందుబాటులో ఉంది.', 'Connected through another network interface': 'మరొక నెట్\u200cవర్క్ ఇంటర్\u200cఫేస్ ద్వారా కనెక్ట్ అయింది'})
SPOKEN_TEXT['Malayalam'].update({'Wi-Fi is not connected': 'വൈ-ഫൈ കണക്റ്റ് ചെയ്തിട്ടില്ല', 'Wi-Fi connected to ': 'വൈ-ഫൈ ഈ നെറ്റ്\u200cവർക്കിലേക്ക് കണക്റ്റ് ചെയ്തിരിക്കുന്നു: ', 'Signal strength is unavailable': 'സിഗ്നൽ ശക്തി ലഭ്യമല്ല', 'Signal strength ': 'സിഗ്നൽ ശക്തി ', 'Wi-Fi status check timed out': 'വൈ-ഫൈ നില പരിശോധിക്കാനുള്ള സമയം കഴിഞ്ഞു', 'Unable to determine Wi-Fi information: ': 'വൈ-ഫൈ വിവരം കണ്ടെത്താനായില്ല: ', '. Review cursor available.': '. റിവ്യൂ കർസർ ലഭ്യമാണ്.', 'Connected through another network interface': 'മറ്റൊരു നെറ്റ്\u200cവർക്ക് ഇന്റർഫേസ് വഴി കണക്റ്റ് ചെയ്തിരിക്കുന്നു'})

SPOKEN_TEXT['Hindi'].update({'excellent': 'बहुत अच्छा', 'good': 'अच्छा', 'fair': 'ठीक', 'weak': 'कमज़ोर', 'signal strength unavailable': 'सिग्नल की शक्ति उपलब्ध नहीं है', 'QuickInfo could not save the settings: ': 'QuickInfo सेटिंग सहेजी नहीं जा सकी: ', 'Unable to open QuickInfo Settings': 'QuickInfo सेटिंग नहीं खोली जा सकी', 'Wi-Fi connected to ': 'वाई-फाई इस नेटवर्क से जुड़ा है: '})
SPOKEN_TEXT['Kannada'].update({'excellent': 'ಅತ್ಯುತ್ತಮ', 'good': 'ಉತ್ತಮ', 'fair': 'ಸಾಧಾರಣ', 'weak': 'ದುರ್ಬಲ', 'signal strength unavailable': 'ಸಿಗ್ನಲ್ ಶಕ್ತಿ ಲಭ್ಯವಿಲ್ಲ', 'QuickInfo could not save the settings: ': 'QuickInfo ಸೆಟ್ಟಿಂಗ್\u200cಗಳನ್ನು ಉಳಿಸಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ: ', 'Unable to open QuickInfo Settings': 'QuickInfo ಸೆಟ್ಟಿಂಗ್\u200cಗಳನ್ನು ತೆರೆಯಲು ಸಾಧ್ಯವಾಗಲಿಲ್ಲ'})
SPOKEN_TEXT['Tamil'].update({'excellent': 'மிகச் சிறந்தது', 'good': 'நல்லது', 'fair': 'சுமாரானது', 'weak': 'பலவீனமானது', 'signal strength unavailable': 'சிக்னல் வலிமை கிடைக்கவில்லை', 'QuickInfo could not save the settings: ': 'QuickInfo அமைப்புகளைச் சேமிக்க முடியவில்லை: ', 'Unable to open QuickInfo Settings': 'QuickInfo அமைப்புகளைத் திறக்க முடியவில்லை'})
SPOKEN_TEXT['Telugu'].update({'excellent': 'అద్భుతం', 'good': 'మంచిది', 'fair': 'సాధారణం', 'weak': 'బలహీనంగా ఉంది', 'signal strength unavailable': 'సిగ్నల్ బలం అందుబాటులో లేదు', 'QuickInfo could not save the settings: ': 'QuickInfo సెట్టింగ్\u200cలను సేవ్ చేయలేకపోయాము: ', 'Unable to open QuickInfo Settings': 'QuickInfo సెట్టింగ్\u200cలను తెరవలేకపోయాము'})
SPOKEN_TEXT['Malayalam'].update({'excellent': 'മികച്ചത്', 'good': 'നല്ലത്', 'fair': 'ശരാശരി', 'weak': 'ദുർബലം', 'signal strength unavailable': 'സിഗ്നൽ ശക്തി ലഭ്യമല്ല', 'QuickInfo could not save the settings: ': 'QuickInfo ക്രമീകരണങ്ങൾ സംരക്ഷിക്കാനായില്ല: ', 'Unable to open QuickInfo Settings': 'QuickInfo ക്രമീകരണങ്ങൾ തുറക്കാനായില്ല'})


# QuickInfo 4.0 announcement translations
SPOKEN_TEXT["Hindi"] .update({'Hardware information. ': 'हार्डवेयर जानकारी। ', 'Hardware information unavailable': 'हार्डवेयर जानकारी उपलब्ध नहीं है', 'Battery health. ': 'बैटरी स्वास्थ्य। ', 'Battery health details are not exposed by this battery or firmware': 'इस बैटरी या फर्मवेयर से स्वास्थ्य विवरण उपलब्ध नहीं हैं', 'Storage drives. ': 'स्टोरेज ड्राइव। ', 'Display and graphics. ': 'डिस्प्ले और ग्राफिक्स। ', 'Network diagnostics. ': 'नेटवर्क जांच। ', 'Checking network diagnostics': 'नेटवर्क की जांच की जा रही है', 'NVDA speech information. ': 'NVDA वाक् जानकारी। ', 'Focused control details. ': 'फ़ोकस किए गए नियंत्रण का विवरण। ', 'Installed NVDA add-ons. ': 'स्थापित NVDA ऐड-ऑन। ', 'Open applications. ': 'खुले एप्लिकेशन। '})
SPOKEN_TEXT["Kannada"] .update({'Hardware information. ': 'ಹಾರ್ಡ್\u200cವೇರ್ ಮಾಹಿತಿ. ', 'Hardware information unavailable': 'ಹಾರ್ಡ್\u200cವೇರ್ ಮಾಹಿತಿ ಲಭ್ಯವಿಲ್ಲ', 'Battery health. ': 'ಬ್ಯಾಟರಿ ಆರೋಗ್ಯ. ', 'Battery health details are not exposed by this battery or firmware': 'ಈ ಬ್ಯಾಟರಿ ಅಥವಾ ಫರ್ಮ್\u200cವೇರ್ ಆರೋಗ್ಯ ವಿವರಗಳನ್ನು ನೀಡುವುದಿಲ್ಲ', 'Storage drives. ': 'ಸ್ಟೋರೇಜ್ ಡ್ರೈವ್\u200cಗಳು. ', 'Display and graphics. ': 'ಡಿಸ್ಪ್ಲೇ ಮತ್ತು ಗ್ರಾಫಿಕ್ಸ್. ', 'Network diagnostics. ': 'ನೆಟ್\u200cವರ್ಕ್ ಪರಿಶೀಲನೆ. ', 'Checking network diagnostics': 'ನೆಟ್\u200cವರ್ಕ್ ಪರಿಶೀಲಿಸಲಾಗುತ್ತಿದೆ', 'NVDA speech information. ': 'NVDA ಮಾತಿನ ಮಾಹಿತಿ. ', 'Focused control details. ': 'ಫೋಕಸ್ ಮಾಡಿದ ನಿಯಂತ್ರಣದ ವಿವರಗಳು. ', 'Installed NVDA add-ons. ': 'ಸ್ಥಾಪಿತ NVDA ಆಡ್-ಆನ್\u200cಗಳು. ', 'Open applications. ': 'ತೆರೆದ ಅಪ್ಲಿಕೇಶನ್\u200cಗಳು. '})
SPOKEN_TEXT["Tamil"] .update({'Hardware information. ': 'வன்பொருள் தகவல். ', 'Hardware information unavailable': 'வன்பொருள் தகவல் கிடைக்கவில்லை', 'Battery health. ': 'மின்கலன் நிலை. ', 'Battery health details are not exposed by this battery or firmware': 'இந்த மின்கலன் அல்லது firmware நிலை விவரங்களை வழங்கவில்லை', 'Storage drives. ': 'சேமிப்பக இயக்கிகள். ', 'Display and graphics. ': 'காட்சி மற்றும் கிராபிக்ஸ். ', 'Network diagnostics. ': 'பிணைய சோதனை. ', 'Checking network diagnostics': 'பிணையம் சோதிக்கப்படுகிறது', 'NVDA speech information. ': 'NVDA பேச்சு தகவல். ', 'Focused control details. ': 'கவனத்தில் உள்ள கட்டுப்பாட்டு விவரங்கள். ', 'Installed NVDA add-ons. ': 'நிறுவப்பட்ட NVDA துணை நிரல்கள். ', 'Open applications. ': 'திறந்த பயன்பாடுகள். '})
SPOKEN_TEXT["Telugu"] .update({'Hardware information. ': 'హార్డ్\u200cవేర్ సమాచారం. ', 'Hardware information unavailable': 'హార్డ్\u200cవేర్ సమాచారం అందుబాటులో లేదు', 'Battery health. ': 'బ్యాటరీ ఆరోగ్యం. ', 'Battery health details are not exposed by this battery or firmware': 'ఈ బ్యాటరీ లేదా ఫర్మ్\u200cవేర్ ఆరోగ్య వివరాలను అందించదు', 'Storage drives. ': 'నిల్వ డ్రైవ్\u200cలు. ', 'Display and graphics. ': 'డిస్\u200cప్లే మరియు గ్రాఫిక్స్. ', 'Network diagnostics. ': 'నెట్\u200cవర్క్ నిర్ధారణ. ', 'Checking network diagnostics': 'నెట్\u200cవర్క్\u200cను తనిఖీ చేస్తోంది', 'NVDA speech information. ': 'NVDA ప్రసంగ సమాచారం. ', 'Focused control details. ': 'ఫోకస్ చేసిన నియంత్రణ వివరాలు. ', 'Installed NVDA add-ons. ': 'ఇన్\u200cస్టాల్ చేసిన NVDA యాడ్-ఆన్\u200cలు. ', 'Open applications. ': 'తెరిచిన అప్లికేషన్\u200cలు. '})
SPOKEN_TEXT["Malayalam"] .update({'Hardware information. ': 'ഹാർഡ്\u200cവെയർ വിവരം. ', 'Hardware information unavailable': 'ഹാർഡ്\u200cവെയർ വിവരം ലഭ്യമല്ല', 'Battery health. ': 'ബാറ്ററി ആരോഗ്യം. ', 'Battery health details are not exposed by this battery or firmware': 'ഈ ബാറ്ററിയോ ഫേംവെയറോ ആരോഗ്യവിവരങ്ങൾ നൽകുന്നില്ല', 'Storage drives. ': 'സ്റ്റോറേജ് ഡ്രൈവുകൾ. ', 'Display and graphics. ': 'ഡിസ്\u200cപ്ലേയും ഗ്രാഫിക്സും. ', 'Network diagnostics. ': 'നെറ്റ്\u200cവർക്ക് പരിശോധന. ', 'Checking network diagnostics': 'നെറ്റ്\u200cവർക്ക് പരിശോധിക്കുന്നു', 'NVDA speech information. ': 'NVDA സംഭാഷണ വിവരം. ', 'Focused control details. ': 'ഫോക്കസ് ചെയ്ത നിയന്ത്രണത്തിന്റെ വിവരങ്ങൾ. ', 'Installed NVDA add-ons. ': 'ഇൻസ്റ്റാൾ ചെയ്ത NVDA ആഡ്-ഓണുകൾ. ', 'Open applications. ': 'തുറന്ന ആപ്ലിക്കേഷനുകൾ. '})


# Additional translations for the expanded diagnostics
SPOKEN_TEXT['Hindi'].update({'Manufacturer ': 'निर्माता ', 'Model ': 'मॉडल ', 'System type ': 'सिस्टम प्रकार ', 'BIOS version ': 'BIOS संस्करण ', 'BIOS date ': 'BIOS दिनांक ', 'Motherboard ': 'मदरबोर्ड ', 'RAM modules ': 'RAM मॉड्यूल ', 'Memory slots ': 'मेमोरी स्लॉट ', 'RAM speed ': 'RAM गति ', 'CPU cores ': 'CPU कोर ', 'Logical processors ': 'लॉजिकल प्रोसेसर ', 'Design capacity ': 'डिज़ाइन क्षमता ', 'Full charge capacity ': 'पूर्ण चार्ज क्षमता ', 'Estimated battery wear ': 'अनुमानित बैटरी घिसाव ', 'Charge cycles ': 'चार्ज चक्र ', 'milliwatt hours': 'मिलीवॉट-घंटे', 'Storage drives. ': 'स्टोरेज ड्राइव। ', 'Physical disk ': 'भौतिक डिस्क ', 'health ': 'स्वास्थ्य ', 'media type unknown': 'मीडिया प्रकार अज्ञात', 'Display and graphics. ': 'डिस्प्ले और ग्राफिक्स। ', 'resolution ': 'रिज़ॉल्यूशन ', 'by ': 'गुणा ', 'refresh rate ': 'रिफ्रेश दर ', 'hertz': 'हर्ट्ज़', 'driver ': 'ड्राइवर ', 'Network diagnostics. ': 'नेटवर्क जांच। ', 'link speed ': 'लिंक गति ', 'ping replies received': 'पिंग उत्तर प्राप्त हुए', 'Average latency ': 'औसत विलंबता ', 'milliseconds': 'मिलीसेकंड', 'Packet loss ': 'पैकेट हानि ', 'percent': 'प्रतिशत', 'NVDA speech information. ': 'NVDA वाक् जानकारी। ', 'Synthesizer ': 'सिंथेसाइज़र ', 'Voice ': 'आवाज़ ', 'Braille display ': 'ब्रेल डिस्प्ले ', 'No active braille display detected': 'कोई सक्रिय ब्रेल डिस्प्ले नहीं मिला', 'Focused control details. ': 'फ़ोकस किए गए नियंत्रण का विवरण। ', 'Name ': 'नाम ', 'Role ': 'भूमिका ', 'Value ': 'मान ', 'Description ': 'विवरण ', 'States ': 'स्थितियाँ ', 'Application ': 'एप्लिकेशन ', 'Installed NVDA add-ons. ': 'स्थापित NVDA ऐड-ऑन। ', ' version ': ' संस्करण ', 'Open applications. ': 'खुले एप्लिकेशन। '})
SPOKEN_TEXT['Kannada'].update({'Manufacturer ': 'ತಯಾರಕರು ', 'Model ': 'ಮಾದರಿ ', 'System type ': 'ಸಿಸ್ಟಮ್ ಪ್ರಕಾರ ', 'BIOS version ': 'BIOS ಆವೃತ್ತಿ ', 'BIOS date ': 'BIOS ದಿನಾಂಕ ', 'Motherboard ': 'ಮದರ್\u200cಬೋರ್ಡ್ ', 'RAM modules ': 'RAM ಮಾಡ್ಯೂಲ್\u200cಗಳು ', 'Memory slots ': 'ಮೆಮೊರಿ ಸ್ಲಾಟ್\u200cಗಳು ', 'RAM speed ': 'RAM ವೇಗ ', 'CPU cores ': 'CPU ಕೋರ್\u200cಗಳು ', 'Logical processors ': 'ಲಾಜಿಕಲ್ ಪ್ರೊಸೆಸರ್\u200cಗಳು ', 'Design capacity ': 'ವಿನ್ಯಾಸ ಸಾಮರ್ಥ್ಯ ', 'Full charge capacity ': 'ಪೂರ್ಣ ಚಾರ್ಜ್ ಸಾಮರ್ಥ್ಯ ', 'Estimated battery wear ': 'ಅಂದಾಜು ಬ್ಯಾಟರಿ ಕ್ಷಯ ', 'Charge cycles ': 'ಚಾರ್ಜ್ ಚಕ್ರಗಳು ', 'milliwatt hours': 'ಮಿಲಿವ್ಯಾಟ್ ಗಂಟೆಗಳು', 'Storage drives. ': 'ಸ್ಟೋರೇಜ್ ಡ್ರೈವ್\u200cಗಳು. ', 'Physical disk ': 'ಭೌತಿಕ ಡಿಸ್ಕ್ ', 'health ': 'ಆರೋಗ್ಯ ', 'media type unknown': 'ಮಾಧ್ಯಮ ಪ್ರಕಾರ ತಿಳಿದಿಲ್ಲ', 'Display and graphics. ': 'ಡಿಸ್ಪ್ಲೇ ಮತ್ತು ಗ್ರಾಫಿಕ್ಸ್. ', 'resolution ': 'ರೆಸಲ್ಯೂಶನ್ ', 'by ': 'ಗೆ ', 'refresh rate ': 'ರಿಫ್ರೆಶ್ ದರ ', 'hertz': 'ಹರ್ಟ್ಜ್', 'driver ': 'ಡ್ರೈವರ್ ', 'Network diagnostics. ': 'ನೆಟ್\u200cವರ್ಕ್ ಪರಿಶೀಲನೆ. ', 'link speed ': 'ಲಿಂಕ್ ವೇಗ ', 'ping replies received': 'ಪಿಂಗ್ ಪ್ರತಿಕ್ರಿಯೆಗಳು ಬಂದಿವೆ', 'Average latency ': 'ಸರಾಸರಿ ವಿಳಂಬ ', 'milliseconds': 'ಮಿಲಿಸೆಕೆಂಡುಗಳು', 'Packet loss ': 'ಪ್ಯಾಕೆಟ್ ನಷ್ಟ ', 'percent': 'ಶೇಕಡಾ', 'NVDA speech information. ': 'NVDA ಮಾತಿನ ಮಾಹಿತಿ. ', 'Synthesizer ': 'ಸಿಂಥಸೈಜರ್ ', 'Voice ': 'ಧ್ವನಿ ', 'Braille display ': 'ಬ್ರೈಲ್ ಡಿಸ್ಪ್ಲೇ ', 'No active braille display detected': 'ಸಕ್ರಿಯ ಬ್ರೈಲ್ ಡಿಸ್ಪ್ಲೇ ಪತ್ತೆಯಾಗಿಲ್ಲ', 'Focused control details. ': 'ಫೋಕಸ್ ಮಾಡಿದ ನಿಯಂತ್ರಣದ ವಿವರಗಳು. ', 'Name ': 'ಹೆಸರು ', 'Role ': 'ಪಾತ್ರ ', 'Value ': 'ಮೌಲ್ಯ ', 'Description ': 'ವಿವರಣೆ ', 'States ': 'ಸ್ಥಿತಿಗಳು ', 'Application ': 'ಅಪ್ಲಿಕೇಶನ್ ', 'Installed NVDA add-ons. ': 'ಸ್ಥಾಪಿತ NVDA ಆಡ್-ಆನ್\u200cಗಳು. ', ' version ': ' ಆವೃತ್ತಿ ', 'Open applications. ': 'ತೆರೆದ ಅಪ್ಲಿಕೇಶನ್\u200cಗಳು. '})
SPOKEN_TEXT['Tamil'].update({'Manufacturer ': 'உற்பத்தியாளர் ', 'Model ': 'மாதிரி ', 'System type ': 'கணினி வகை ', 'BIOS version ': 'BIOS பதிப்பு ', 'BIOS date ': 'BIOS தேதி ', 'Motherboard ': 'மதர்போர்டு ', 'RAM modules ': 'RAM தொகுதிகள் ', 'Memory slots ': 'நினைவக ஸ்லாட்டுகள் ', 'RAM speed ': 'RAM வேகம் ', 'CPU cores ': 'CPU கோர்கள் ', 'Logical processors ': 'தருக்க செயலிகள் ', 'Design capacity ': 'வடிவமைப்பு திறன் ', 'Full charge capacity ': 'முழு மின்னேற்ற திறன் ', 'Estimated battery wear ': 'மதிப்பிடப்பட்ட மின்கல தேய்மானம் ', 'Charge cycles ': 'மின்னேற்ற சுழற்சிகள் ', 'milliwatt hours': 'மில்லிவாட் மணிநேரம்', 'Storage drives. ': 'சேமிப்பக இயக்கிகள். ', 'Physical disk ': 'இயற்பியல் வட்டு ', 'health ': 'நிலை ', 'media type unknown': 'ஊடக வகை தெரியவில்லை', 'Display and graphics. ': 'காட்சி மற்றும் கிராபிக்ஸ். ', 'resolution ': 'தெளிவுத்திறன் ', 'by ': 'க்கு ', 'refresh rate ': 'புதுப்பிப்பு வீதம் ', 'hertz': 'ஹெர்ட்ஸ்', 'driver ': 'இயக்கி ', 'Network diagnostics. ': 'பிணைய சோதனை. ', 'link speed ': 'இணைப்பு வேகம் ', 'ping replies received': 'பிங் பதில்கள் பெறப்பட்டன', 'Average latency ': 'சராசரி தாமதம் ', 'milliseconds': 'மில்லி விநாடிகள்', 'Packet loss ': 'தரவு இழப்பு ', 'percent': 'சதவீதம்', 'NVDA speech information. ': 'NVDA பேச்சு தகவல். ', 'Synthesizer ': 'பேச்சு இயந்திரம் ', 'Voice ': 'குரல் ', 'Braille display ': 'பிரெயில் காட்சி ', 'No active braille display detected': 'செயலில் உள்ள பிரெயில் காட்சி இல்லை', 'Focused control details. ': 'கவனக் கட்டுப்பாட்டு விவரங்கள். ', 'Name ': 'பெயர் ', 'Role ': 'பங்கு ', 'Value ': 'மதிப்பு ', 'Description ': 'விளக்கம் ', 'States ': 'நிலைகள் ', 'Application ': 'பயன்பாடு ', 'Installed NVDA add-ons. ': 'நிறுவப்பட்ட NVDA துணை நிரல்கள். ', ' version ': ' பதிப்பு ', 'Open applications. ': 'திறந்த பயன்பாடுகள். '})
SPOKEN_TEXT['Telugu'].update({'Manufacturer ': 'తయారీదారు ', 'Model ': 'మోడల్ ', 'System type ': 'సిస్టమ్ రకం ', 'BIOS version ': 'BIOS వెర్షన్ ', 'BIOS date ': 'BIOS తేదీ ', 'Motherboard ': 'మదర్\u200cబోర్డ్ ', 'RAM modules ': 'RAM మాడ్యూల్\u200cలు ', 'Memory slots ': 'మెమరీ స్లాట్\u200cలు ', 'RAM speed ': 'RAM వేగం ', 'CPU cores ': 'CPU కోర్లు ', 'Logical processors ': 'లాజికల్ ప్రాసెసర్\u200cలు ', 'Design capacity ': 'రూపకల్పన సామర్థ్యం ', 'Full charge capacity ': 'పూర్తి ఛార్జ్ సామర్థ్యం ', 'Estimated battery wear ': 'అంచనా బ్యాటరీ అరుగుదల ', 'Charge cycles ': 'ఛార్జ్ చక్రాలు ', 'milliwatt hours': 'మిల్లీవాట్ గంటలు', 'Storage drives. ': 'నిల్వ డ్రైవ్\u200cలు. ', 'Physical disk ': 'భౌతిక డిస్క్ ', 'health ': 'ఆరోగ్యం ', 'media type unknown': 'మీడియా రకం తెలియదు', 'Display and graphics. ': 'డిస్\u200cప్లే మరియు గ్రాఫిక్స్. ', 'resolution ': 'రిజల్యూషన్ ', 'by ': 'బై ', 'refresh rate ': 'రిఫ్రెష్ రేట్ ', 'hertz': 'హెర్ట్జ్', 'driver ': 'డ్రైవర్ ', 'Network diagnostics. ': 'నెట్\u200cవర్క్ నిర్ధారణ. ', 'link speed ': 'లింక్ వేగం ', 'ping replies received': 'పింగ్ సమాధానాలు అందాయి', 'Average latency ': 'సగటు ఆలస్యం ', 'milliseconds': 'మిల్లీసెకన్లు', 'Packet loss ': 'ప్యాకెట్ నష్టం ', 'percent': 'శాతం', 'NVDA speech information. ': 'NVDA ప్రసంగ సమాచారం. ', 'Synthesizer ': 'సింథసైజర్ ', 'Voice ': 'వాయిస్ ', 'Braille display ': 'బ్రెయిల్ డిస్\u200cప్లే ', 'No active braille display detected': 'క్రియాశీల బ్రెయిల్ డిస్\u200cప్లే కనుగొనబడలేదు', 'Focused control details. ': 'ఫోకస్ నియంత్రణ వివరాలు. ', 'Name ': 'పేరు ', 'Role ': 'పాత్ర ', 'Value ': 'విలువ ', 'Description ': 'వివరణ ', 'States ': 'స్థితులు ', 'Application ': 'అప్లికేషన్ ', 'Installed NVDA add-ons. ': 'ఇన్\u200cస్టాల్ చేసిన NVDA యాడ్-ఆన్\u200cలు. ', ' version ': ' వెర్షన్ ', 'Open applications. ': 'తెరిచిన అప్లికేషన్\u200cలు. '})
SPOKEN_TEXT['Malayalam'].update({'Manufacturer ': 'നിർമ്മാതാവ് ', 'Model ': 'മോഡൽ ', 'System type ': 'സിസ്റ്റം തരം ', 'BIOS version ': 'BIOS പതിപ്പ് ', 'BIOS date ': 'BIOS തീയതി ', 'Motherboard ': 'മദർബോർഡ് ', 'RAM modules ': 'RAM മൊഡ്യൂളുകൾ ', 'Memory slots ': 'മെമ്മറി സ്ലോട്ടുകൾ ', 'RAM speed ': 'RAM വേഗത ', 'CPU cores ': 'CPU കോറുകൾ ', 'Logical processors ': 'ലോജിക്കൽ പ്രോസസ്സറുകൾ ', 'Design capacity ': 'ഡിസൈൻ ശേഷി ', 'Full charge capacity ': 'പൂർണ്ണ ചാർജ് ശേഷി ', 'Estimated battery wear ': 'കണക്കാക്കിയ ബാറ്ററി തേയ്മാനം ', 'Charge cycles ': 'ചാർജ് സൈക്കിളുകൾ ', 'milliwatt hours': 'മില്ലിവാട്ട് മണിക്കൂർ', 'Storage drives. ': 'സ്റ്റോറേജ് ഡ്രൈവുകൾ. ', 'Physical disk ': 'ഫിസിക്കൽ ഡിസ്ക് ', 'health ': 'ആരോഗ്യം ', 'media type unknown': 'മീഡിയ തരം ലഭ്യമല്ല', 'Display and graphics. ': 'ഡിസ്\u200cപ്ലേയും ഗ്രാഫിക്സും. ', 'resolution ': 'റെസല്യൂഷൻ ', 'by ': 'ബൈ ', 'refresh rate ': 'റിഫ്രഷ് നിരക്ക് ', 'hertz': 'ഹെർട്സ്', 'driver ': 'ഡ്രൈവർ ', 'Network diagnostics. ': 'നെറ്റ്\u200cവർക്ക് പരിശോധന. ', 'link speed ': 'ലിങ്ക് വേഗത ', 'ping replies received': 'പിംഗ് മറുപടികൾ ലഭിച്ചു', 'Average latency ': 'ശരാശരി ലേറ്റൻസി ', 'milliseconds': 'മില്ലിസെക്കൻഡ്', 'Packet loss ': 'പാക്കറ്റ് നഷ്ടം ', 'percent': 'ശതമാനം', 'NVDA speech information. ': 'NVDA സംഭാഷണ വിവരം. ', 'Synthesizer ': 'സിന്തസൈസർ ', 'Voice ': 'ശബ്ദം ', 'Braille display ': 'ബ്രെയിൽ ഡിസ്പ്ലേ ', 'No active braille display detected': 'സജീവമായ ബ്രെയിൽ ഡിസ്പ്ലേ കണ്ടെത്തിയില്ല', 'Focused control details. ': 'ഫോക്കസ് ചെയ്ത നിയന്ത്രണത്തിന്റെ വിവരങ്ങൾ. ', 'Name ': 'പേര് ', 'Role ': 'പങ്ക് ', 'Value ': 'മൂല്യം ', 'Description ': 'വിവരണം ', 'States ': 'അവസ്ഥകൾ ', 'Application ': 'ആപ്ലിക്കേഷൻ ', 'Installed NVDA add-ons. ': 'ഇൻസ്റ്റാൾ ചെയ്ത NVDA ആഡ്-ഓണുകൾ. ', ' version ': ' പതിപ്പ് ', 'Open applications. ': 'തുറന്ന ആപ്ലിക്കേഷനുകൾ. '})


SPOKEN_TEXT["Hindi"].update({
 "Volume and microphone status. ":"वॉल्यूम और माइक्रोफ़ोन की स्थिति। ", "Output volume ":"आउटपुट वॉल्यूम ", "Microphone volume ":"माइक्रोफ़ोन वॉल्यूम ", "percent, ":"प्रतिशत, ", "muted":"म्यूट है", "unmuted":"अनम्यूट है", "Display scaling ":"डिस्प्ले स्केलिंग ", "Internet speed test complete. Download ":"इंटरनेट गति परीक्षण पूरा। डाउनलोड ", "Upload ":"अपलोड ", "megabits per second":"मेगाबिट प्रति सेकंड", "Starting internet speed test. Approximately three megabytes will be downloaded and half a megabyte uploaded.":"इंटरनेट गति परीक्षण शुरू हो रहा है। लगभग तीन मेगाबाइट डाउनलोड और आधा मेगाबाइट अपलोड होगा।", "Internet speed test failed or timed out":"इंटरनेट गति परीक्षण विफल हुआ या समय समाप्त हो गया", "This QuickInfo command is disabled in settings":"यह QuickInfo कमांड सेटिंग में बंद है"})
SPOKEN_TEXT["Kannada"].update({
 "Volume and microphone status. ":"ವಾಲ್ಯೂಮ್ ಮತ್ತು ಮೈಕ್ರೊಫೋನ್ ಸ್ಥಿತಿ. ", "Output volume ":"ಔಟ್‌ಪುಟ್ ವಾಲ್ಯೂಮ್ ", "Microphone volume ":"ಮೈಕ್ರೊಫೋನ್ ವಾಲ್ಯೂಮ್ ", "percent, ":"ಶೇಕಡಾ, ", "muted":"ಮ್ಯೂಟ್ ಆಗಿದೆ", "unmuted":"ಮ್ಯೂಟ್ ಆಗಿಲ್ಲ", "Display scaling ":"ಡಿಸ್‌ಪ್ಲೇ ಸ್ಕೇಲಿಂಗ್ ", "Internet speed test complete. Download ":"ಇಂಟರ್ನೆಟ್ ವೇಗ ಪರೀಕ್ಷೆ ಪೂರ್ಣಗೊಂಡಿದೆ. ಡೌನ್‌ಲೋಡ್ ", "Upload ":"ಅಪ್‌ಲೋಡ್ ", "megabits per second":"ಮೆಗಾಬಿಟ್ ಪ್ರತಿ ಸೆಕೆಂಡ್", "Starting internet speed test. Approximately three megabytes will be downloaded and half a megabyte uploaded.":"ಇಂಟರ್ನೆಟ್ ವೇಗ ಪರೀಕ್ಷೆ ಆರಂಭವಾಗಿದೆ. ಸುಮಾರು ಮೂರು ಮೆಗಾಬೈಟ್ ಡೌನ್‌ಲೋಡ್ ಮತ್ತು ಅರ್ಧ ಮೆಗಾಬೈಟ್ ಅಪ್‌ಲೋಡ್ ಆಗುತ್ತದೆ.", "Internet speed test failed or timed out":"ಇಂಟರ್ನೆಟ್ ವೇಗ ಪರೀಕ್ಷೆ ವಿಫಲವಾಗಿದೆ ಅಥವಾ ಸಮಯ ಮೀರಿದೆ", "This QuickInfo command is disabled in settings":"ಈ QuickInfo ಕಮಾಂಡ್ ಸೆಟ್ಟಿಂಗ್‌ಗಳಲ್ಲಿ ನಿಷ್ಕ್ರಿಯವಾಗಿದೆ"})
SPOKEN_TEXT["Tamil"].update({
 "Volume and microphone status. ":"ஒலி அளவு மற்றும் ஒலிவாங்கி நிலை. ", "Output volume ":"வெளியீட்டு ஒலி அளவு ", "Microphone volume ":"ஒலிவாங்கி ஒலி அளவு ", "percent, ":"சதவீதம், ", "muted":"ஒலியடக்கப்பட்டுள்ளது", "unmuted":"ஒலியடக்கம் நீக்கப்பட்டுள்ளது", "Display scaling ":"காட்சி அளவாக்கம் ", "Internet speed test complete. Download ":"இணைய வேகச் சோதனை முடிந்தது. பதிவிறக்கம் ", "Upload ":"பதிவேற்றம் ", "megabits per second":"மெகாபிட் வினாடிக்கு", "Starting internet speed test. Approximately three megabytes will be downloaded and half a megabyte uploaded.":"இணைய வேகச் சோதனை தொடங்குகிறது. சுமார் மூன்று மெகாபைட் பதிவிறக்கமும் அரை மெகாபைட் பதிவேற்றமும் செய்யப்படும்.", "Internet speed test failed or timed out":"இணைய வேகச் சோதனை தோல்வியடைந்தது அல்லது நேரம் முடிந்தது", "This QuickInfo command is disabled in settings":"இந்த QuickInfo கட்டளை அமைப்புகளில் முடக்கப்பட்டுள்ளது"})
SPOKEN_TEXT["Telugu"].update({
 "Volume and microphone status. ":"వాల్యూమ్ మరియు మైక్రోఫోన్ స్థితి. ", "Output volume ":"అవుట్‌పుట్ వాల్యూమ్ ", "Microphone volume ":"మైక్రోఫోన్ వాల్యూమ్ ", "percent, ":"శాతం, ", "muted":"మ్యూట్ చేయబడింది", "unmuted":"మ్యూట్ చేయబడలేదు", "Display scaling ":"డిస్‌ప్లే స్కేలింగ్ ", "Internet speed test complete. Download ":"ఇంటర్నెట్ వేగ పరీక్ష పూర్తయింది. డౌన్‌లోడ్ ", "Upload ":"అప్‌లోడ్ ", "megabits per second":"సెకనుకు మెగాబిట్లు", "Starting internet speed test. Approximately three megabytes will be downloaded and half a megabyte uploaded.":"ఇంటర్నెట్ వేగ పరీక్ష ప్రారంభమవుతోంది. సుమారు మూడు మెగాబైట్లు డౌన్‌లోడ్, అర మెగాబైట్ అప్‌లోడ్ అవుతుంది.", "Internet speed test failed or timed out":"ఇంటర్నెట్ వేగ పరీక్ష విఫలమైంది లేదా సమయం ముగిసింది", "This QuickInfo command is disabled in settings":"ఈ QuickInfo ఆదేశం సెట్టింగ్‌లలో నిలిపివేయబడింది"})
SPOKEN_TEXT["Malayalam"].update({
 "Volume and microphone status. ":"വോളിയത്തിന്റെയും മൈക്രോഫോണിന്റെയും നില. ", "Output volume ":"ഔട്ട്പുട്ട് വോളിയം ", "Microphone volume ":"മൈക്രോഫോൺ വോളിയം ", "percent, ":"ശതമാനം, ", "muted":"മ്യൂട്ട് ചെയ്തിരിക്കുന്നു", "unmuted":"മ്യൂട്ട് ചെയ്തിട്ടില്ല", "Display scaling ":"ഡിസ്‌പ്ലേ സ്കെയിലിംഗ് ", "Internet speed test complete. Download ":"ഇന്റർനെറ്റ് വേഗ പരിശോധന പൂർത്തിയായി. ഡൗൺലോഡ് ", "Upload ":"അപ്‌ലോഡ് ", "megabits per second":"മെഗാബിറ്റ് ഓരോ സെക്കൻഡിലും", "Starting internet speed test. Approximately three megabytes will be downloaded and half a megabyte uploaded.":"ഇന്റർനെറ്റ് വേഗ പരിശോധന ആരംഭിക്കുന്നു. ഏകദേശം മൂന്ന് മെഗാബൈറ്റ് ഡൗൺലോഡും അര മെഗാബൈറ്റ് അപ്‌ലോഡും നടക്കും.", "Internet speed test failed or timed out":"ഇന്റർനെറ്റ് വേഗ പരിശോധന പരാജയപ്പെട്ടു അല്ലെങ്കിൽ സമയപരിധി കഴിഞ്ഞു", "This QuickInfo command is disabled in settings":"ഈ QuickInfo കമാൻഡ് ക്രമീകരണങ്ങളിൽ പ്രവർത്തനരഹിതമാണ്"})

def localize_message(message):
    language = config.conf.get("quickInfo", {}).get("language", "English")
    translations = SPOKEN_TEXT.get(language, {})
    if not translations:
        return message
    # Replace longer phrases first so shorter fragments do not break them.
    for source in sorted(translations, key=len, reverse=True):
        message = message.replace(source, translations[source])
    return message

def announce(message):
    ui.message(localize_message(str(message)))

COMMANDS = [
    ("Screen Curtain status", "screenCurtainInfo"),
    ("Wi-Fi status and SSID", "wifiInfo"), ("Network connection type", "networkType"),
    ("Default audio output device", "audioOutputDevice"), ("Computer name", "computerName"),
    ("Copy computer name command", "copyComputerName"), ("Network name", "networkName"),
    ("Copy network name command", "copyNetworkName"), ("Internet connectivity", "internetStatus"),
    ("IP address", "ipAddress"), ("DNS servers", "dnsServers"), ("Default gateway", "defaultGateway"),
    ("Battery information", "batteryInfo"), ("System uptime", "systemUptime"),
    ("Windows version", "windowsInfo"), ("Memory information", "memoryInfo"),
    ("Processor information", "processorInfo"), ("Microphone", "microphoneDevice"),
    ("Bluetooth devices", "bluetoothInfo"), ("Disk information", "diskInfo"),
    ("Quick system summary", "systemSummary"), ("Clipboard content type", "clipboardType"),
    ("Keyboard layout", "keyboardLayout"), ("Lock key status", "lockKeyStatus"),
    ("NVDA cursor information", "nvdaCursor"),
    ("Hardware and BIOS information", "hardwareInfo"),
    ("Battery health information", "batteryHealth"),
    ("All storage drives", "allDrives"),
    ("Display and graphics information", "displayInfo"),
    ("Network diagnostics", "networkDiagnostics"),
    ("NVDA speech information", "nvdaSpeechInfo"),
    ("Current focus details", "focusInspector"),
    ("Installed NVDA add-ons", "addonInventory"),
    ("Open applications", "runningApps"),
    ("Volume and microphone status", "audioStatus"),
    ("Display scaling", "displayScaling"),
    ("Internet download and upload speed", "internetSpeedTest"),
 ]

COMMAND_CATEGORIES = {
    "screenCurtainInfo": "Accessibility", "nvdaCursor": "Accessibility", "focusInspector": "Accessibility",
    "nvdaSpeechInfo": "NVDA", "addonInventory": "NVDA",
    "wifiInfo": "Network", "networkType": "Network", "internetStatus": "Network",
    "ipAddress": "Network", "dnsServers": "Network", "defaultGateway": "Network",
    "networkDiagnostics": "Network", "internetSpeedTest": "Network", "networkName": "Network",
    "copyNetworkName": "Network", "audioOutputDevice": "Audio", "microphoneDevice": "Audio",
    "audioStatus": "Audio", "systemSummary": "System", "computerName": "System",
    "copyComputerName": "System", "batteryInfo": "System", "batteryHealth": "System",
    "systemUptime": "System", "windowsInfo": "System", "memoryInfo": "System",
    "processorInfo": "System", "hardwareInfo": "System", "diskInfo": "System",
    "allDrives": "System", "displayInfo": "System", "displayScaling": "System",
    "bluetoothInfo": "System", "runningApps": "System", "clipboardType": "System",
    "keyboardLayout": "System", "lockKeyStatus": "System",
}

class QuickInfoSettingsDialog(wx.Dialog):
    def __init__(self, parent):
        super(QuickInfoSettingsDialog, self).__init__(
            parent, title=tr("QuickInfo Settings"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )
        panel = wx.ScrolledWindow(self, style=wx.VSCROLL)
        panel.SetScrollRate(0, 12)
        sizer = wx.BoxSizer(wx.VERTICAL)
        opts = config.conf.get("quickInfo", {})

        languageLabel = wx.StaticText(panel, label=tr("Language for QuickInfo"))
        self.languageChoice = wx.Choice(panel, choices=LANGUAGES)
        current = opts.get("language", "English")
        self.languageChoice.SetSelection(
            LANGUAGES.index(current) if current in LANGUAGES else 0
        )
        self.languageChoice.SetName(tr("Language for QuickInfo"))
        self.languageChoice.SetToolTip(tr("Choose the language used for QuickInfo announcements and labels."))
        sizer.Add(languageLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        sizer.Add(self.languageChoice, 0, wx.EXPAND | wx.ALL, 10)
        languageHelp = wx.StaticText(
            panel,
            label=tr("Choose the language used for QuickInfo announcements and labels.")
        )
        sizer.Add(languageHelp, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.computerChoice = wx.Choice(
            panel, choices=[tr("Enabled"), tr("Disabled")]
        )
        computerLabel = wx.StaticText(
            panel, label=tr("Allow the Copy Computer Name command")
        )
        self.computerChoice.SetName(tr("Allow the Copy Computer Name command"))
        self.computerChoice.SetToolTip(tr("Choose Enabled or Disabled for copying the computer name."))
        self.computerChoice.SetSelection(
            0 if opts.get("copyComputerName", True) else 1
        )
        sizer.Add(computerLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        sizer.Add(self.computerChoice, 0, wx.EXPAND | wx.ALL, 10)
        sizer.Add(wx.StaticText(
            panel,
            label=tr("Enabled lets the copy-computer-name command place the computer name on the clipboard.")
        ), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        self.networkChoice = wx.Choice(
            panel, choices=[tr("Enabled"), tr("Disabled")]
        )
        networkLabel = wx.StaticText(
            panel, label=tr("Allow the Copy Network Name command")
        )
        self.networkChoice.SetName(tr("Allow the Copy Network Name command"))
        self.networkChoice.SetToolTip(tr("Choose Enabled or Disabled for copying the network name."))
        self.networkChoice.SetSelection(
            0 if opts.get("copyNetworkName", True) else 1
        )
        sizer.Add(networkLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        sizer.Add(self.networkChoice, 0, wx.EXPAND | wx.ALL, 10)
        sizer.Add(wx.StaticText(
            panel,
            label=tr("Enabled lets the copy-network-name command place the Wi-Fi or network name on the clipboard.")
        ), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)

        # Per-command enablement and optional additional keyboard shortcut.
        commandLabel = wx.StaticText(panel, label=tr("Command to configure"))
        self.commandChoice = wx.Choice(panel, choices=[tr(label) for label, method in COMMANDS])
        self.commandChoice.SetName(tr("Command to configure"))
        self.commandChoice.SetSelection(0)
        sizer.Add(commandLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        sizer.Add(self.commandChoice, 0, wx.EXPAND | wx.ALL, 10)
        enabledLabel = wx.StaticText(panel, label=tr("Command status"))
        self.commandEnabledChoice = wx.Choice(panel, choices=[tr("Enabled"), tr("Disabled")])
        self.commandEnabledChoice.SetName(tr("Command status"))
        sizer.Add(enabledLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        sizer.Add(self.commandEnabledChoice, 0, wx.EXPAND | wx.ALL, 10)
        shortcutLabel = wx.StaticText(panel, label=tr("Additional keyboard shortcut"))
        self.shortcutInput = wx.TextCtrl(panel)
        self.shortcutInput.SetName(tr("Additional keyboard shortcut"))
        self.shortcutInput.SetHint("Example: NVDA+Ctrl+Shift+F12")
        sizer.Add(shortcutLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 10)
        sizer.Add(self.shortcutInput, 0, wx.EXPAND | wx.ALL, 10)
        sizer.Add(wx.StaticText(panel, label=tr("Leave the shortcut blank to keep the default shortcut only. Custom shortcuts are additional bindings.")), 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self._enabledCommands = _read_json_setting("enabledCommands")
        self._customGestures = _read_json_setting("customGestures")
        self.commandChoice.Bind(wx.EVT_CHOICE, self.onCommandChoice)
        self.loadCommandSettings()

        panel.SetSizer(sizer)
        panel.SetVirtualSize(sizer.GetMinSize())

        buttons = wx.BoxSizer(wx.HORIZONTAL)
        helpButton = wx.Button(self, wx.ID_ANY, tr("Keyboard shortcuts quick help"))
        helpButton.SetName(tr("Keyboard shortcuts quick help"))
        helpButton.Bind(wx.EVT_BUTTON, self.onKeyboardHelp)
        save = wx.Button(self, wx.ID_OK, tr("Save"))
        cancel = wx.Button(self, wx.ID_CANCEL, tr("Cancel"))
        self.SetAffirmativeId(wx.ID_OK)
        self.SetEscapeId(wx.ID_CANCEL)
        self.Bind(wx.EVT_BUTTON, self.onSave, id=wx.ID_OK)
        self.Bind(wx.EVT_BUTTON, self.onCancel, id=wx.ID_CANCEL)
        save.SetDefault()
        buttons.Add(helpButton, 0, wx.ALL, 6)
        buttons.Add(save, 0, wx.ALL, 6)
        buttons.Add(cancel, 0, wx.ALL, 6)

        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND | wx.ALL, 6)
        outer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 6)
        self.SetSizerAndFit(outer)
        self.SetMinSize((520, -1))
        self.CentreOnParent()
        wx.CallAfter(self.languageChoice.SetFocus)

    def onKeyboardHelp(self, event):
        owner = getattr(self, "_ownerPlugin", None)
        if owner is None:
            parent = self.GetParent()
            owner = getattr(parent, "plugin", None) if parent else None
        if owner is not None:
            owner.openQuickInfoHelp()
        else:
            announce(tr("QuickInfo keyboard help is unavailable."))

    def loadCommandSettings(self):
        index = self.commandChoice.GetSelection()
        if index == wx.NOT_FOUND:
            return
        method = COMMANDS[index][1]
        self.commandEnabledChoice.SetSelection(0 if self._enabledCommands.get(method, True) else 1)
        self.shortcutInput.SetValue(self._customGestures.get(method, ""))

    def onCommandChoice(self, event):
        self.loadCommandSettings()
        event.Skip()

    def onSave(self, event):
        try:
            language_index = self.languageChoice.GetSelection()
            computer_index = self.computerChoice.GetSelection()
            network_index = self.networkChoice.GetSelection()
            if language_index == wx.NOT_FOUND:
                raise ValueError("Choose a QuickInfo language before saving.")
            if computer_index == wx.NOT_FOUND or network_index == wx.NOT_FOUND:
                raise ValueError("Choose Enabled or Disabled for both copy commands.")

            settings = config.conf["quickInfo"]
            settings["language"] = LANGUAGES[language_index]
            settings["copyComputerName"] = (computer_index == 0)
            settings["copyNetworkName"] = (network_index == 0)
            command_index = self.commandChoice.GetSelection()
            if command_index != wx.NOT_FOUND:
                method = COMMANDS[command_index][1]
                self._enabledCommands[method] = (self.commandEnabledChoice.GetSelection() == 0)
                gesture = _normalize_gesture(self.shortcutInput.GetValue())
                if gesture:
                    self._customGestures[method] = gesture
                else:
                    self._customGestures.pop(method, None)
                settings["enabledCommands"] = json.dumps(self._enabledCommands, ensure_ascii=False)
                settings["customGestures"] = json.dumps(self._customGestures, ensure_ascii=False)
            config.conf.save()
        except Exception as error:
            announce("QuickInfo could not save the settings: " + str(error))
            return

        announce(tr("QuickInfo settings saved"))
        parent = self.GetParent()
        # Clear the owning reference before destroying this modeless dialog.
        if parent and hasattr(parent, "_settingsDialog"):
            parent._settingsDialog = None
        if parent and hasattr(parent, "refreshLabels"):
            wx.CallAfter(parent.refreshLabels)
        plugin = getattr(parent, "plugin", None) if parent else None
        if plugin is not None:
            plugin._quickInfoSettingsDialog = None
        owner = getattr(self, "_ownerPlugin", None)
        if owner is not None:
            owner._quickInfoSettingsDialog = None
        if owner is not None:
            try:
                owner.applyCustomGestures()
            except Exception:
                pass
        self.Hide()
        wx.CallAfter(self.Destroy)

    def onCancel(self, event):
        parent = self.GetParent()
        if parent and hasattr(parent, "_settingsDialog"):
            parent._settingsDialog = None
        plugin = getattr(parent, "plugin", None) if parent else None
        if plugin is not None:
            plugin._quickInfoSettingsDialog = None
        owner = getattr(self, "_ownerPlugin", None)
        if owner is not None:
            owner._quickInfoSettingsDialog = None
        self.Hide()
        wx.CallAfter(self.Destroy)


class QuickInfoHelpDialog(wx.Dialog):
    """Keyboard-first searchable list of QuickInfo shortcuts and their purpose."""
    def __init__(self, plugin, parent):
        super(QuickInfoHelpDialog, self).__init__(
            parent, title=tr("QuickInfo Keyboard Shortcuts"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )
        self.plugin = plugin
        panel = wx.Panel(self)
        layout = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(panel, label=tr(
            "Quick reference of QuickInfo keyboard commands. Search by command name or shortcut."
        ))
        layout.Add(intro, 0, wx.EXPAND | wx.ALL, 8)
        searchLabel = wx.StaticText(panel, label=tr("Search keyboard shortcuts"))
        layout.Add(searchLabel, 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.search = wx.SearchCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.search.SetName(tr("Search keyboard shortcuts"))
        self.search.ShowCancelButton(True)
        layout.Add(self.search, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.rows = self.buildRows()
        self.commandList = wx.ListBox(
            panel, choices=self.rows, style=wx.LB_SINGLE
        )
        self.commandList.SetName(tr("Keyboard shortcuts and command summaries"))
        layout.Add(self.commandList, 1, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(layout)
        close = wx.Button(self, wx.ID_CLOSE, tr("Close"))
        close.SetDefault()
        bottom = wx.BoxSizer(wx.HORIZONTAL)
        bottom.Add(close, 0, wx.ALL, 6)
        root = wx.BoxSizer(wx.VERTICAL)
        root.Add(panel, 1, wx.EXPAND | wx.ALL, 6)
        root.Add(bottom, 0, wx.ALIGN_RIGHT | wx.ALL, 4)
        self.SetSizerAndFit(root)
        self.SetMinSize((680, 500))
        self.CentreOnParent()
        self.search.Bind(wx.EVT_TEXT, self.onSearch)
        self.search.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self.onClear)
        close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
        self.Bind(wx.EVT_CLOSE, self.onClose)
        self.search.SetFocus()

    def buildRows(self):
        gesture_map = getattr(self.plugin.__class__, "_GlobalPlugin__gestures", {})
        custom = _read_json_setting("customGestures")
        rows = []
        for label, method in COMMANDS:
            gestures = [
                key.split(":", 1)[-1]
                for key, value in gesture_map.items()
                if value == method
            ]
            extra = custom.get(method)
            if extra:
                normalized = _normalize_gesture(extra)
                if normalized:
                    gestures.append(normalized)
            shortcut = ", ".join(dict.fromkeys(gestures)) if gestures else tr("No shortcut assigned")
            summary = QUICKINFO_HELP_SUMMARIES.get(method, label)
            state = tr("Enabled") if command_is_enabled(method) else tr("Disabled")
            rows.append(
                shortcut + "  |  " + tr(label) + "  |  " +
                tr(summary) + "  |  " + state
            )
        rows.append(
            "NVDA+Ctrl+Shift+Q  |  " + tr("Open QuickInfo settings") +
            "  |  " + tr("Change language, command availability and custom shortcuts.") +
            "  |  " + tr("Enabled")
        )
        rows.append(
            "NVDA+Ctrl+Shift+Alt+F4  |  " + tr("QuickInfo keyboard help") +
            "  |  " + tr("Opens this searchable shortcut reference.") +
            "  |  " + tr("Enabled")
        )
        return rows

    def onSearch(self, event):
        query = self.search.GetValue().strip().casefold()
        filtered = [row for row in self.rows if query in row.casefold()] if query else self.rows
        self.commandList.Set(filtered)
        if filtered:
            self.commandList.SetSelection(0)

    def onClear(self, event):
        self.search.SetValue("")
        self.commandList.Set(self.rows)
        if self.rows:
            self.commandList.SetSelection(0)

    def onClose(self, event):
        if getattr(self.plugin, "_quickInfoHelpDialog", None) is self:
            self.plugin._quickInfoHelpDialog = None
        event.Skip()


QUICKINFO_HELP_SUMMARIES = {'screenCurtainInfo': 'Reports whether Screen Curtain is on or off.', 'wifiInfo': 'Reports Wi-Fi network name and signal strength.', 'networkType': 'Reports the active network connection type.', 'audioOutputDevice': 'Reports the default audio output device.', 'computerName': 'Reports the Windows computer name.', 'copyComputerName': 'Copies the Windows computer name to the clipboard.', 'networkName': 'Reports the current network name.', 'copyNetworkName': 'Copies the network name to the clipboard.', 'internetStatus': 'Checks internet connectivity.', 'ipAddress': 'Reports the local IP address.', 'dnsServers': 'Reports configured DNS servers.', 'defaultGateway': 'Reports the default network gateway.', 'batteryInfo': 'Reports battery charge and charging status.', 'systemUptime': 'Reports Windows uptime.', 'windowsInfo': 'Reports Windows edition, version and build.', 'memoryInfo': 'Reports installed and available memory.', 'processorInfo': 'Reports processor model and load.', 'microphoneDevice': 'Reports the default microphone device.', 'bluetoothInfo': 'Lists detected Bluetooth devices.', 'diskInfo': 'Reports system drive capacity and free space.', 'systemSummary': 'Reads a summary of important system information.', 'clipboardType': 'Reports the clipboard content type.', 'keyboardLayout': 'Reports the active keyboard layout.', 'lockKeyStatus': 'Reports Caps Lock and Num Lock status.', 'nvdaCursor': 'Reports NVDA cursor and review position information.', 'hardwareInfo': 'Reports computer model, BIOS and hardware information.', 'batteryHealth': 'Reports battery capacity, wear and cycle count when available.', 'allDrives': 'Lists storage drives and their free space.', 'displayInfo': 'Reports display resolution, refresh rate and graphics information.', 'networkDiagnostics': 'Reports network adapter, latency and packet loss information.', 'nvdaSpeechInfo': 'Reports NVDA version, synthesizer, voice and braille information.', 'focusInspector': "Reports the focused control's name, role, value and states.", 'addonInventory': 'Lists installed NVDA add-ons and their versions.', 'runningApps': 'Lists currently open application windows.', 'audioStatus': 'Reports master volume and microphone mute status.', 'displayScaling': 'Reports display scaling information.', 'internetSpeedTest': 'Runs an on-demand internet download and upload speed test.'}


class QuickInfoDialog(wx.Dialog):
    def __init__(self, plugin, parent):
        super(QuickInfoDialog, self).__init__(parent, title=tr("QuickInfo"),
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.plugin = plugin
        panel = wx.Panel(self)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(wx.StaticText(panel, label=tr("Available commands")), 0, wx.ALL, 8)
        sizer.Add(wx.StaticText(panel, label=tr("Command category")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.categoryChoice = wx.Choice(panel, choices=[tr(x) for x in ["All", "System", "Network", "Audio", "Accessibility", "NVDA"]])
        self.categoryChoice.SetName(tr("Command category"))
        self.categoryChoice.SetSelection(0)
        sizer.Add(self.categoryChoice, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        sizer.Add(wx.StaticText(panel, label=tr("Search commands")), 0, wx.LEFT | wx.RIGHT | wx.TOP, 8)
        self.searchBox = wx.SearchCtrl(panel, style=wx.TE_PROCESS_ENTER)
        self.searchBox.SetName(tr("Search commands"))
        self.searchBox.ShowCancelButton(True)
        sizer.Add(self.searchBox, 0, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        self.filteredCommands = [(label, method) for label, method in COMMANDS if command_is_enabled(method)]
        self.commandList = wx.ListBox(panel, choices=[tr(label) for label, method in self.filteredCommands],
                                      style=wx.LB_SINGLE)
        self.commandList.SetName(tr("Available commands"))
        self.commandList.SetSelection(0)
        sizer.Add(self.commandList, 1, wx.EXPAND | wx.ALL, 8)
        sizer.Add(wx.StaticText(panel, label=tr("Use Up and Down arrows to choose a command, then press Enter.")),
                  0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 8)
        panel.SetSizer(sizer)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.settingsButton = wx.Button(self, wx.ID_ANY, tr("Open settings"))
        close = wx.Button(self, wx.ID_CANCEL, tr("Close"))
        buttons.Add(self.settingsButton, 0, wx.ALL, 5)
        buttons.Add(close, 0, wx.ALL, 5)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(panel, 1, wx.EXPAND | wx.ALL, 6)
        outer.Add(buttons, 0, wx.ALIGN_RIGHT | wx.ALL, 4)
        self.SetSizerAndFit(outer)
        self.SetMinSize((520, 520))
        self.CentreOnParent()
        self.commandList.Bind(wx.EVT_LISTBOX_DCLICK, self.onActivate)
        self.commandList.Bind(wx.EVT_KEY_DOWN, self.onListKey)
        self.searchBox.Bind(wx.EVT_TEXT, self.onSearch)
        self.searchBox.Bind(wx.EVT_TEXT_ENTER, lambda event: self.onActivate(None))
        self.categoryChoice.Bind(wx.EVT_CHOICE, self.onSearch)
        self.searchBox.Bind(wx.EVT_SEARCHCTRL_CANCEL_BTN, self.onClearSearch)
        self.settingsButton.Bind(wx.EVT_BUTTON, self.onSettings)
        close.Bind(wx.EVT_BUTTON, lambda event: self.Close())
        self.Bind(wx.EVT_CLOSE, self.onClose)
        self.searchBox.SetFocus()
        self._settingsDialog = None
    def onSearch(self, event):
        query = self.searchBox.GetValue().strip().casefold()
        category_index = self.categoryChoice.GetSelection()
        categories = ["All", "System", "Network", "Audio", "Accessibility", "NVDA"]
        selected_category = categories[category_index] if 0 <= category_index < len(categories) else "All"
        self.filteredCommands = [(label, method) for label, method in COMMANDS
                                 if command_is_enabled(method)
                                 and (selected_category == "All" or COMMAND_CATEGORIES.get(method, "System") == selected_category)
                                 and (not query or query in tr(label).casefold() or query in label.casefold())]
        self.commandList.Set([tr(label) for label, method in self.filteredCommands])
        if self.filteredCommands:
            self.commandList.SetSelection(0)
        event.Skip()

    def onClearSearch(self, event):
        self.searchBox.SetValue("")
        self.searchBox.SetFocus()

    def onListKey(self, event):
        if event.GetKeyCode() in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.onActivate(None)
        else:
            event.Skip()
    def onActivate(self, event):
        index = self.commandList.GetSelection()
        if index == wx.NOT_FOUND: return
        method = self.filteredCommands[index][1]
        self.plugin._quickInfoDialog = None
        self.Destroy()
        wx.CallAfter(getattr(self.plugin, "script_" + method), None)

    def onSettings(self, event):
        if self._settingsDialog and self._settingsDialog.IsShown():
            self._settingsDialog.Raise()
            return
        self._settingsDialog = QuickInfoSettingsDialog(self)
        self._settingsDialog.Bind(wx.EVT_CLOSE, self.onSettingsClosed)
        self._settingsDialog.Show()
        wx.CallAfter(self._settingsDialog.languageChoice.SetFocus)

    def onSettingsClosed(self, event):
        self._settingsDialog = None
        event.Skip()
        wx.CallAfter(self.commandList.SetFocus)

    def refreshLabels(self):
        self.SetTitle(tr("QuickInfo"))
        self.searchBox.SetValue("")
        self.filteredCommands = [(label, method) for label, method in COMMANDS if command_is_enabled(method)]
        self.commandList.Set([tr(label) for label, method in self.filteredCommands])
        if self.filteredCommands:
            self.commandList.SetSelection(0)
        self.searchBox.SetFocus()

    def onClose(self, event):
        self.plugin._quickInfoDialog = None
        event.Skip()



class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    scriptCategory = "Quick Info"

    def __init__(self):
        super(GlobalPlugin, self).__init__()
        self.applyCustomGestures()

    def applyCustomGestures(self):
        for old_gesture in getattr(self, "_quickInfoBoundGestures", set()):
            try:
                self.removeGesture("kb:" + old_gesture)
            except Exception:
                pass
        self._quickInfoBoundGestures = set()
        for method, gesture in _read_json_setting("customGestures").items():
            try:
                normalized = _normalize_gesture(gesture)
                if normalized and any(m == method for label, m in COMMANDS):
                    self.bindGesture("kb:" + normalized, "script_" + method)
                    self._quickInfoBoundGestures.add(normalized)
            except Exception:
                continue

    def openQuickInfoHelp(self):
        current = getattr(self, "_quickInfoHelpDialog", None)
        if current and current.IsShown():
            current.Raise()
            wx.CallAfter(current.search.SetFocus)
            return
        dlg = QuickInfoHelpDialog(self, gui.mainFrame)
        self._quickInfoHelpDialog = dlg
        dlg.Show()
        wx.CallAfter(dlg.search.SetFocus)

    def openQuickInfoDialog(self):
        """Open the command browser. Kept separate from the settings shortcut."""
        current = getattr(self, "_quickInfoDialog", None)
        if current and current.IsShown():
            current.Raise()
            wx.CallAfter(current.commandList.SetFocus)
            return
        dlg = QuickInfoDialog(self, gui.mainFrame)
        self._quickInfoDialog = dlg
        dlg.Show()
        wx.CallAfter(dlg.commandList.SetFocus)

    def openQuickInfoSettings(self):
        """Open settings directly; do not require opening the command browser first."""
        current = getattr(self, "_quickInfoSettingsDialog", None)
        if current and current.IsShown():
            current.Raise()
            wx.CallAfter(current.languageChoice.SetFocus)
            return
        dlg = QuickInfoSettingsDialog(gui.mainFrame)
        dlg._ownerPlugin = self
        self._quickInfoSettingsDialog = dlg
        dlg.Bind(wx.EVT_CLOSE, self.onQuickInfoSettingsClosed)
        dlg.Show()
        wx.CallAfter(dlg.languageChoice.SetFocus)

    def onQuickInfoSettingsClosed(self, event):
        self._quickInfoSettingsDialog = None
        event.Skip()

    def script_openQuickInfo(self, gesture):
        """Open QuickInfo Settings directly."""
        try:
            wx.CallAfter(self.openQuickInfoSettings)
        except Exception:
            announce("Unable to open QuickInfo Settings")

    def script_openQuickInfoCommands(self, gesture):
        self.openQuickInfoDialog()

    def script_openQuickInfoHelp(self, gesture):
        self.openQuickInfoHelp()

    def script_screenCurtainInfo(self, gesture):
        """Report the active Screen Curtain state using NVDA's live state API."""
        try:
            import NVDAHelper
            state_function = getattr(NVDAHelper.localLib, "isScreenFullyBlack", None)
            if state_function is not None:
                enabled = bool(state_function())
                announce(
                    "Screen Curtain is on" if enabled
                    else "Screen Curtain is off"
                )
                return
        except Exception:
            pass

        # Compatibility fallback for older NVDA versions.
        try:
            import visionEnhancementProviders.screenCurtain as legacyCurtain
            state_function = getattr(legacyCurtain, "isScreenFullyBlack", None)
            if state_function is not None:
                enabled = bool(state_function())
                announce(
                    "Screen Curtain is on" if enabled
                    else "Screen Curtain is off"
                )
                return
        except Exception:
            pass

        announce("Screen Curtain status is unavailable in this NVDA version")

    def script_lockKeyStatus(self, gesture):
        try:
            caps = bool(user32.GetKeyState(0x14) & 1)
            num = bool(user32.GetKeyState(0x90) & 1)
            announce("Caps Lock " + ("on" if caps else "off") + ", Num Lock " + ("on" if num else "off"))
        except Exception:
            announce("Unable to determine lock key status")

    def script_keyboardLayout(self, gesture):
        try:
            hkl = user32.GetKeyboardLayout(0)
            buf = ctypes.create_unicode_buffer(85)
            if kernel32.GetLocaleInfoW(hkl & 0xFFFF, 2, buf, len(buf)):
                announce("Keyboard layout " + buf.value)
            else:
                announce("Keyboard layout detected")
        except Exception:
            announce("Keyboard layout unavailable")

    def script_nvdaCursor(self, gesture):
        try:
            obj = api.getFocusObject()
            review = api.getReviewPosition()
            if obj:
                announce("Focus cursor on " + str(obj.name or obj.role.displayString) +
                           (". Review cursor available." if review else ""))
            else:
                announce("Unable to determine NVDA cursor context")
        except Exception:
            announce("NVDA cursor information unavailable")

    def script_wifiInfo(self, gesture):
        """Announce Wi-Fi SSID and signal strength without blocking NVDA."""
        announce("Checking Wi-Fi connection and signal strength")

        def worker():
            try:
                ssid, signal, quality = wifi_details()
                if not ssid:
                    message = "Wi-Fi is not connected"
                elif signal is None:
                    message = (
                        "Wi-Fi connected to " + ssid +
                        ". Signal strength is unavailable"
                    )
                else:
                    message = (
                        "Wi-Fi connected to " + ssid +
                        ". Signal strength " + str(signal) +
                        " percent, " + quality
                    )
            except subprocess.TimeoutExpired:
                message = "Wi-Fi status check timed out"
            except Exception as error:
                message = "Unable to determine Wi-Fi information: " + str(error)
            wx.CallAfter(announce, message)

        threading.Thread(
            target=worker, name="QuickInfoWiFiLookup", daemon=True
        ).start()

    def script_networkType(self, gesture):
        try:
            name, status, ssid = network_details()
            announce(status)
        except Exception:
            announce("Network information unavailable")

    def script_audioOutputDevice(self, gesture):
        """Detect the default output endpoint without blocking NVDA's main thread."""
        announce("Checking default audio output device")
        def worker():
            try:
                name = default_audio_device()
                message = (tr("Default audio output device") + " " + name if name
                           else "Windows did not return a default playback device")
            except subprocess.TimeoutExpired:
                message = "Audio device detection timed out while querying Windows Core Audio"
            except Exception as error:
                message = "Audio device detection failed: " + str(error)
            wx.CallAfter(announce, message)
        threading.Thread(target=worker, name="QuickInfoAudioLookup", daemon=True).start()

    def script_computerName(self, gesture):
        try:
            announce(tr("Computer name") + " " + computer_name())
        except Exception:
            announce("Computer name unavailable")

    def script_copyComputerName(self, gesture):
        if not config.conf.get("quickInfo", {}).get("copyComputerName", True):
            announce("Copy computer name command is disabled in QuickInfo options")
            return
        try:
            copy_text(computer_name(), "computer name")
        except Exception:
            announce("Unable to copy computer name")

    def script_networkName(self, gesture):
        try:
            name, status, ssid = network_details()
            announce(tr("Network name") + " " + name if name else "Network name unavailable. " + status)
        except Exception:
            announce("Network name unavailable")

    def script_copyNetworkName(self, gesture):
        if not config.conf.get("quickInfo", {}).get("copyNetworkName", True):
            announce("Copy network name command is disabled in QuickInfo options")
            return
        try:
            name, status, ssid = network_details()
            copy_text(name, "network name")
        except Exception:
            announce("Unable to copy network name")


    def _get_system_data(self):
        try:
            return system_data()
        except Exception:
            announce("Unable to retrieve system information")
            return None

    def script_internetStatus(self, gesture):
        data = self._get_system_data()
        if data:
            announce("Internet access available" if data.get("internet")
                       else "Network adapter may be connected, but internet access could not be confirmed")

    def script_ipAddress(self, gesture):
        data = self._get_system_data()
        if data:
            values = data.get("ipv4") or data.get("ipv6") or []
            announce("IP address " + ", ".join(values) if values else "IP address unavailable")

    def script_dnsServers(self, gesture):
        data = self._get_system_data()
        if data:
            values = data.get("dns") or []
            announce("DNS servers " + ", ".join(values) if values else "DNS server information unavailable")

    def script_defaultGateway(self, gesture):
        data = self._get_system_data()
        if data:
            values = data.get("gateway") or []
            announce("Default gateway " + ", ".join(values) if values else "Default gateway unavailable")

    def script_batteryInfo(self, gesture):
        data = self._get_system_data()
        if data:
            battery = data.get("battery")
            if not battery:
                announce("Battery not detected")
                return
            percent = battery.get("percent")
            status = battery.get("status")
            status_text = {1: "discharging", 2: "charging", 3: "fully charged",
                           4: "low", 5: "critical", 6: "charging", 7: "charging",
                           8: "charging", 9: "charging"}.get(status, "status unavailable")
            message = "Battery " + (str(percent) + " percent, " if percent is not None else "")
            message += status_text
            minutes = battery.get("minutes")
            if minutes and minutes < 71582788:
                message += ", estimated " + str(minutes // 60) + " hours " + str(minutes % 60) + " minutes remaining"
            announce(message)

    def script_systemUptime(self, gesture):
        data = self._get_system_data()
        if data:
            announce("System uptime " + str(data.get("uptimeDays", 0)) + " days, " +
                       str(data.get("uptimeHours", 0)) + " hours, " +
                       str(data.get("uptimeMinutes", 0)) + " minutes")

    def script_windowsInfo(self, gesture):
        """Announce Windows version without blocking NVDA's main event loop."""
        announce("Checking Windows version")

        def worker():
            try:
                message = windows_version_details()
            except Exception as error:
                # Keep the failure actionable instead of silently swallowing it.
                message = "Windows version lookup failed: " + str(error)
            try:
                wx.CallAfter(announce, message)
            except Exception:
                # Last-resort fallback if wx cannot schedule the UI callback.
                # Do not call NVDA speech APIs directly from this worker thread.
                pass

        try:
            thread = threading.Thread(
                target=worker,
                name="QuickInfoWindowsVersion",
                daemon=True
            )
            thread.start()
        except Exception as error:
            announce("Could not start Windows version lookup: " + str(error))

    def script_memoryInfo(self, gesture):
        data = self._get_system_data()
        if data:
            total, free = data.get("ramTotalGB"), data.get("ramFreeGB")
            if total is not None and free is not None:
                announce("Memory " + str(total) + " gigabytes total, " + str(free) + " gigabytes available")
            else:
                announce("Memory information unavailable")

    def script_processorInfo(self, gesture):
        data = self._get_system_data()
        if data:
            message = data.get("cpu") or "Processor name unavailable"
            if data.get("cpuLoad") is not None:
                message += ", current load " + str(data["cpuLoad"]) + " percent"
            announce(message)

    def script_microphoneDevice(self, gesture):
        name = microphone_device()
        announce("Microphone endpoint " + name if name else "Microphone information unavailable")

    def script_bluetoothInfo(self, gesture):
        devices = bluetooth_devices()
        if devices:
            announce("Bluetooth devices detected: " + ", ".join(str(x) for x in devices[:5]))
        else:
            announce("No active Bluetooth devices found, or Bluetooth information is unavailable")

    def script_diskInfo(self, gesture):
        data = self._get_system_data()
        if data:
            free, size = data.get("diskFreeGB"), data.get("diskSizeGB")
            if free is not None and size is not None:
                announce("System drive has " + str(free) + " gigabytes free of " + str(size) + " gigabytes")
            else:
                announce("System drive information unavailable")

    def script_systemSummary(self, gesture):
        data = self._get_system_data()
        if not data:
            return
        parts = [computer_name(), data.get("windows") or "Windows information unavailable",
                 "version " + str(data.get("version") or "unknown"),
                 "build " + str(data.get("build") or "unknown"),
                 data.get("connection", "Network status unavailable"),
                 "internet available" if data.get("internet") else "internet access not confirmed"]
        if data.get("profile"):
            parts.append("network " + str(data["profile"]))
        if data.get("ipv4"):
            parts.append("IP " + ", ".join(data["ipv4"]))
        if data.get("cpu"):
            parts.append("processor " + str(data["cpu"]))
        if data.get("ramTotalGB") is not None:
            parts.append("RAM " + str(data["ramTotalGB"]) + " gigabytes total")
        if data.get("diskFreeGB") is not None:
            parts.append("system drive " + str(data["diskFreeGB"]) + " gigabytes free")
        battery = data.get("battery")
        if battery and battery.get("percent") is not None:
            parts.append("battery " + str(battery["percent"]) + " percent")
        parts.append("uptime " + str(data.get("uptimeDays", 0)) + " days, " +
                     str(data.get("uptimeHours", 0)) + " hours")
        announce("Quick system summary. " + ". ".join(parts))

    def script_hardwareInfo(self, gesture):
        try:
            d=hardware_details(); labels=(("manufacturer","Manufacturer"),("model","Model"),("systemType","System type"),("bios","BIOS version"),("biosDate","BIOS date"),("board","Motherboard"),("ramModules","RAM modules"),("ramSlots","Memory slots"),("ramSpeed","RAM speed"),("cpuCores","CPU cores"),("cpuLogical","Logical processors"))
            parts=[label+" "+str(d[key]) for key,label in labels if d.get(key) not in (None,"")]
            announce("Hardware information. "+". ".join(parts) if parts else "Hardware information unavailable")
        except Exception: announce("Hardware information unavailable")

    def script_batteryHealth(self, gesture):
        try:
            d=battery_health_details();parts=[]
            if d.get("designCapacity"): parts.append("Design capacity "+str(d["designCapacity"])+" milliwatt hours")
            if d.get("fullCapacity"): parts.append("Full charge capacity "+str(d["fullCapacity"])+" milliwatt hours")
            if d.get("wearPercent") is not None: parts.append("Estimated battery wear "+str(d["wearPercent"])+" percent")
            if d.get("cycles") is not None: parts.append("Charge cycles "+str(d["cycles"]))
            announce("Battery health. "+". ".join(parts) if parts else "Battery health details are not exposed by this battery or firmware")
        except Exception: announce("Battery health information unavailable")

    def script_allDrives(self, gesture):
        try:
            d=all_drive_details();parts=[]
            for v in d.get("volumes",[])[:16]:
                label=str(v.get("name","Drive"))+(" "+str(v["label"]) if v.get("label") else "")
                parts.append(label+": "+str(v.get("freeGB","unknown"))+" gigabytes free of "+str(v.get("sizeGB","unknown")))
            for p in d.get("physical",[])[:8]: parts.append("Physical disk "+str(p.get("name","unknown"))+", "+str(p.get("media","media type unknown"))+", health "+str(p.get("health","unknown")))
            announce("Storage drives. "+". ".join(parts) if parts else "Storage drive information unavailable")
        except Exception: announce("Storage drive information unavailable")

    def script_displayInfo(self, gesture):
        try:
            d=display_details();parts=[]
            for g in d.get("graphics",[])[:8]:
                name=str(g.get("name","Graphics adapter"))
                if g.get("width") and g.get("height"): name+=", resolution "+str(g["width"])+" by "+str(g["height"])
                if g.get("refresh"): name+=", refresh rate "+str(g["refresh"])+" hertz"
                if g.get("driver"): name+=", driver "+str(g["driver"])
                parts.append(name)
            announce("Display and graphics. "+". ".join(parts) if parts else "Display information unavailable")
        except Exception: announce("Display and graphics information unavailable")

    def script_networkDiagnostics(self, gesture):
        announce("Checking network diagnostics")
        def worker():
            try:
                d=network_diagnostics();parts=[]
                for a in d.get("adapters",[])[:8]: parts.append(str(a.get("name","Adapter"))+", link speed "+str(a.get("linkSpeed","unknown")))
                parts.append(str(d.get("received",0))+" of "+str(d.get("sent",4))+" ping replies received")
                if d.get("averageMs") is not None: parts.append("Average latency "+str(d["averageMs"])+" milliseconds")
                parts.append("Packet loss "+str(round(d.get("lost",4)/max(d.get("sent",4),1)*100))+" percent")
                wx.CallAfter(announce,"Network diagnostics. "+". ".join(parts))
            except Exception: wx.CallAfter(announce,"Network diagnostics unavailable")
        threading.Thread(target=worker,name="QuickInfoNetworkDiagnostics",daemon=True).start()

    def script_nvdaSpeechInfo(self, gesture):
        try:
            details=nvda_speech_details()
            try:
                import braille
                handler=getattr(braille,"handler",None);display=getattr(handler,"display",None) if handler else None
                details=(details+". " if details else "")+("Braille display "+str(getattr(display,"name","connected")) if display else "No active braille display detected")
            except Exception: pass
            announce("NVDA speech information. "+details if details else "NVDA speech information unavailable")
        except Exception: announce("NVDA speech information unavailable")

    def script_focusInspector(self, gesture):
        try:
            d=focus_details();announce("Focused control details. "+d if d else "Focused control details unavailable")
        except Exception: announce("Focused control details unavailable")

    def script_addonInventory(self, gesture):
        try:
            items=addon_inventory();announce("Installed NVDA add-ons. "+". ".join(items) if items else "No add-ons found or add-on inventory unavailable")
        except Exception: announce("NVDA add-on inventory unavailable")

    def script_runningApps(self, gesture):
        try:
            items=running_applications();announce("Open applications. "+". ".join(str(x) for x in items[:20]) if items else "No titled open applications found")
        except Exception: announce("Open application list unavailable")

    def script_audioStatus(self, gesture):
        try:
            d = audio_status_details()
            parts = []
            for key, label in (("output", "Output"), ("microphone", "Microphone")):
                raw = d.get(key, "")
                if raw and "|" in raw:
                    level, muted = raw.split("|", 1)
                    parts.append(label + " volume " + level + " percent, " + ("muted" if muted.lower() == "true" else "unmuted"))
                else:
                    parts.append(label + " volume and mute state unavailable")
            announce("Volume and microphone status. " + ". ".join(parts))
        except Exception:
            announce("Volume and microphone status unavailable")

    def script_displayScaling(self, gesture):
        try:
            d = display_scaling_details()
            value = d.get("scalingPercent")
            announce("Display scaling " + str(value) + " percent" if value else "Display scaling information unavailable")
        except Exception:
            announce("Display scaling information unavailable")

    def script_internetSpeedTest(self, gesture):
        announce("Starting internet speed test. Approximately three megabytes will be downloaded and half a megabyte uploaded.")
        def worker():
            try:
                d = internet_speed_test()
                message = "Internet speed test complete. Download " + str(d["downloadMbps"]) + " megabits per second. Upload " + str(d["uploadMbps"]) + " megabits per second."
                wx.CallAfter(announce, message)
            except Exception:
                wx.CallAfter(announce, "Internet speed test failed or timed out")
        threading.Thread(target=worker, name="QuickInfoSpeedTest", daemon=True).start()

    def script_clipboardType(self, gesture):
        try:
            if not user32.OpenClipboard(None):
                announce("Clipboard unavailable")
                return
            try:
                if user32.IsClipboardFormatAvailable(15):
                    announce("Clipboard contains files")
                elif user32.IsClipboardFormatAvailable(13):
                    announce("Clipboard contains text")
                else:
                    announce("Clipboard empty or unsupported")
            finally:
                user32.CloseClipboard()
        except Exception:
            announce("Clipboard unavailable")

    __gestures = {
        "kb:NVDA+control+shift+q": "openQuickInfo",
        "kb:NVDA+control+shift+s": "openQuickInfo",
        "kb:NVDA+control+shift+alt+q": "openQuickInfoCommands",
        "kb:NVDA+control+shift+alt+f4": "openQuickInfoHelp",
        "kb:NVDA+control+shift+f9": "screenCurtainInfo",
        "kb:NVDA+control+shift+f7": "lockKeyStatus",
        "kb:NVDA+control+shift+k": "keyboardLayout",
        "kb:NVDA+control+shift+c": "nvdaCursor",
        "kb:NVDA+control+shift+f10": "wifiInfo",
        "kb:NVDA+control+shift+n": "networkType",
        "kb:NVDA+control+shift+f6": "audioOutputDevice",
        "kb:NVDA+control+shift+v": "clipboardType",
        "kb:NVDA+control+shift+o": "computerName",
        "kb:NVDA+control+shift+alt+o": "copyComputerName",
        "kb:NVDA+control+shift+alt+n": "networkName",
        "kb:NVDA+control+shift+alt+v": "copyNetworkName",
        "kb:NVDA+control+shift+alt+i": "internetStatus",
        "kb:NVDA+control+shift+alt+p": "ipAddress",
        "kb:NVDA+control+shift+alt+d": "dnsServers",
        "kb:NVDA+control+shift+alt+g": "defaultGateway",
        "kb:NVDA+control+shift+alt+b": "batteryInfo",
        "kb:NVDA+control+shift+alt+u": "systemUptime",
        "kb:NVDA+control+shift+alt+w": "windowsInfo",
        "kb:NVDA+control+shift+alt+f5": "windowsInfo",
        "kb:NVDA+control+shift+alt+m": "memoryInfo",
        "kb:NVDA+control+shift+alt+c": "processorInfo",
        "kb:NVDA+control+shift+alt+r": "microphoneDevice",
        "kb:NVDA+control+shift+alt+l": "bluetoothInfo",
        "kb:NVDA+control+shift+alt+e": "diskInfo",
        "kb:NVDA+control+shift+alt+s": "systemSummary",
        "kb:NVDA+control+shift+alt+h": "hardwareInfo",
        "kb:NVDA+control+shift+alt+y": "batteryHealth",
        "kb:NVDA+control+shift+alt+f8": "allDrives",
        "kb:NVDA+control+shift+alt+f": "displayInfo",
        "kb:NVDA+control+shift+alt+t": "networkDiagnostics",
        "kb:NVDA+control+shift+alt+f11": "nvdaSpeechInfo",
        "kb:NVDA+control+shift+alt+x": "focusInspector",
        "kb:NVDA+control+shift+alt+a": "addonInventory",
        "kb:NVDA+control+shift+alt+j": "runningApps",
        "kb:NVDA+control+shift+alt+f12": "audioStatus",
        "kb:NVDA+control+shift+alt+f2": "displayScaling",
        "kb:NVDA+control+shift+alt+f3": "internetSpeedTest",
    }


# NVDA excludes gesture-bound scripts from Input Gestures when they have no
# script description. Attach descriptions and a category to every QuickInfo command.
_QUICKINFO_DESCRIPTIONS = dict(QUICKINFO_HELP_SUMMARIES)
_QUICKINFO_DESCRIPTIONS.update({
    "openQuickInfo": "Opens QuickInfo settings.",
    "openQuickInfoCommands": "Opens the searchable QuickInfo command browser.",
    "openQuickInfoHelp": "Opens the searchable QuickInfo keyboard shortcut reference.",
})
for _gesture, _method in GlobalPlugin.__dict__.get("_GlobalPlugin__gestures", {}).items():
    _script_name = "script_" + _method
    _function = getattr(GlobalPlugin, _script_name, None)
    if _function is None:
        continue
    try:
        _decorated = _quickInfoScript(
            description=tr(_QUICKINFO_DESCRIPTIONS.get(_method, _method)),
            category="QuickInfo"
        )(_function)
        setattr(GlobalPlugin, _script_name, _decorated)
    except Exception:
        # Gesture bindings remain usable if metadata registration is unavailable.
        pass


# Gate all command scripts using the user's per-command enabled/disabled choices.
# Wrapping after class definition ensures NVDA registers the wrapped script methods.
import functools as _quickInfoFunctools
for _quickInfoLabel, _quickInfoMethod in COMMANDS:
    _quickInfoName = "script_" + _quickInfoMethod
    _quickInfoOriginal = getattr(GlobalPlugin, _quickInfoName, None)
    if _quickInfoOriginal is None or getattr(_quickInfoOriginal, "_quickInfoGated", False):
        continue
    def _quickInfoMakeGate(_original, _method):
        @_quickInfoFunctools.wraps(_original)
        def _wrapped(self, gesture):
            if not command_is_enabled(_method):
                announce("This QuickInfo command is disabled in settings")
                return
            return _original(self, gesture)
        _wrapped._quickInfoGated = True
        return _wrapped
    setattr(GlobalPlugin, _quickInfoName, _quickInfoMakeGate(_quickInfoOriginal, _quickInfoMethod))
