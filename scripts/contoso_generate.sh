#!/usr/bin/env bash
# Build and run SQLBI's official generator, then validate/normalize its CSVs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="${CONTOSO_DATA_DIR:-$ROOT/data}"
WORK="${CONTOSO_WORK_DIR:-$ROOT/runtime/contoso-generator}"
DOTNET_VERSION=8.0.425
SOURCE_COMMIT=eaeb57a9eaa6ad0cdab4fb527552102685434ee0
mkdir -p "$WORK" "$DATA_DIR/raw" "$DATA_DIR/cache" "$DATA_DIR/processed" "$DATA_DIR/provenance"
if [[ -e "$DATA_DIR/raw/sales.csv" ]]; then
  echo 'Existing data is immutable. To regenerate, choose a fresh CONTOSO_DATA_DIR.' >&2
  exit 1
fi
# A custom destination still uses the reviewed, versioned configuration and workbook.
if [[ "$DATA_DIR" != "$ROOT/data" ]]; then
  cp "$ROOT/data/provenance/generator-config.json" "$ROOT/data/provenance/input-data.xlsx" \
     "$ROOT/data/provenance/source-manifest.json" "$ROOT/data/provenance/LICENSE-"*.txt \
     "$ROOT/data/provenance/linux-build.patch" "$DATA_DIR/provenance/"
fi
export DOTNET_CLI_HOME="$WORK/dotnet-home" XDG_DATA_HOME="$WORK/xdg"
export NUGET_PACKAGES="$WORK/nuget" DOTNET_CLI_TELEMETRY_OPTOUT=1
if [[ -n "${DOTNET_BIN:-}" ]]; then
  DOTNET="$DOTNET_BIN"
elif command -v dotnet >/dev/null; then
  DOTNET="$(command -v dotnet)"
else
  # Microsoft's official installer; installs only under the project runtime folder.
  curl --fail --silent --show-error --location https://dot.net/v1/dotnet-install.sh -o "$WORK/dotnet-install.sh"
  bash "$WORK/dotnet-install.sh" --version "$DOTNET_VERSION" --install-dir "$WORK/dotnet" --no-path
  DOTNET="$WORK/dotnet/dotnet"
fi
if [[ ! -d "$WORK/source/.git" ]]; then
  git clone https://github.com/sql-bi/Contoso-Data-Generator-V2.git "$WORK/source"
fi
git -C "$WORK/source" checkout "$SOURCE_COMMIT"
python3 - "$WORK/source/DatabaseGenerator/DatabaseGenerator.csproj" <<'PY'
import sys
from pathlib import Path
path = Path(sys.argv[1])
text = path.read_text(encoding='utf-8-sig')
# Upstream's build-version stamp uses Windows cmd syntax. No engine logic is changed.
text = text.replace('<Target Name="PreBuild" BeforeTargets="PreBuildEvent">',
                    '<Target Name="PreBuild" BeforeTargets="PreBuildEvent" Condition="\'$(OS)\' == \'Windows_NT\'">')
path.write_text(text)
PY
"$DOTNET" build "$WORK/source/DatabaseGenerator/DatabaseGenerator.csproj" -c Release
"$DOTNET" --info > "$DATA_DIR/provenance/dotnet-info.txt"
"$DOTNET" "$WORK/source/DatabaseGenerator/bin/Release/net8.0/DatabaseGenerator.dll" \
  "$DATA_DIR/provenance/generator-config.json" "$DATA_DIR/provenance/input-data.xlsx" \
  "$DATA_DIR/raw" "$DATA_DIR/cache" | tee "$DATA_DIR/provenance/generation-console.log"
# The official program logs some exceptions without a nonzero exit. Normalization
# independently requires complete tables, dates, USD/rate=1, keys, and valid prices.
python3 - "$DATA_DIR" <<'PYVERIFY'
import hashlib, json, sys
from pathlib import Path
root = Path(sys.argv[1])
manifest = json.loads((root/'provenance/source-manifest.json').read_text())
for item in manifest['asset_files']:
    actual = hashlib.file_digest((root/'cache'/item['name']).open('rb'), 'sha256').hexdigest()
    if actual != item['sha256']:
        raise SystemExit(f"Official input changed: {item['name']}; review before publishing")
for name in ['generator-config.json', 'input-data.xlsx']:
    actual = hashlib.file_digest((root/'provenance'/name).open('rb'), 'sha256').hexdigest()
    if actual != manifest[name]['sha256']:
        raise SystemExit(f"Reviewed input changed: {name}; review before publishing")
PYVERIFY
python3 "$ROOT/scripts/contoso_normalize.py" --data-dir "$DATA_DIR"
