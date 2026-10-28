%global upstream_version 0.57.0
%global snapshot 1

%global lua54_compat 0
%global lua_min_version 5.5
%global lua_next_version 5.6
%if 0%{?fedora} && 0%{?fedora} < 45
%global lua54_compat 1
%global lua_min_version 5.4
%global lua_next_version 5.5
%endif

%global hyprland_commit 0aa6745f705f2c4b0a5004eb019ef76e5bbdb426
%global hyprland_shortcommit %(c=%{hyprland_commit}; echo ${c:0:7})
%global hyprland_commits 7978
%global hyprland_commit_date Wed Oct 28 06:37:05 2026
%global hyprland_commit_message_b64 VkVSU0lPTjogYnVtcCB0byAwLjU3LjA=

%global udis86_commit 5336633af70f3917760a6d441ff02d93477b0c86
%global udis86_shortcommit %(c=%{udis86_commit}; echo ${c:0:7})

Name:           hyprland-git
Version:        %{upstream_version}^%{snapshot}.git%{hyprland_shortcommit}
Release:        %autorelease -b2
Summary:        Dynamic tiling Wayland compositor that doesn't sacrifice on its looks

# Hyprland: BSD-3-Clause
# hyprland-protocols: BSD-3-Clause
# subprojects/udis86: BSD-2-Clause
# protocols/ext-workspace-unstable-v1.xml: HPND-sell-variant
# protocols/wlr-foreign-toplevel-management-unstable-v1.xml: HPND-sell-variant
# protocols/wlr-layer-shell-unstable-v1.xml: HPND-sell-variant
# protocols/idle.xml: LGPL-2.1-or-later
License:        BSD-3-Clause AND BSD-2-Clause AND HPND-sell-variant AND LGPL-2.1-or-later
URL:            https://github.com/hyprwm/Hyprland
Source0:        %{url}/archive/%{hyprland_commit}/%{name}-%{hyprland_shortcommit}.tar.gz
Source2:        https://github.com/canihavesomecoffee/udis86/archive/%{udis86_commit}/udis86-%{udis86_shortcommit}.tar.gz
Source3:        macros.hyprland
Patch0:         escape-version-metadata.patch
Patch1:         lua54-hyprpm.patch

# Build tools
BuildRequires:  cmake >= 3.30
BuildRequires:  gcc-c++
BuildRequires:  glaze-static
BuildRequires:  ninja-build
BuildRequires:  python3
BuildRequires:  systemd-rpm-macros

# Hypr ecosystem
BuildRequires:  pkgconfig(aquamarine) >= 0.15.0
BuildRequires:  pkgconfig(hyprcursor) >= 0.1.7
BuildRequires:  pkgconfig(hyprgraphics) >= 0.5.1
BuildRequires:  pkgconfig(hyprland-protocols) >= 0.8.0
BuildRequires:  pkgconfig(hyprlang) >= 0.6.7
BuildRequires:  pkgconfig(hyprutils) >= 0.14.3
BuildRequires:  pkgconfig(hyprwayland-scanner) >= 0.3.10
BuildRequires:  pkgconfig(hyprwire)

# Core compositor and utility dependencies
BuildRequires:  muParser-devel
BuildRequires:  pkgconfig(cairo)
BuildRequires:  pkgconfig(egl)
BuildRequires:  pkgconfig(gbm)
BuildRequires:  pkgconfig(gio-2.0)
BuildRequires:  pkgconfig(glesv2)
BuildRequires:  pkgconfig(glslang)
BuildRequires:  pkgconfig(lcms2)
BuildRequires:  pkgconfig(libcanberra)
BuildRequires:  pkgconfig(libdrm)
BuildRequires:  pkgconfig(libeis-1.0)
BuildRequires:  pkgconfig(libinput) >= 1.29
BuildRequires:  pkgconfig(pango)
BuildRequires:  pkgconfig(pangocairo)
BuildRequires:  pkgconfig(pixman-1)
BuildRequires:  pkgconfig(re2)
BuildRequires:  pkgconfig(readline)
BuildRequires:  pkgconfig(sdbus-c++) >= 2.0.0
BuildRequires:  pkgconfig(tomlplusplus)
BuildRequires:  pkgconfig(uuid)
BuildRequires:  pkgconfig(wayland-protocols) >= 1.49
BuildRequires:  pkgconfig(wayland-scanner)
BuildRequires:  pkgconfig(wayland-server) >= 1.22.91
BuildRequires:  pkgconfig(xcursor)
BuildRequires:  pkgconfig(xkbcommon) >= 1.11.0

# XWayland support
BuildRequires:  pkgconfig(xcb)
BuildRequires:  pkgconfig(xcb-composite)
BuildRequires:  pkgconfig(xcb-errors)
BuildRequires:  pkgconfig(xcb-icccm)
BuildRequires:  pkgconfig(xcb-render)
BuildRequires:  pkgconfig(xcb-res)
BuildRequires:  pkgconfig(xcb-xfixes)

# Fedora 44 and older provide Lua 5.4. Fedora 45 and newer provide Lua 5.5
BuildRequires:  pkgconfig(lua) >= %{lua_min_version}
BuildRequires:  pkgconfig(lua) < %{lua_next_version}

# Fedora's udis86 package is a different implementation from the modified
# canihavesomecoffee fork bundled by Hyprland.
Provides:       bundled(udis86) = 1.7.2^1.%{udis86_shortcommit}

# Satisfy Hyprland dependencies while keeping both variants mutually exclusive.
Provides:       hyprland = %{version}-%{release}
Provides:       hyprland%{?_isa} = %{version}-%{release}
Conflicts:      hyprland

