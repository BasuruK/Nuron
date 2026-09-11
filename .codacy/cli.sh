cli.sh 158L cognitive
// /Users/basuruk/Dev/Nuron/Nuron/.codacy/cli.sh
§ function function (L39-L48)
get_version_from_yaml() {
    if [ -f "$version_file" ]; then
        local version=$(grep -o 'version: *"[^"]*"' "$version_file" | cut -d'"' -f2)
        if [ -n "$version" ]; then
            echo "$version"
            return 0
        fi
    fi
    return 1
}
// ... 1 lines omitted
§ function function (L50-L61)
get_latest_version() {
    local response
    if [ -n "$GH_TOKEN" ]; then
        response=$(curl -f -Lq --header "Authorization: Bearer [REDACTED:Authorization header] "https://api.github.com/repos/codacy/codacy-cli-v2/releases/latest")
    else
        response=$(curl -f -Lq "https://api.github.com/repos/codacy/codacy-cli-v2/releases/latest")
    fi

    handle_rate_limit "$response"
    local version=$(echo "$response" | grep -m 1 tag_name | cut -d'"' -f4)
    echo "$version"
}
// ... 1 lines omitted
§ function function (L63-L68)
handle_rate_limit() {
    local response="$1"
    if echo "$response" | grep -q "API rate limit exceeded"; then
          fatal "Error: GitHub API rate limit exceeded. Please try again later"
    fi
}
// ... 1 lines omitted
§ function function (L70-L84)
download_file() {
    local url="$1"

    echo "Downloading from URL: ${url}"
    if command -v curl > /dev/null 2>&1; then
        if ! curl -# -LSf "$url" -O; then
            rm -f "$(basename "$url")"
            return 1
        fi
    elif command -v wget > /dev/null 2>&1; then
        wget "$url"
    else
        fatal "Error: Could not find curl or wget, please install one."
    fi
}
// ... 1 lines omitted
§ function function (L86-L91)
download() {
    local url="$1"
    local output_folder="$2"

    ( cd "$output_folder" && download_file "$url" )
}
// ... 1 lines omitted
§ function function (L93-L110)
download_cli() {
    # OS name lower case
    suffix=$(echo "$os_name" | tr '[:upper:]' '[:lower:]')

    local bin_folder="$1"
    local bin_path="$2"
    local version="$3"

    if [ ! -f "$bin_path" ]; then
        echo "📥 Downloading CLI version $version..."

        remote_file="codacy-cli-v2_${version}_${suffix}_${arch}.tar.gz"
        url="https://github.com/codacy/codacy-cli-v2/releases/download/${version}/${remote_file}"

        download "$url" "$bin_folder"
        tar xzfv "${bin_folder}/${remote_file}" -C "${bin_folder}"
    fi
}
§ block block (L111-L158)

# Warn if CODACY_CLI_V2_VERSION is set and update is requested
if [ -n "$CODACY_CLI_V2_VERSION" ] && [ "$1" = "update" ]; then
    echo "⚠️  Warning: Performing update with forced version $CODACY_CLI_V2_VERSION"
    echo "    Unset CODACY_CLI_V2_VERSION to use the latest version"
fi

# Ensure version.yaml exists and is up to date
if [ ! -f "$version_file" ] || [ "$1" = "update" ]; then
    echo "ℹ️  Fetching latest version..."
    version=$(get_latest_version)
    if [ -z "$version" ]; then
        echo "Error: Failed to retrieve a valid release version" >&2
        exit 1
    fi
    mkdir -p "$CODACY_CLI_V2_TMP_FOLDER"
    echo "version: \"$version\"" > "${version_file}.tmp"
    mv "${version_file}.tmp" "$version_file"
fi

# Set the version to use
if [ -n "$CODACY_CLI_V2_VERSION" ]; then
    version="$CODACY_CLI_V2_VERSION"
else
    version=$(get_version_from_yaml)
fi


# Set up version-specific paths
bin_folder="${CODACY_CLI_V2_TMP_FOLDER}/${version}"

mkdir -p "$bin_folder"
bin_path="$bin_folder"/"$bin_name"

# Download the tool if not already installed
download_cli "$bin_folder" "$bin_path" "$version"
chmod +x "$bin_path"

run_command="$bin_path"
if [ -z "$run_command" ]; then
    fatal "Codacy cli v2 binary could not be found."
fi

if [ "$#" -eq 1 ] && [ "$1" = "download" ]; then
    echo "Codacy cli v2 download succeeded"
else
    exec "$run_command" "$@"
fi
7/8 chunks shown (969 tokens)
[lean-ctx] full source: read "/Users/basuruk/Dev/Nuron/Nuron/.codacy/cli.sh" directly (no MCP)  ·  or ctx_read("/Users/basuruk/Dev/Nuron/Nuron/.codacy/cli.sh", mode="full")
