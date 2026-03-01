#!/usr/bin/env bash
set -euo pipefail

echo ""
echo " Garmin Health Coach — macOS Build"
echo " ===================================="
echo ""

# Install build dependencies
pip install pyinstaller pystray pillow --quiet

# Clean and build
pyinstaller garmin_coach.spec --clean --noconfirm

# Package the .app bundle into a .dmg for distribution
echo ""
echo " Packaging dist/GarminHealthCoach.app into GarminHealthCoach.dmg ..."

# Remove any existing dmg
rm -f GarminHealthCoach.dmg

# Create a temporary staging folder with the app + Applications symlink
STAGING=$(mktemp -d)
cp -R "dist/GarminHealthCoach.app" "$STAGING/"
ln -s /Applications "$STAGING/Applications"

# Build the dmg
hdiutil create \
    -volname "Garmin Health Coach" \
    -srcfolder "$STAGING" \
    -ov \
    -format UDZO \
    "GarminHealthCoach.dmg"

rm -rf "$STAGING"

echo ""
echo " Build complete."
echo " App bundle : dist/GarminHealthCoach.app"
echo " Installer  : GarminHealthCoach.dmg"
echo ""