# Retire the session subpackage so its exact-version dependency cannot block upgrades.
Obsoletes:      hyprland-git-uwsm < 0.57.0^1.git0aa6745-2

Requires:       aquamarine%{?_isa} >= 0.15.0
Requires:       hyprcursor%{?_isa} >= 0.1.7
Requires:       hyprgraphics%{?_isa} >= 0.5.1
Requires:       hyprlang%{?_isa} >= 0.6.7
Requires:       hyprutils%{?_isa} >= 0.14.3
Requires:       xorg-x11-server-Xwayland%{?_isa}

# Used by the default configuration.
Recommends:     kitty
Recommends:     hyprland-guiutils

# Logind needs polkit to create a graphical session.
Recommends:     polkit

Recommends:     (qt5-qtwayland if qt5-qtbase-gui)
Recommends:     (qt6-qtwayland if qt6-qtbase-gui)

%description
Hyprland is a dynamic tiling Wayland compositor that doesn't sacrifice on its looks.
It supports multiple layouts, visual effects, flexible IPC, a powerful plugin system, and extensive customization.

%package devel
Summary:        Development files for %{name}
License:        BSD-3-Clause
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       pkgconfig(xkbcommon) >= 1.11.0
Requires:       pkgconfig(lua) >= %{lua_min_version}
Requires:       pkgconfig(lua) < %{lua_next_version}
Requires:       pkgconfig(libeis-1.0)
Provides:       hyprland-devel = %{version}-%{release}
Provides:       hyprland-devel%{?_isa} = %{version}-%{release}
Conflicts:      hyprland-devel

%description devel
Headers, generated protocol files, pkg-config metadata, and RPM macros for
building software and plugins against %{name}.

%prep
%autosetup -n Hyprland-%{hyprland_commit} -N
%patch -P 0 -p1

# Replace the empty Git submodule directory with the exact pinned source.
rm -rf subprojects/udis86
mkdir -p subprojects/udis86

tar -xf %{SOURCE2} -C subprojects/udis86 --strip-components=1

# Use the pinned udis86 fork even if a system implementation is installed.
sed -Ei \
    -e 's|^pkg_check_modules\(udis_dep .*$|set(udis_dep_FOUND FALSE)|' \
    -e 's|^  find_library\(udis_nopc udis86\)$|  set(udis_nopc "udis_nopc-NOTFOUND")|' \
    CMakeLists.txt

grep -Fxq 'set(udis_dep_FOUND FALSE)' CMakeLists.txt
grep -Fxq '  set(udis_nopc "udis_nopc-NOTFOUND")' CMakeLists.txt

# Temporary compatibility for Fedora releases that still provide Lua 5.4
# Drop this branch once all supported Fedora releases provide Lua 5.5
%if %{lua54_compat}
%patch -P 1 -p1
sed -Ei \
    's|^pkg_search_module\(LUA .*$|pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL lua)|' \
    CMakeLists.txt

grep -Fxq \
    'pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL lua)' \
    CMakeLists.txt
%endif

# Export dependencies included by the installed Lua and input headers.
sed -i '/^Requires:/s/$/, lua >= %{lua_min_version}, lua < %{lua_next_version}, libeis-1.0/' hyprland.pc.in

cp -p subprojects/udis86/LICENSE LICENSE-udis86

# Generate the packaged RPM macro without modifying the source in %{_sourcedir}.
cp -p %{SOURCE3} macros.hyprland
sed -i 's|@@HYPRLAND_VERSION@@|%{version}|g' macros.hyprland

%build
# Decode after RPM expansion; the CMake patch escapes the resulting C++ string.
export GIT_COMMIT_HASH=%{shescape:%{hyprland_commit}}
export GIT_BRANCH=%{shescape:main}
export GIT_COMMIT_DATE=%{shescape:%{hyprland_commit_date}}
export GIT_DIRTY=%{shescape:clean}
export GIT_TAG=%{shescape:unknown}
export GIT_COMMITS=%{shescape:%{hyprland_commits}}

GIT_COMMIT_MESSAGE="$(
    printf '%%s' %{shescape:%{hyprland_commit_message_b64}} | base64 --decode
)"
export GIT_COMMIT_MESSAGE

%cmake \
    -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    -DNO_UWSM=ON \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON

%cmake_build

%install
%cmake_install

# Systemd user units belong in lib even when libraries are installed in lib64.
if [ "%{_libdir}/systemd/user" != "%{_userunitdir}" ]; then
    install -d %{buildroot}%{_userunitdir}
    mv %{buildroot}%{_libdir}/systemd/user/hyprland-session.target \
        %{buildroot}%{_userunitdir}/hyprland-session.target
fi

install -Dpm0644 macros.hyprland \
    %{buildroot}%{_rpmconfigdir}/macros.d/macros.hyprland

%files
%license LICENSE LICENSE-udis86
%{_bindir}/Hyprland
%{_bindir}/hyprland
%{_bindir}/hyprctl
%{_bindir}/hyprpm
%{_bindir}/start-hyprland
%{_datadir}/hypr/
%{_datadir}/wayland-sessions/hyprland.desktop
%{_datadir}/xdg-desktop-portal/hyprland-portals.conf
%{_userunitdir}/hyprland-session.target
%{_mandir}/man1/Hyprland.1*
%{_mandir}/man1/hyprctl.1*
%{bash_completions_dir}/hypr*
%{fish_completions_dir}/hypr*.fish
%{zsh_completions_dir}/_hypr*

%files devel
%{_includedir}/hyprland/
%{_datadir}/pkgconfig/hyprland.pc
%{_rpmconfigdir}/macros.d/macros.hyprland

%changelog
%autochangelog
