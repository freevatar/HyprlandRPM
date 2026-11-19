%global zig_version 0.17.1
%global zig_toolchain zig-x86_64-linux-%{zig_version}
%global app_id com.mitchellh.ghostty

Name:           ghostty
Version:        1.4.0
Release:        %autorelease -b3
Summary:        GPU-accelerated terminal emulator for Wayland and X11

License:        MIT AND OFL-1.1 AND Apache-2.0 AND BSD-2-Clause AND BSD-3-Clause AND CC0-1.0 AND MPL-2.0 AND Unicode-3.0
URL:            https://github.com/ghostty-org/ghostty
Source0:        https://release.files.ghostty.org/%{version}/%{name}-%{version}.tar.gz
# Fedora 44 and 45 ship Zig 0.16. Ghostty 1.4 requires 0.17 to compile.
Source1:        https://ziglang.org/download/%{zig_version}/%{zig_toolchain}.tar.xz
Source2:        sources.sha256
Source3:        prepare-zig-sources.py
ExclusiveArch:  x86_64

BuildRequires:  desktop-file-utils
BuildRequires:  gettext
BuildRequires:  ncurses
BuildRequires:  pandoc-cli
BuildRequires:  pkgconf-pkg-config
BuildRequires:  systemd-rpm-macros
BuildRequires:  pkgconfig(bzip2)
BuildRequires:  pkgconfig(egl)
BuildRequires:  pkgconfig(fontconfig)
BuildRequires:  pkgconfig(freetype2)
BuildRequires:  pkgconfig(gtk4) >= 4.18
BuildRequires:  pkgconfig(gtk4-layer-shell-0) >= 1.0.4
BuildRequires:  pkgconfig(harfbuzz)
BuildRequires:  pkgconfig(libadwaita-1) >= 1.7
BuildRequires:  pkgconfig(libpng)
BuildRequires:  pkgconfig(oniguruma)
BuildRequires:  pkgconfig(simdutf)
BuildRequires:  pkgconfig(wayland-client)
BuildRequires:  pkgconfig(wayland-scanner)
BuildRequires:  pkgconfig(x11)
BuildRequires:  pkgconfig(zlib)

%description
Ghostty is a GPU-accelerated terminal emulator with a native GTK interface,
tabs, split panes, and shell integration.

# COPR can prepare the source RPM on a different architecture.
# {{{ if [ -n "${OUTDIR:-}" ]; then python3 "$(dirname "$INPUT_PATH")/prepare-zig-sources.py" "$INPUT_PATH" "$OUTDIR" >&2; fi }}}
%sourcelist
%{name}-%{version}-deps.tar.zst

%prep
(cd "%{_sourcedir}" && sha256sum --check --strict "%{SOURCE2}")
%setup -q -a 1 -a 4

# Import each local archive with the target compiler. Zig writes its computed
# package ID under zig-pkg. The release manifest supplies the expected ID.
export ZIG_GLOBAL_CACHE_DIR="$PWD/.zig-global-cache"
mkdir zig-import
(
    cd zig-import
    touch build.zig
    for archive in ../zig-sources/*/*; do
        directory=${archive%/*}
        expected=${directory##*/}
        "../%{zig_toolchain}/zig" fetch --save="$expected" --pkg-dir ../zig-pkg "$archive"
        test -d "../zig-pkg/$expected" || { echo "Unexpected Zig package ID for $archive" >&2; exit 1; }
    done
)

%build
export PATH="$PWD/%{zig_toolchain}:$PATH"
export ZIG_GLOBAL_CACHE_DIR="$PWD/.zig-global-cache"
# Fedora ships static glslang and has no SPIRV-Cross development package.
DESTDIR="$PWD/stage" zig build %{?_smp_mflags} \
    --system "$PWD/zig-pkg" --prefix %{_prefix} --build-id=sha1 \
    -Dversion-string=%{version} -Doptimize=ReleaseFast -Dcpu=baseline -Dstrip=false \
    -Dgtk-wayland=true -Dinstalled-exe-path=%{_bindir}/ghostty \
    -Demit-docs=true -Demit-themes=true -fno-sys=glslang -fno-sys=spirv-cross

%install
install -Dpm0755 stage%{_bindir}/ghostty %{buildroot}%{_bindir}/ghostty
install -Dpm0644 stage%{_userunitdir}/app-%{app_id}.service \
    %{buildroot}%{_userunitdir}/app-%{app_id}.service
cp -a stage%{_datadir} %{buildroot}%{_prefix}/
# The terminal only needs the application resources. Upstream also builds a VT SDK.
rm %{buildroot}%{_datadir}/pkgconfig/libghostty-vt*.pc
# Fedora's ncurses-term owns the short name. Ship Ghostty's native TERM entry.
rm -f %{buildroot}%{_datadir}/terminfo/g/ghostty
%find_lang %{app_id}

# Retain the notices accompanying the bundled sources and fonts.
mkdir -p bundled-licenses
(
    cd zig-pkg
    find . -maxdepth 2 -type f \( -iname 'license*' -o -iname 'copying*' -o -iname 'ofl.txt' \) \
        -exec cp --parents -p -t "$OLDPWD/bundled-licenses" {} +
)

%check
%{buildroot}%{_bindir}/ghostty --version | grep -Fx 'Ghostty %{version}'
desktop-file-validate %{buildroot}%{_datadir}/applications/%{app_id}.desktop
infocmp -A %{buildroot}%{_datadir}/terminfo xterm-ghostty >/dev/null

%files -f %{app_id}.lang
%license LICENSE src/font/res/OFL.txt bundled-licenses/
%{_bindir}/ghostty
%{_userunitdir}/app-%{app_id}.service
%{_datadir}/applications/%{app_id}.desktop
%{_datadir}/bat/syntaxes/ghostty.sublime-syntax
%{_datadir}/dbus-1/services/%{app_id}.service
%{_datadir}/ghostty/
%{_datadir}/icons/hicolor/*/apps/%{app_id}.png
%{_datadir}/kio/servicemenus/%{app_id}.desktop
%{_datadir}/metainfo/%{app_id}.metainfo.xml
%{_datadir}/nautilus-python/extensions/ghostty.py
%{_datadir}/nvim/site/*/ghostty.vim
%{_datadir}/terminfo/x/xterm-ghostty
%{_datadir}/vim/vimfiles/*/ghostty.vim
%{bash_completions_dir}/ghostty.bash
%{fish_completions_dir}/ghostty.fish
%{zsh_completions_dir}/_ghostty
%{_mandir}/man1/ghostty.1*
%{_mandir}/man5/ghostty.5*

%changelog
%autochangelog
