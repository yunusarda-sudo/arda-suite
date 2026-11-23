#!/usr/bin/env bash
# Build script for SharedBrain.app (Native macOS desktop application bundle)
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Shared Brain"
BUNDLE_NAME="SharedBrain.app"
DIST_DIR="$REPO_DIR/dist"
APP_DIR="$DIST_DIR/$BUNDLE_NAME"
CONTENTS="$APP_DIR/Contents"
MACOS_DIR="$CONTENTS/MacOS"
RESOURCES_DIR="$CONTENTS/Resources"

echo "==> Building $BUNDLE_NAME for macOS..."

mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

# 1. Generate Info.plist
cat <<EOF > "$CONTENTS/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleDevelopmentRegion</key>
    <string>en</string>
    <key>CFBundleDisplayName</key>
    <string>Shared Brain</string>
    <key>CFBundleExecutable</key>
    <string>SharedBrain</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIdentifier</key>
    <string>com.mem0.sharedbrain</string>
    <key>CFBundleInfoDictionaryVersion</key>
    <string>6.0</string>
    <key>CFBundleName</key>
    <string>Shared Brain</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1.0.0</string>
    <key>LSMinimumSystemVersion</key>
    <string>11.0</string>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

echo "APPL????" > "$CONTENTS/PkgInfo"

# 2. Generate Launcher Script
cat <<'EOF' > "$MACOS_DIR/SharedBrain"
#!/bin/bash
set -e

# Resolve project repository directory
BUNDLE_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DIR="$(cd "$BUNDLE_ROOT/.." && pwd)"
if [ ! -f "$DIR/dashboard/server.py" ]; then
    DIR="$(cd "$BUNDLE_ROOT/../.." && pwd)"
fi
if [ ! -f "$DIR/dashboard/server.py" ] && [ -n "${MEM0_ROOT:-}" ]; then
    DIR="$MEM0_ROOT"
fi
if [ ! -f "$DIR/dashboard/server.py" ]; then
    DIR="$HOME/mem0-local"
fi

cd "$DIR"

# 1. Ensure Qdrant on port 6333
if ! curl -s -f "http://127.0.0.1:6333/readyz" >/dev/null 2>&1 && ! nc -z 127.0.0.1 6333 >/dev/null 2>&1; then
    launchctl kickstart -k gui/$(id -u)/com.mem0.qdrant >/dev/null 2>&1 || true
    sleep 0.5
    if ! nc -z 127.0.0.1 6333 >/dev/null 2>&1 && [ -f "$DIR/bin/qdrant" ]; then
        "$DIR/bin/qdrant" >/tmp/qdrant-app.log 2>&1 &
    fi
fi

# 2. Ensure Ollama on port 11434
if ! curl -s "http://127.0.0.1:11434/api/tags" >/dev/null 2>&1; then
    if [ -d "/Applications/Ollama.app" ]; then
        open -a "/Applications/Ollama.app"
    elif which ollama >/dev/null 2>&1; then
        ollama serve >/tmp/ollama-app.log 2>&1 &
    fi
fi

# 3. Ensure Dashboard on port 8899
if ! curl -s "http://localhost:8899/api/health" >/dev/null 2>&1; then
    "$DIR/bin/mem0-python" "$DIR/dashboard/server.py" >/tmp/shared-brain.log 2>&1 &
    for i in {1..20}; do
        if curl -s "http://localhost:8899/api/health" >/dev/null 2>&1; then
            break
        fi
        sleep 0.25
    done
fi

# 4. Launch window in Desktop App Mode (standalone frame without address bar)
TARGET_URL="http://localhost:8899/#harness"

if [ -d "/Applications/Google Chrome.app" ]; then
    open -na "/Applications/Google Chrome.app" --args --app="$TARGET_URL" --user-data-dir="$HOME/Library/Application Support/SharedBrain/ChromeProfile"
elif [ -d "/Applications/Brave Browser.app" ]; then
    open -na "/Applications/Brave Browser.app" --args --app="$TARGET_URL" --user-data-dir="$HOME/Library/Application Support/SharedBrain/BraveProfile"
elif [ -d "/Applications/Microsoft Edge.app" ]; then
    open -na "/Applications/Microsoft Edge.app" --args --app="$TARGET_URL" --user-data-dir="$HOME/Library/Application Support/SharedBrain/EdgeProfile"
else
    open "$TARGET_URL"
fi
EOF

chmod +x "$MACOS_DIR/SharedBrain"

# 3. Generate High-Res Icon (.icns) using python stdlib + sips + iconutil
ICONSET_DIR="/tmp/AppIcon.iconset"
rm -rf "$ICONSET_DIR"
mkdir -p "$ICONSET_DIR"

