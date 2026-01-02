# quickInfo.py
# NVDA Global Plugin
# QuickInfo – quick on-demand information for NVDA users

import globalPluginHandler
import ui
import api
import ctypes
import subprocess
import nvwave
import config


user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


class GlobalPlugin(globalPluginHandler.GlobalPlugin):
    """
    QuickInfo global plugin.
    Provides reliable, read-only announcements of useful system
    and NVDA-related information not exposed elsewhere.
    """

    scriptCategory = "Quick Info"

    # ------------------------------------------------------------
    # 1. Screen Curtain – honest informational message
    # ------------------------------------------------------------
    def script_screenCurtainInfo(self, gesture):
        ui.message(
            "Screen curtain is available. "
            "NVDA does not expose its current on or off state."
        )

    # ------------------------------------------------------------
    # 2. Caps Lock and Num Lock status
    # ------------------------------------------------------------
    def script_lockKeyStatus(self, gesture):
        try:
            caps = bool(user32.GetKeyState(0x14) & 1)
            num = bool(user32.GetKeyState(0x90) & 1)
            ui.message(
                f"Caps Lock {'on' if caps else 'off'}, "
                f"Num Lock {'on' if num else 'off'}"
            )
        except Exception:
            ui.message("Unable to determine lock key status")

    # ------------------------------------------------------------
    # 3. Keyboard layout (localized display name)
    # ------------------------------------------------------------
    def script_keyboardLayout(self, gesture):
        try:
            hkl = user32.GetKeyboardLayout(0)
            langID = hkl & 0xFFFF
            buffer = ctypes.create_unicode_buffer(85)
            LOCALE_SENGLISHDISPLAYNAME = 0x00000002

            if kernel32.GetLocaleInfoW(
                langID,
                LOCALE_SENGLISHDISPLAYNAME,
                buffer,
                len(buffer)
            ):
                ui.message(f"Keyboard layout {buffer.value}")
            else:
                ui.message("Keyboard layout detected")
        except Exception:
            ui.message("Keyboard layout unavailable")

    # ------------------------------------------------------------
    # 4. NVDA cursor context (focus + review)
    # ------------------------------------------------------------
    def script_nvdaCursor(self, gesture):
        try:
            focusObj = api.getFocusObject()
            reviewPos = api.getReviewPosition()

            if focusObj:
                name = focusObj.name or focusObj.role.displayString
                if reviewPos:
                    ui.message(
                        f"Focus cursor on {name}. Review cursor available."
                    )
                else:
                    ui.message(f"Focus cursor on {name}")
            else:
                ui.message("Unable to determine NVDA cursor context")
        except Exception:
            ui.message("NVDA cursor information unavailable")

    # ------------------------------------------------------------
    # 5. Wi-Fi SSID (connected network)
    # ------------------------------------------------------------
    def script_wifiInfo(self, gesture):
        try:
            output = subprocess.check_output(
                ["netsh", "wlan", "show", "interfaces"],
                stderr=subprocess.DEVNULL,
                universal_newlines=True
            )
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("SSID") and "BSSID" not in line:
                    ssid = line.split(":", 1)[1].strip()
                    ui.message(f"Wi-Fi connected to {ssid}")
                    return
            ui.message("Wi-Fi not connected")
        except Exception:
            ui.message("Wi-Fi information unavailable")

    # ------------------------------------------------------------
    # 6. Network connection type (Wi-Fi / Ethernet / none)
    # ------------------------------------------------------------
    def script_networkType(self, gesture):
        try:
            output = subprocess.check_output(
                ["netsh", "interface", "show", "interface"],
                stderr=subprocess.DEVNULL,
                universal_newlines=True
            )
            for line in output.splitlines():
                if "Connected" in line:
                    if "Wi-Fi" in line or "Wireless" in line:
                        ui.message("Connected via Wi-Fi")
                    else:
                        ui.message("Connected via Ethernet")
                    return
            ui.message("No active network connection")
        except Exception:
            ui.message("Network information unavailable")

    # ------------------------------------------------------------
    # 7. Audio output device (generic, honest)
    # ------------------------------------------------------------
    def script_audioOutputDevice(self, gesture):
        try:
            try:
                device = nvwave.getOutputDeviceName()
            except Exception:
                device = None

            if device:
                ui.message(f"Audio output device {device}")
                return

            speech = config.conf.get("speech", {})
            outputDevice = speech.get("outputDevice")

            if not outputDevice or outputDevice == "default":
                ui.message("Audio output follows Windows default device")
            else:
                ui.message(f"Audio output device {outputDevice}")
        except Exception:
            ui.message("Unable to determine audio output device")

    # ------------------------------------------------------------
    # 8. Clipboard content type
    # ------------------------------------------------------------
    def script_clipboardType(self, gesture):
        try:
            if not user32.OpenClipboard(None):
                ui.message("Clipboard unavailable")
                return
            try:
                CF_UNICODETEXT = 13
                CF_HDROP = 15

                if user32.IsClipboardFormatAvailable(CF_HDROP):
                    ui.message("Clipboard contains files")
                elif user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    ui.message("Clipboard contains text")
                else:
                    ui.message("Clipboard empty or unsupported")
            finally:
                user32.CloseClipboard()
        except Exception:
            ui.message("Clipboard unavailable")

    # ------------------------------------------------------------
    # Default gestures
    # ------------------------------------------------------------
    __gestures = {
        "kb:NVDA+control+shift+f9":  "screenCurtainInfo",
        "kb:NVDA+control+shift+f7":  "lockKeyStatus",
        "kb:NVDA+control+shift+k":   "keyboardLayout",
        "kb:NVDA+control+shift+c":   "nvdaCursor",
        "kb:NVDA+control+shift+f10": "wifiInfo",
        "kb:NVDA+control+shift+n":   "networkType",
        "kb:NVDA+control+shift+f6":  "audioOutputDevice",
        "kb:NVDA+control+shift+v":   "clipboardType",
    }
