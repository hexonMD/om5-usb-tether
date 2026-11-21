# Native tethering driver experiment

The patch targets the PTP camera driver in libgphoto2 **2.5.34**. It registers the original OM-5's USB Remote identity and implements single-image capture using the shutter sequence observed through Windows WPD. It takes a snapshot of the card's object handles, releases the shutter once, finds a new file, and returns its path through the existing libgphoto2 capture API. darktable uses this API for tethering.

The first hardware acceptance test should use **single-shot JPEG**, an SD card, and the original OM-5 in Remote. Acceptance requires detection, a shutter release from the application, and a full-resolution file in the application's session. Multi-file brackets, live view, and video need separate native-driver work. The working Windows WPD bridge is available from the main README.

## Build in a separate development directory

Use the source release and its normal build prerequisites. On macOS these include a compiler, pkg-config, GNU libtool's libltdl, and libusb. Set `repo` to the absolute path of this repository and run the following inside a fresh, extracted libgphoto2 2.5.34 release. [libgphoto2 release and build documentation](https://github.com/gphoto/libgphoto2/releases/tag/v2.5.34)

```sh
git apply --check "$repo/native/libgphoto2-2.5.34-om5.patch"
git apply "$repo/native/libgphoto2-2.5.34-om5.patch"
prefix="$PWD/om5-install"
./configure --prefix="$prefix" --with-camlibs=ptp2 --disable-nls
make -j2
make install
cc -I"$prefix/include" "$repo/native/check_abilities.c" -L"$prefix/lib" -lgphoto2 -o check-abilities
CAMLIBS="$prefix/lib/libgphoto2/2.5.34" LD_LIBRARY_PATH="$prefix/lib" ./check-abilities
```

The registry check should print `33a2:0138  capture + configuration` without connecting a camera. For macOS, the installed library carries its build prefix in its install name. For a hardware test the USB backend must appear in the configure summary, and the host application must load this build of libgphoto2 and its PTP driver. A packaged darktable installation may bundle its own copy. Driver registration alone does not establish capture or download in darktable. [darktable tether diagnostics](https://docs.darktable.org/usermanual/development/en/tethering/troubleshooting/)

The patch to upstream library.c follows that file's LGPL-2.0-or-later license. The independent registry checker follows the project's MIT license.
