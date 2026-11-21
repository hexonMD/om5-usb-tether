# Enable USB Remote on the original OM-5

The original OM-5 offers Select, Storage, MTP, and Webcam in its USB settings. On a body running **firmware 1.2**, restoring a copy of its own Current settings with the USB preference set to Remote enabled tethering on stock firmware. The camera entered Remote automatically when the USB cable was connected. The stock menu continued to show its usual choices.

This procedure writes the full settings backup. Keep the original file private and use the same camera throughout. The public helper implements the sequence tested on Windows. Its offline tests also run on macOS. Live setup on a Mac and independent confirmation on a second OM-5 are pending.

## 1. Save MTP and connect over Wi-Fi

1. Charge the battery, unplug USB, and check **Setup Menu → Firmware**. The tested model is the **original OM-5** with **body firmware 1.2**.
2. Open **Menu → Custom Menu (gear) → D4 → USB Mode**, choose **MTP**, press **OK**, and exit the menu. This saves the starting preference. Choosing MTP at the cable-connection prompt applies to that connection. [OM-5 manual, USB Mode, p. 339](https://download.omsystem.com/pages/inst/om5/manual_om5_ENU.pdf)
3. Open **Playback Menu → Device Connection**. Join the camera's Wi-Fi network from the laptop using the credentials displayed by the camera. Close other apps communicating with the camera during the restore. [OM-5 manual, Device Connection, p. 359](https://download.omsystem.com/pages/inst/om5/manual_om5_ENU.pdf)
4. Open a terminal in the downloaded repository. This helper uses only the Python standard library. In the commands below, substitute `py -3.13` on Windows or `python3.13` on macOS for `python` as needed.

## 2. Back up your own camera

```sh
python om5_usb_mode.py prepare om5-private --body-version 1.2
```

The helper opens a temporary maintenance session and exports the **whole Current settings backup** to a new folder. Keep **original.csv** as your backup. **remote.csv** is the proposed restore. **body.json** ties the backup to the body that supplied it. The helper preserves all other bytes, including line endings. A text diff should show exactly this change.

```diff
-2,MODE_SELECT_PC,MODE_PC_MTP
+2,MODE_SELECT_PC,MODE_PC_REMOTE
```

Keep the files as generated. A spreadsheet editor can change the CSV encoding or other values when saving.

## 3. Validate and restore

```sh
python om5_usb_mode.py apply om5-private
```

The helper checks camera identity and verifies that current settings still match **original.csv**. It uploads **remote.csv** for validation. Once the camera reports success and zero error bytes, it uploads the identical file **a second time** and submits one restore. It waits for completion and independently re-exports the Current settings. Look for **Full camera readback matches remote.csv**.

If settings changed after preparation, use a fresh backup folder. If the connection drops after submission, reconnect and run `python om5_usb_mode.py verify om5-private`. It reports whether the full original or Remote copy is active. Inspect a different readback before attempting another restore. Keep the camera powered throughout the write.

## 4. Restart and verify over USB

```sh
python om5_usb_mode.py restart om5-private
```

This checks the Remote readback before requesting the camera's stock restart. Wait for startup, then connect a USB **data** cable. A blank rear display and no connection menu can be normal in Remote. Check the USB identity on the laptop.

- **Windows PowerShell** — `Get-PnpDevice -PresentOnly | Where-Object InstanceId -like 'USB\VID_33A2&PID_0138\*' | Select-Object Status,FriendlyName`. The tested body appeared as **OM-5**.
- **macOS** — Open **System Information → Hardware → USB**, select the camera, and check for vendor ID **0x33a2** and product ID **0x0138**.

Product ID **0x012f** means MTP. **0x0138** identifies USB Remote. A separate Windows test released the shutter and downloaded a 5184 × 3888 JPEG. [Take a photo on Windows](../README.md#take-a-photo-on-windows)

## Return to the ordinary USB prompt

Disconnect USB. Open **Menu → Custom Menu → D4 → USB Mode**, choose **Select**, press **OK**, and exit. Reconnect the cable to check for the **Storage / MTP / Webcam** prompt. Saving **MTP** in D4 instead returns the body to the USB preference used for the backup. To enable Remote again, repeat the setup with a fresh export. OM SYSTEM documents the Select preference. The project's restore rollback was tested in the emulator. A physical rollback and second enable are still unverified. [OM SYSTEM USB mode guidance](https://support.jp.omsystem.com/en/support/imsg/digicamera/qa/products/om5/)
