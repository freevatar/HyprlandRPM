%global upstream_version 0.55.4
%global snapshot 69

%global lua54_compat 0
%if 0%{?fedora} && 0%{?fedora} < 45
%global lua54_compat 1
%endif

%global hyprland_commit 466f6bc53f44c42fd7d8f8c01eeaec112112aefd
%global hyprland_shortcommit %(c=%{hyprland_commit}; echo ${c:0:7})
%global hyprland_commits 7617
%global hyprland_commit_date Fri Jul 17 17:54:38 2026
%global hyprland_commit_message protocols/presentation: associate feedback with committed surface state, attempt 2

%global protocols_commit bd153e76f751f150a09328dbdeb5e4fab9d23622
%global protocols_shortcommit %(c=%{protocols_commit}; echo ${c:0:7})

%global udis86_commit 5336633af70f3917760a6d441ff02d93477b0c86
%global udis86_shortcommit %(c=%{udis86_commit}; echo ${c:0:7})

Name:           hyprland-git
Version:        %{upstream_version}^%{snapshot}.git%{hyprland_shortcommit}
Release:        %autorelease
Summary:        Dynamic tiling Wayland compositor that doesn't sacrifice on its looks

# Hyprland: BSD-3-Clause
# subprojects/hyprland-protocols: BSD-3-Clause
# subprojects/udis86: BSD-2-Clause
# protocols/ext-workspace-unstable-v1.xml: HPND-sell-variant
# protocols/wlr-foreign-toplevel-management-unstable-v1.xml: HPND-sell-variant
# protocols/wlr-layer-shell-unstable-v1.xml: HPND-sell-variant
# protocols/idle.xml: LGPL-2.1-or-later
License:        BSD-3-Clause AND BSD-2-Clause AND HPND-sell-variant AND LGPL-2.1-or-later
URL:            https://github.com/hyprwm/Hyprland
Source0:        %{url}/archive/%{hyprland_commit}/%{name}-%{hyprland_shortcommit}.tar.gz
Source1:        https://github.com/hyprwm/hyprland-protocols/archive/%{protocols_commit}/protocols-%{protocols_shortcommit}.tar.gz
Source2:        https://github.com/canihavesomecoffee/udis86/archive/%{udis86_commit}/udis86-%{udis86_shortcommit}.tar.gz
Source3:        macros.hyprland

# Build tools
BuildRequires:  cmake >= 3.30
BuildRequires:  gcc-c++
BuildRequires:  glaze-static
BuildRequires:  ninja-build
BuildRequires:  python3

# Hypr ecosystem
BuildRequires:  pkgconfig(aquamarine) >= 0.9.3
BuildRequires:  pkgconfig(hyprcursor) >= 0.1.7
BuildRequires:  pkgconfig(hyprgraphics) >= 0.5.1
BuildRequires:  pkgconfig(hyprlang) >= 0.6.7
BuildRequires:  pkgconfig(hyprutils) >= 0.13.1
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
BuildRequires:  pkgconfig(libdrm)
BuildRequires:  pkgconfig(libeis-1.0)
BuildRequires:  pkgconfig(libinput) >= 1.29
BuildRequires:  pkgconfig(pango)
BuildRequires:  pkgconfig(pangocairo)
BuildRequires:  pkgconfig(pixman-1)
BuildRequires:  pkgconfig(re2)
BuildRequires:  pkgconfig(readline)
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
%if %{lua54_compat}
BuildRequires:  pkgconfig(lua)
%else
BuildRequires:  pkgconfig(lua) >= 5.5
%endif

# Fedora's udis86 package is a different implementation from the modified
# canihavesomecoffee fork bundled by Hyprland.
Provides:       bundled(udis86) = 1.7.2^1.%{udis86_shortcommit}

Requires:       aquamarine%{?_isa} >= 0.9.3
Requires:       hyprcursor%{?_isa} >= 0.1.7
Requires:       hyprgraphics%{?_isa} >= 0.5.1
Requires:       hyprlang%{?_isa} >= 0.6.7
Requires:       hyprutils%{?_isa} >= 0.13.1
Requires:       xorg-x11-server-Xwayland%{?_isa}

# Used by the default configuration.
Recommends:     kitty
Recommends:     hyprland-qtutils

# Logind needs polkit to create a graphical session.
Recommends:     polkit

# Recommended systemd-managed startup method.
Recommends:     %{name}-uwsm

Recommends:     (qt5-qtwayland if qt5-qtbase-gui)
Recommends:     (qt6-qtwayland if qt6-qtbase-gui)

