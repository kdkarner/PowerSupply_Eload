#!/bin/bash
set -e

APP_NAME="powersupply-ctrl"
# Versión única: se toma de SOFTWARE_VERSION en main.py
VERSION="$(sed -n 's/^SOFTWARE_VERSION = "\([^"]*\)".*/\1/p' main.py)"
[ -n "$VERSION" ] || { echo "Error: no se pudo leer SOFTWARE_VERSION de main.py"; exit 1; }
ARCH="amd64"
PKG_NAME="${APP_NAME}_${VERSION}_${ARCH}"

# Salida: build/ guarda el paquete actual; build/previous_release/ todas las versiones anteriores
OUT_DIR="build"
PREV_DIR="$OUT_DIR/previous_release"
CY_DIR="$OUT_DIR/cython"                # módulos compilados (.so)
DIST_DIR="$OUT_DIR/dist"                # salida de PyInstaller
BUILD_DIR="$OUT_DIR/$PKG_NAME"          # estructura del paquete
PKG_FILE="$OUT_DIR/$PKG_NAME.deb"
ICON_SRC="icon.png" # Nombre del archivo de icono en tu proyecto
ICON_DST="/usr/share/icons/hicolor/256x256/apps/$APP_NAME.png"

if [ ! -f "$ICON_SRC" ]; then
    echo "Error: No se encontró el archivo '$ICON_SRC' en el directorio actual."
    exit 1
fi

PYTHON="${PYTHON:-python3}"
[ -x ".venv/bin/python" ] && PYTHON=".venv/bin/python"

echo "=== 0. Archivando versiones anteriores en $PREV_DIR ==="
mkdir -p "$PREV_DIR"
# Paquetes de otras versiones (en build/ o sueltos en la raíz de versiones viejas del script)
for item in "$OUT_DIR"/${APP_NAME}_*_${ARCH} "$OUT_DIR"/${APP_NAME}_*_${ARCH}.deb \
            ./${APP_NAME}_*_${ARCH} ./${APP_NAME}_*_${ARCH}.deb; do
    [ -e "$item" ] || continue
    case "$(basename "$item")" in
        "$PKG_NAME"|"$PKG_NAME.deb") [ "$(dirname "$item")" = "$OUT_DIR" ] && continue ;;
    esac
    rm -rf "$PREV_DIR/$(basename "$item")"
    mv "$item" "$PREV_DIR/"
    echo "  archivado: $(basename "$item")"
done

echo "=== 1. Compilando módulos a código nativo con Cython ==="
rm -rf "$CY_DIR" build_cython dist   # build_cython/ y dist/ en la raíz: ubicaciones antiguas
"$PYTHON" setup_cython.py build_ext --build-lib "$CY_DIR" --build-temp "$CY_DIR/tmp"
# Los .c generados equivalen al código fuente y los .o no se usan más: no se conservan en disco
rm -rf "$CY_DIR/c" "$CY_DIR/tmp"

echo "=== 1b. Pruebas contra los módulos compilados ==="
ln -sfn ../../config "$CY_DIR/config"
PSU_MODULE_DIR="$CY_DIR" "$PYTHON" -m pytest test -q -p no:cacheprovider

echo "=== 2. Empaquetando con PyInstaller (solo .so, sin .pyc de la app) ==="
# Temporales de PyInstaller en build/pyinstaller: --clean nunca toca los paquetes de build/
"$PYTHON" -m PyInstaller --noconfirm --clean --workpath "$OUT_DIR/pyinstaller" --distpath "$DIST_DIR" powersupply-ctrl.spec

echo "=== 3. Creando estructura del paquete Debian ==="
rm -rf "$BUILD_DIR" "$PKG_FILE"
mkdir -p "$BUILD_DIR/DEBIAN"
mkdir -p "$BUILD_DIR/opt/$APP_NAME/config"
mkdir -p "$BUILD_DIR/usr/bin"
mkdir -p "$BUILD_DIR/usr/share/applications"
mkdir -p "$BUILD_DIR/usr/share/pixmaps"
mkdir -p "$BUILD_DIR$(dirname "$ICON_DST")"

echo "=== 4. Copiando binario, configuración e icono ==="
cp -r "$DIST_DIR/$APP_NAME"/* "$BUILD_DIR/opt/$APP_NAME/"
# Configuración por defecto (solo lectura). En el primer arranque se copia a ~/.config/powersupply-ctrl/
cp config/*.yaml config/*.json "$BUILD_DIR/opt/$APP_NAME/config/"
# Icono: tema hicolor (menús/escritorio), pixmaps (compatibilidad) y junto a la app (icono de ventana)
cp "$ICON_SRC" "$BUILD_DIR$ICON_DST"
cp "$ICON_SRC" "$BUILD_DIR/usr/share/pixmaps/$APP_NAME.png"
cp "$ICON_SRC" "$BUILD_DIR/opt/$APP_NAME/icon.png"
ln -s "/opt/$APP_NAME/$APP_NAME" "$BUILD_DIR/usr/bin/$APP_NAME"

echo "=== 5. Generando metadatos DEBIAN/control ==="
cat <<EOF > "$BUILD_DIR/DEBIAN/control"
Package: $APP_NAME
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: libc6 (>= 2.43), libgl1, libegl1, libxcb1, libwayland-client0, libwayland-cursor0, libwayland-egl1
Maintainer: Skytrac Lab <admin@skytrac.local>
Description: Controlador SCPI Multi-Fuente de Alimentación en PyQt6.
 Permite gestión multicanal, telemetría en tiempo real e hilos asíncronos.
EOF

echo "=== 6. Generando acceso directo (.desktop) ==="
cat <<EOF > "$BUILD_DIR/usr/share/applications/$APP_NAME.desktop"
[Desktop Entry]
Version=1.0
Type=Application
Name=Power Supply Controller
Comment=Controlador SCPI para Fuentes de Alimentación y Cargas Electrónicas
Exec=/usr/bin/$APP_NAME
Icon=$ICON_DST
Terminal=false
StartupNotify=true
StartupWMClass=$APP_NAME
Categories=Development;Electronics;
EOF
desktop-file-validate "$BUILD_DIR/usr/share/applications/$APP_NAME.desktop" 2>/dev/null || true

echo "=== 7. Generando script de posinstalación (icono en el escritorio) ==="
cat <<'EOF' > "$BUILD_DIR/DEBIAN/postinst"
#!/bin/bash
# Instala el acceso directo con icono en el escritorio de cada usuario real del equipo.
APP_NAME="powersupply-ctrl"
DESKTOP_FILE="/usr/share/applications/${APP_NAME}.desktop"

command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true

getent passwd | while IFS=: read -r user _ uid gid _ home _; do
    [ "$uid" -ge 1000 ] 2>/dev/null && [ "$uid" -lt 60000 ] || continue
    [ -d "$home" ] || continue

    # Carpeta de escritorio real del usuario (respeta el idioma: Escritorio, Desktop, Bureau...)
    desk=$(runuser -u "$user" -- env HOME="$home" xdg-user-dir DESKTOP 2>/dev/null || true)
    if [ -z "$desk" ] || [ "$desk" = "$home" ] || [ ! -d "$desk" ]; then
        desk=""
        for d in Escritorio Desktop Bureau; do
            [ -d "$home/$d" ] && desk="$home/$d" && break
        done
    fi
    [ -n "$desk" ] || continue

    target="$desk/${APP_NAME}.desktop"
    install -m 755 -o "$uid" -g "$gid" "$DESKTOP_FILE" "$target" || continue

    # Lanzador de confianza (GNOME/Nautilus y Cinnamon/Nemo): requiere la sesión D-Bus del usuario
    bus="/run/user/$uid/bus"
    if [ -S "$bus" ]; then
        runuser -u "$user" -- env HOME="$home" DBUS_SESSION_BUS_ADDRESS="unix:path=$bus" \
            gio set "$target" metadata::trusted true 2>/dev/null || true
    else
        runuser -u "$user" -- env HOME="$home" gio set "$target" metadata::trusted true 2>/dev/null || true
    fi
done
exit 0
EOF
chmod 755 "$BUILD_DIR/DEBIAN/postinst"

echo "=== 8. Generando script de posdesinstalación (limpieza) ==="
cat <<'EOF' > "$BUILD_DIR/DEBIAN/postrm"
#!/bin/bash
# Solo al desinstalar: en una actualización el acceso directo se conserva (lo reinstala el postinst nuevo).
APP_NAME="powersupply-ctrl"

case "$1" in
    remove|purge)
        getent passwd | while IFS=: read -r user _ uid gid _ home _; do
            [ "$uid" -ge 1000 ] 2>/dev/null && [ "$uid" -lt 60000 ] || continue
            for f in "$home"/*/"${APP_NAME}.desktop"; do
                [ -f "$f" ] && grep -q "Exec=/usr/bin/${APP_NAME}" "$f" && rm -f "$f"
            done
        done
        command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
        command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
        ;;
esac
exit 0
EOF
chmod 755 "$BUILD_DIR/DEBIAN/postrm"

echo "=== 9. Construyendo paquete .deb ==="
dpkg-deb --build --root-owner-group "$BUILD_DIR" "$PKG_FILE"

echo "=========================================="
echo " Paquete generado con éxito: $PKG_FILE"
echo " Versiones anteriores en:     $PREV_DIR/"
echo "=========================================="
