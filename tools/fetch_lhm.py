"""Gets the sensor library TENET Hub bundles for temperatures (used by the build, Windows only).

    python tools/fetch_lhm.py [LibreHardwareMonitor.zip]

Downloads LibreHardwareMonitor (pinned release, checked by SHA-256) and writes build/lhm/:
  LibreHardwareMonitorLib.dll and the DLLs it needs (not the app's own window / chart DLLs)
  PawnIO_setup.exe   the PawnIO driver installer the app carries inside itself; Windows only lets
                     signed drivers read CPU temperatures, and PawnIO is the one the library uses
  NOTICE.txt         where these come from and their licenses
"""
import hashlib, io, os, sys, tempfile, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "build", "lhm")
VERSION = "0.9.6"
URL = f"https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v{VERSION}/LibreHardwareMonitor.zip"
SHA256 = "086D9F1B5A99E643EDC2CFAAAC16051685B551E4C5AC0B32A57C58C0E529C001"
SKIP = ("Aga.Controls.dll", "OxyPlot.dll", "OxyPlot.WindowsForms.dll", "Microsoft.Win32.TaskScheduler.dll")
PAWNIO_RESOURCE = "LibreHardwareMonitor.Resources.PawnIO_setup.exe"

NOTICE = f"""TENET Hub bundles these to read temperatures:

LibreHardwareMonitorLib {VERSION} and its dependencies
  https://github.com/LibreHardwareMonitor/LibreHardwareMonitor
  Mozilla Public License 2.0 (source code at the link above)

PawnIO setup (the driver LibreHardwareMonitor uses for CPU sensors), as shipped inside
LibreHardwareMonitor {VERSION}
  https://pawnio.eu/  -  https://github.com/namazso/PawnIO
"""


def main():
    if len(sys.argv) > 1:
        data = open(sys.argv[1], "rb").read()
    else:
        with urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "tenet-hub-build"})) as r:
            data = r.read()
    got = hashlib.sha256(data).hexdigest().upper()
    if got != SHA256:
        sys.exit(f"LibreHardwareMonitor.zip checksum {got} isn't the pinned {SHA256}")
    os.makedirs(OUT, exist_ok=True)
    z = zipfile.ZipFile(io.BytesIO(data))
    dlls = [n for n in z.namelist() if "/" not in n and n.endswith(".dll") and n not in SKIP]
    for n in dlls:
        open(os.path.join(OUT, n), "wb").write(z.read(n))
    exe = os.path.join(tempfile.mkdtemp(prefix="lhm-"), "LibreHardwareMonitor.exe")   # not in OUT:
    open(exe, "wb").write(z.read("LibreHardwareMonitor.exe"))                         # never bundled
    import clr  # noqa: F401  (pythonnet)
    from System.Reflection import Assembly
    from System.IO import MemoryStream
    stream = Assembly.LoadFile(exe).GetManifestResourceStream(PAWNIO_RESOURCE)
    if stream is None:
        sys.exit(f"{PAWNIO_RESOURCE} not found in LibreHardwareMonitor.exe")
    ms = MemoryStream()
    stream.CopyTo(ms)
    open(os.path.join(OUT, "PawnIO_setup.exe"), "wb").write(bytes(ms.ToArray()))
    open(os.path.join(OUT, "NOTICE.txt"), "w", encoding="utf-8", newline="\n").write(NOTICE)
    size = os.path.getsize(os.path.join(OUT, "PawnIO_setup.exe"))
    print(f"build/lhm: {len(dlls)} DLLs, PawnIO_setup.exe {size} bytes")


if __name__ == "__main__":
    main()
