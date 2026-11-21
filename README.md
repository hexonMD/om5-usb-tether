# OM-5 USB tethering

Capture photographs from the original OM SYSTEM OM-5 over USB and process focus stacks on a laptop. On a physical OM-5 running body firmware 1.2, one shutter request from the Windows bridge downloaded eight 5184 × 3888 bracket frames and the camera's finished focus-stacked JPEG. All nine originals remained on the SD card.

**Start here if your camera offers Storage, MTP, and Webcam.** USB Remote must first be enabled by backing up your camera's current settings over Wi-Fi, changing its saved USB preference, and restoring the edited copy to the same body. The step-by-step guide includes the setup commands for Windows and macOS, verification, and how to get the USB selection menu back. [Enable USB Remote on the original OM-5](docs/enable-usb-remote.md)

## Camera and software compatibility

| Workflow | Windows | macOS |
| --- | --- | --- |
| OM-5 shutter and JPEG transfer | Verified using Windows Portable Devices (WPD) | Physical camera test pending |
| Focus stacking | Windows bridge retrieved a bracket and the camera's composite | Offline stacking of aligned JPEGs available |
| Lightroom Classic plug-in | Bridge tested with the camera. Import inside Lightroom still needs a catalog test | Plug-in uses gPhoto2 for live capture and needs a capture-capable driver. OM-5 hardware test pending |
| Lightroom Classic's Start Tethered Capture | Native camera integration unresolved | Same |
| darktable tethering | Experimental libgphoto2 patch registers the camera. App capture pending | Same |
| OM Capture | Physical application test pending | Same |
| Video | Studio processes supplied video files | Same |

The tested camera exposed USB Remote with USB ID `33a2:0138` after a stock settings restore. Ordinary MTP uses `33a2:012f`. The tested body retained its stock firmware.

Developers testing darktable can build the experimental PTP driver in a separate directory. The driver passed a Linux build and registry check for the Remote USB ID and capture capability. [Native driver experiment](native/README.md)

## Set up the laptop

Clone or download this repository and keep the plug-in beside its launchers. Use Python 3.13 in the repository folder. The graphical Studio also uses Tk.

**Windows PowerShell**

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python generate_samples.py
& ".\Launch Windows.cmd"
```

**macOS Terminal**

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python generate_samples.py
sh 'Launch Mac.command'
```

Select **Load sample** and **Run job** to process the generated focus target and clip. For scripted jobs run `python jewelry_studio.py example` with the virtual environment active. Edit the example JSON to point at your focus planes and footage, then run `python jewelry_studio.py run shot-list.json --output results`.

## Take a photo on Windows

With the virtual environment active, an SD card inserted, and the camera in USB Remote, run the following command. It takes a real photograph and saves one new file to a new directory. For a complete multi-frame JPEG bracket, use the plug-in command below.

```powershell
python om5_wpd_photo.py captures-first-shot
```

For a read-only identity check, run `python om5_wpd_probe.py --execute --usb-mode remote --body-version 1.2 --out probe-first`. Supply the version displayed in your camera menu. The raw device information saved locally can contain the body serial.

## Lightroom Classic

In **File → Plug-in Manager → Add**, choose **Jewelry Studio.lrplugin**. In **File → Plug-in Extras**, **Import sample focus stack** exercises the offline catalog workflow. **Capture over USB** releases the shutter and imports the downloaded JPEGs into the active catalog. Use JPEG or RAW+JPEG on the camera. The Windows bridge downloads the JPEGs in either mode.

Adobe's published tether list and OM Capture's compatibility list omit the original OM-5. The camera vendor, Adobe, and darktable have not endorsed this project. [Adobe tethered cameras](https://helpx.adobe.com/lightroom-classic/desktop/kb/tethered-camera-support.html) · [OM Capture](https://software.omsystem.com/omcapture/en/) · [darktable tethering diagnostics](https://docs.darktable.org/usermanual/development/en/tethering/troubleshooting/)

## Contributing test results

Include your OS, app version, original OM-5 body firmware, USB mode, and whether the app discovered the body, fired the shutter, and downloaded a full-resolution file. Report each step individually. Redact device serials and pairing details from diagnostics. Run `python -m unittest discover -v` for offline regression tests.

Original project code is available under the [MIT license](LICENSE). Files marked 0BSD retain their existing license. The libgphoto2 patch follows upstream's LGPL-2.0-or-later license.