%description
Hyprland is a dynamic tiling Wayland compositor that doesnэt sacrifice on its looks.
It supports multiple layouts, visual effects, flexible IPC, a powerful plugin system and extensive customization.

%package uwsm
Summary:        Files for a uwsm-managed Hyprland session
BuildArch:      noarch
Requires:       %{name} = %{version}-%{release}
Requires:       uwsm

%description uwsm
Desktop-session files for starting Hyprland through the Universal Wayland
Session Manager.

%package devel
Summary:        Development files for %{name}
License:        BSD-3-Clause
Requires:       %{name}%{?_isa} = %{version}-%{release}
Requires:       pkgconfig(xkbcommon) >= 1.11.0

%description devel
Headers, generated protocol files, pkg-config metadata and RPM macros for
building software and plugins against %{name}.

%prep
%autosetup -n Hyprland-%{hyprland_commit} -N

# Replace the empty Git submodule directories with the exact pinned sources.
rm -rf subprojects/hyprland-protocols subprojects/udis86
mkdir -p subprojects/hyprland-protocols subprojects/udis86

tar -xf %{SOURCE1} -C subprojects/hyprland-protocols --strip-components=1
tar -xf %{SOURCE2} -C subprojects/udis86 --strip-components=1

# Always use the pinned bundled subprojects, even when similarly named system
# packages happen to enter the buildroot through another dependency.
sed -Ei \
    -e 's|^pkg_check_modules\(udis_dep .*$|set(udis_dep_FOUND FALSE)|' \
    -e 's|^  find_library\(udis_nopc udis86\)$|  set(udis_nopc "udis_nopc-NOTFOUND")|' \
    -e 's|^pkg_check_modules\(hyprland_protocols_dep .*$|set(hyprland_protocols_dep_FOUND FALSE)|' \
    CMakeLists.txt

grep -Fxq 'set(udis_dep_FOUND FALSE)' CMakeLists.txt
grep -Fxq '  set(udis_nopc "udis_nopc-NOTFOUND")' CMakeLists.txt
grep -Fxq 'set(hyprland_protocols_dep_FOUND FALSE)' CMakeLists.txt

# Temporary compatibility for Fedora releases that still provide Lua 5.4
# Drop this branch once all supported Fedora releases provide Lua 5.5
%if %{lua54_compat}
sed -Ei \
    's|^pkg_search_module\(LUA .*$|pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL lua)|' \
    CMakeLists.txt

grep -Fxq \
    'pkg_search_module(LUA REQUIRED IMPORTED_TARGET GLOBAL lua)' \
    CMakeLists.txt
%endif

cp -p subprojects/hyprland-protocols/LICENSE LICENSE-hyprland-protocols
cp -p subprojects/udis86/LICENSE LICENSE-udis86

# Generate the packaged RPM macro without modifying the source in %{_sourcedir}.
cp -p %{SOURCE3} macros.hyprland
sed -i 's|@@HYPRLAND_VERSION@@|%{version}|g' macros.hyprland

%build
export GIT_COMMIT_HASH='%{hyprland_commit}'
export GIT_BRANCH='main'
export GIT_COMMIT_MESSAGE='%{hyprland_commit_message}'
export GIT_COMMIT_DATE='%{hyprland_commit_date}'
export GIT_DIRTY='clean'
export GIT_TAG='unknown'
export GIT_COMMITS='%{hyprland_commits}'

%cmake \
    -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_TESTING=OFF \
    -DFETCHCONTENT_FULLY_DISCONNECTED=ON

%cmake_build

%install
%cmake_install

install -Dpm0644 macros.hyprland \
    %{buildroot}%{_rpmconfigdir}/macros.d/macros.hyprland

%files
%license LICENSE LICENSE-hyprland-protocols LICENSE-udis86
%{_bindir}/Hyprland
%{_bindir}/hyprland
%{_bindir}/hyprctl
%{_bindir}/hyprpm
%{_bindir}/start-hyprland
%{_datadir}/hypr/
%{_datadir}/wayland-sessions/hyprland.desktop
%{_datadir}/xdg-desktop-portal/hyprland-portals.conf
%{_mandir}/man1/Hyprland.1*
%{_mandir}/man1/hyprctl.1*
%{bash_completions_dir}/hypr*
%{fish_completions_dir}/hypr*.fish
%{zsh_completions_dir}/_hypr*

%files uwsm
%{_datadir}/wayland-sessions/hyprland-uwsm.desktop

%files devel
%{_includedir}/hyprland/
%{_datadir}/pkgconfig/hyprland.pc
%{_rpmconfigdir}/macros.d/macros.hyprland

%changelog
%autochangelog