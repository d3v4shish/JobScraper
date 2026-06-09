#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_root="$(cd "$script_dir/.." && pwd)"
python_bin="${PYTHON:-$project_root/.venv/bin/python}"

if [[ ! -x "$python_bin" ]]; then
  python_bin="${PYTHON:-python3}"
fi

"$python_bin" -m pip install -e "$project_root[build]"

"$python_bin" -m PyInstaller \
  "$script_dir/JobScraperLinux.spec" \
  --noconfirm \
  --clean \
  --distpath "$project_root/dist-linux" \
  --workpath "$project_root/build-linux/work"

app_dir="$project_root/dist-linux/JobScraper"
icon_src="$project_root/src/jobscraper/assets/app_icon.png"
icon_dst="$app_dir/JobScraper.png"
desktop_dst="$app_dir/JobScraper.desktop"

install -m 0644 "$icon_src" "$icon_dst"

cat > "$desktop_dst" <<EOF
[Desktop Entry]
Type=Application
Name=JobScraper
Comment=Desktop workbench for scraping and reviewing job sources
Exec=$app_dir/JobScraper
Icon=$icon_dst
Terminal=false
Categories=Development;
StartupNotify=true
StartupWMClass=JobScraper
EOF
chmod 0755 "$desktop_dst"

echo "Linux package built: $app_dir"
echo "Launcher: $desktop_dst"
