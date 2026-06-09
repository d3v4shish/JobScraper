#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
app_dir="${1:-$project_root/dist-linux/JobScraper}"

if [[ ! -x "$app_dir/JobScraper" ]]; then
  echo "JobScraper executable not found: $app_dir/JobScraper" >&2
  exit 1
fi

icon_src="$app_dir/JobScraper.png"
if [[ ! -f "$icon_src" ]]; then
  icon_src="$project_root/src/jobscraper/assets/app_icon.png"
fi

data_home="${XDG_DATA_HOME:-$HOME/.local/share}"
desktop_dir="$data_home/applications"
icon_dir="$data_home/icons/hicolor/512x512/apps"
desktop_dst="$desktop_dir/jobscraper.desktop"
icon_dst="$icon_dir/jobscraper.png"

install -d "$desktop_dir" "$icon_dir"
install -m 0644 "$icon_src" "$icon_dst"

cat > "$desktop_dst" <<EOF
[Desktop Entry]
Type=Application
Name=JobScraper
Comment=Desktop workbench for scraping and reviewing job sources
Exec=$app_dir/JobScraper
Icon=jobscraper
Terminal=false
Categories=Development;
StartupNotify=true
StartupWMClass=JobScraper
EOF
chmod 0755 "$desktop_dst"

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$desktop_dir" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache "$data_home/icons/hicolor" >/dev/null 2>&1 || true
fi

echo "Installed desktop launcher: $desktop_dst"
echo "Installed icon: $icon_dst"