"$REPO_DIR/bin/mem0-python" - <<'PYEOF'
import math, struct, zlib

def generate_brain_icon(size=256):
    # Generates a modern gradient rounded squircle with glowing brain glyph
    cx, cy = size / 2.0, size / 2.0
    radius = size * 0.44
    pixels = bytearray()
    
    for y in range(size):
        row = bytearray(b'\x00') # PNG filter byte
        for x in range(size):
            dx = (x - cx) / radius
            dy = (y - cy) / radius
            dist = math.sqrt(dx * dx + dy * dy)
            
            # Rounded squircle boundary
            squircle = (abs(dx)**3.2 + abs(dy)**3.2)**(1.0 / 3.2)
            if squircle <= 1.0:
                # Gradient background: deep indigo #4f46e5 to purple #9333ea
                t = (x + y) / (2.0 * size)
                r = int(79 * (1 - t) + 147 * t)
                g = int(70 * (1 - t) + 51 * t)
                b = int(229 * (1 - t) + 234 * t)
                
                # Antialiased border
                edge = 1.0 - squircle
                alpha = int(min(1.0, max(0.0, edge * 35.0)) * 255)
                
                # Inner brain hemisphere highlight
                if 0.25 < dist < 0.65 and (abs(dx) > 0.04 or abs(dy) < 0.35):
                    r = min(255, r + 45)
                    g = min(255, g + 55)
                    b = min(255, b + 40)
            else:
                r, g, b, alpha = 0, 0, 0, 0
                
            row.extend([r, g, b, alpha])
        pixels.extend(row)
        
    compressed = zlib.compress(bytes(pixels))
    def chunk(tag, data):
        crc = zlib.crc32(tag + data) & 0xffffffff
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', crc)
        
    png = (b'\x89PNG\r\n\x1a\n'
           + chunk(b'IHDR', struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0))
           + chunk(b'IDAT', compressed)
           + chunk(b'IEND', b''))
    with open('/tmp/SharedBrain_base.png', 'wb') as f:
        f.write(png)

generate_brain_icon(256)
PYEOF

if [ -f "/tmp/SharedBrain_base.png" ]; then
    sips -z 16 16     /tmp/SharedBrain_base.png --out "$ICONSET_DIR/icon_16x16.png" >/dev/null 2>&1 || true
    sips -z 32 32     /tmp/SharedBrain_base.png --out "$ICONSET_DIR/icon_16x16@2x.png" >/dev/null 2>&1 || true
    sips -z 32 32     /tmp/SharedBrain_base.png --out "$ICONSET_DIR/icon_32x32.png" >/dev/null 2>&1 || true
    sips -z 64 64     /tmp/SharedBrain_base.png --out "$ICONSET_DIR/icon_32x32@2x.png" >/dev/null 2>&1 || true
    sips -z 128 128   /tmp/SharedBrain_base.png --out "$ICONSET_DIR/icon_128x128.png" >/dev/null 2>&1 || true
    sips -z 256 256   /tmp/SharedBrain_base.png --out "$ICONSET_DIR/icon_128x128@2x.png" >/dev/null 2>&1 || true
    sips -z 256 256   /tmp/SharedBrain_base.png --out "$ICONSET_DIR/icon_256x256.png" >/dev/null 2>&1 || true
    sips -z 512 512   /tmp/SharedBrain_base.png --out "$ICONSET_DIR/icon_256x256@2x.png" >/dev/null 2>&1 || true
    sips -z 512 512   /tmp/SharedBrain_base.png --out "$ICONSET_DIR/icon_512x512.png" >/dev/null 2>&1 || true
    
    iconutil -c icns "$ICONSET_DIR" -o "$RESOURCES_DIR/AppIcon.icns" >/dev/null 2>&1 || true
    rm -rf "$ICONSET_DIR" /tmp/SharedBrain_base.png
fi

# 4. Install into ~/Applications for Spotlight and Launchpad
USER_APPS="$HOME/Applications"
mkdir -p "$USER_APPS"
rm -rf "$USER_APPS/$BUNDLE_NAME"
cp -R "$APP_DIR" "$USER_APPS/$BUNDLE_NAME"

echo "==> Successfully built and installed $BUNDLE_NAME"
echo "    Location: $USER_APPS/$BUNDLE_NAME"
echo "    You can launch it via: open '$USER_APPS/$BUNDLE_NAME'"
echo "    Or find it in Spotlight (Cmd+Space -> 'Shared Brain') and Launchpad!"
